from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, List, Optional, Set
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from api_logger import (
    log_analysis_complete,
    log_data_processing,
    log_error,
    log_expert_analysis,
)
from reopsai_backend.application.plan_generation_service import plan_generation_service
from reopsai_backend.application.workspace_service import workspace_service
from reopsai_backend.application.keywords import _clean_metadata_text, fetch_project_keywords


@dataclass(frozen=True)
class WorkspaceAiResult:
    status: str
    data: Any = None
    error: str | None = None


class WorkspaceAiService:
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        *,
        openai_adapter=_DEFAULT_ADAPTER,
        project_keyword_fetcher=_DEFAULT_ADAPTER,
        plan_generation_adapter=None,
        workspace_record_service=None,
    ):
        if openai_adapter is self._DEFAULT_ADAPTER:
            from reopsai_backend.infrastructure.llm import get_openai_service

            openai_adapter = get_openai_service()
        if project_keyword_fetcher is self._DEFAULT_ADAPTER:
            project_keyword_fetcher = fetch_project_keywords

        self.openai_adapter = openai_adapter
        self.project_keyword_fetcher = project_keyword_fetcher
        self.plan_generation_adapter = plan_generation_adapter or plan_generation_service
        self.workspace_record_service = workspace_record_service or workspace_service

    def generate_project_name(self, *, study_name: str, problem_definition: str) -> WorkspaceAiResult:
        prompt = f"""
다음 연구 제목과 문제 정의에서 핵심 키워드를 추출하여 프로젝트명과 관련 태그를 생성해주세요.

연구 제목: {study_name if study_name else '(없음)'}
문제 정의: {problem_definition if problem_definition else '(없음)'}

응답 형식 (JSON만):
{{
  "projectName": "서비스명 또는 브랜드명",
  "tags": ["태그1", "태그2", "태그3"]
}}

규칙:
- 프로젝트명은 최대한 브랜드/서비스명을 포함하되, 유추할 수 없다면 도메인/비즈니스 수준의 단어로 선택
- 태그는 3-5개 생성 (도메인, 산업, 비즈니스 유형 등)
- 태그는 간결하고 명확하게
- 각 결과물 앞에 띄어쓰기나, - 와 같은 불필요한 부분이 포함되지않도록 처리해주세요.

예시:
{{"projectName": "KB증권 MTS M-able", "tags": ["금융", "증권", "모바일앱", "MTS"]}}

답변:"""

        result = self.openai_adapter.generate_response(
            prompt,
            generation_config={'temperature': 0.3, 'max_output_tokens': 200},
        )
        if not result.get('success'):
            return WorkspaceAiResult("llm_failed", error=result.get('error', '프로젝트명 생성 실패'))

        project_name, tags = self._parse_project_name_response(result.get('content', '').strip())
        return WorkspaceAiResult("ok", {"projectName": project_name, "tags": tags})

    @staticmethod
    def _parse_project_name_response(content: str):
        try:
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                project_name = data.get('projectName', '').strip()
                tags = data.get('tags', [])
                if not project_name:
                    project_name = WorkspaceAiService._fallback_name_from_text(content)
                    tags = []
            else:
                project_name = WorkspaceAiService._fallback_name_from_text(content)
                tags = []
        except Exception as exc:
            print(f"[ERROR] JSON 파싱 실패: {exc}, content: {content}")
            project_name = WorkspaceAiService._fallback_name_from_text(content)
            tags = []

        project_name = re.sub(r'^\d+\.\s*', '', project_name)
        return project_name, tags

    @staticmethod
    def _fallback_name_from_text(content: str) -> str:
        name = content.strip().replace('"', '').strip()
        if ':' in name:
            name = name.split(':')[-1].strip()
        return name

    def generate_study_name(self, *, problem_definition: str) -> WorkspaceAiResult:
        prompt = f"""
다음 문제 정의를 바탕으로 적절한 연구명을 하나만 생성해주세요.

문제 정의:
{problem_definition}

연구명 규칙:
- 명확하고 구체적인 연구명 하나만 작성
- 문제를 잘 나타내야 함
- "연구", "조사", "분석" 같은 단어는 제외
- 10-20자 이내
- 설명 없이 연구명만 출력

답변 형식: 연구명만 출력 (추가 설명, 예시, 목록 없이)"""

        result = self.openai_adapter.generate_response(
            prompt,
            generation_config={'temperature': 0.4, 'max_output_tokens': 100},
        )
        if not result.get('success'):
            return WorkspaceAiResult("llm_failed", error=result.get('error', '연구명 생성 실패'))

        study_name = self._clean_study_name(result.get('content', '').strip())
        return WorkspaceAiResult("ok", {"studyName": study_name})

    @staticmethod
    def _clean_study_name(content: str) -> str:
        lines = content.split('\n')
        study_name = lines[0].strip()
        study_name = study_name.replace('"', '').replace('*', '').replace('-', '').strip()
        if ':' in study_name:
            study_name = study_name.split(':')[-1].strip()
        return re.sub(r'^\d+\.\s*', '', study_name)

    def stream_tags(self, *, project_title: str, product_url: str):
        url_context = self.build_url_analysis_context(product_url) if product_url else None

        context_sections: List[str] = []
        if url_context:
            context_sections.append(f"서비스 URL 분석 결과:\n{url_context}")
        elif product_url:
            context_sections.append(f"서비스 URL/도메인: {product_url}")

        if project_title:
            context_sections.append(f"프로젝트 이름: {project_title}")

        context = "\n\n".join(section for section in context_sections if section).strip()
        prompt = f"""
{context}

지침:
- URL의 메타데이터가 제공되면 해당 정보를 우선으로 반영하세요.
- 프로젝트 이름만 제공되더라도 기업/브랜드, 산업군, 서비스 유형, 주요 기능, 타깃 사용자 등을 적극적으로 유추하여 7개 내외의 태그를 작성하세요.
- 태그는 쉼표로 구분하고, 각 태그는 2~4 단어 이내로 간결하게 작성합니다.
- 회사명 또는 브랜드명은 최대 1개만 포함하고, 나머지는 산업/서비스/기능/고객 관점의 태그로 구성하세요.
- 숫자, 기호, 불필요한 접미사는 제거하고, 한글 또는 널리 쓰이는 영문 약어를 사용합니다.
- 중복되거나 지나치게 일반적인 단어(예: 서비스, 플랫폼)는 피하세요.
- 가능한 경우 {project_title}의 서비스 유형을 추론하여 구체적인 산업/사용 시나리오 태그를 추가하세요.
"""

        result = self.openai_adapter.generate_response(prompt, {"temperature": 0.3})
        if not result.get('success'):
            error_data = {'error': result.get('error', '생성 실패'), 'done': True}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            return

        accumulated_text = result.get('content') or ''
        tags = [tag.strip() for tag in accumulated_text.split(',') if tag.strip()]
        current_tags = []
        for tag in tags[:8]:
            current_tags.append(tag)
            yield f"data: {json.dumps({'tags': current_tags[:8]}, ensure_ascii=False)}\n\n"

        final_tags = tags[:8]
        yield f"data: {json.dumps({'tags': final_tags, 'done': True}, ensure_ascii=False)}\n\n"

    def regenerate_plan_background(self, *, artifact_id: int, study_id: int, project_id: int, form_data: dict) -> WorkspaceAiResult:
        project_keywords = self.project_keyword_fetcher(project_id)
        try:
            log_expert_analysis("백그라운드 계획서 재생성", f"시작: artifact_id={artifact_id}, study_id={study_id}")
            response = self.plan_generation_adapter.generate_oneshot_parallel_experts(form_data, project_keywords)
            if response.get('success'):
                updated = self.workspace_record_service.complete_artifact(
                    artifact_id=artifact_id,
                    content=response.get('final_plan', ''),
                )
                if not updated:
                    return WorkspaceAiResult("not_found")
                log_analysis_complete()
                log_data_processing(
                    "계획서 재생성 완료",
                    {"artifact_id": artifact_id, "study_id": study_id},
                    "백그라운드 계획서 재생성 성공",
                )
                return WorkspaceAiResult("ok")

            self.workspace_record_service.delete_artifact_by_id(artifact_id=artifact_id)
            return WorkspaceAiResult("failed", error=response.get("error"))
        except Exception as exc:
            log_error(exc, f"백그라운드 계획서 재생성 오류: artifact_id={artifact_id}, study_id={study_id}")
            try:
                self.workspace_record_service.delete_artifact_by_id(artifact_id=artifact_id)
            except Exception as delete_error:
                log_error(delete_error, f"재생성 오류 후 artifact 삭제 실패: artifact_id={artifact_id}")
            return WorkspaceAiResult("failed", error=str(exc))

    @staticmethod
    def build_url_analysis_context(product_url: str) -> Optional[str]:
        if not product_url:
            return None
        normalized_url = product_url.strip()
        if not normalized_url:
            return None

        if not normalized_url.startswith(("http://", "https://")):
            normalized_url = f"https://{normalized_url}"

        try:
            response = requests.get(
                normalized_url,
                timeout=6,
                allow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; SmartResearchManager/1.0; "
                        "+https://smart-research-manager.local)"
                    )
                },
            )
        except Exception as exc:
            print(f"[URL 분석] 요청 실패 ({normalized_url}): {exc}")
            return None

        if response.status_code >= 400 or not response.text:
            print(f"[URL 분석] 응답 코드 {response.status_code} ({normalized_url})")
            return None

        final_url = response.url or normalized_url
        soup = BeautifulSoup(response.text, "html.parser")
        parsed = urlparse(final_url)
        domain = parsed.netloc

        def get_meta_by(attr_name: str, attr_value: str) -> Optional[str]:
            tag = soup.find('meta', attrs={attr_name: attr_value})
            if tag:
                return _clean_metadata_text(tag.get('content'))
            return None

        title_candidates: List[str] = []
        if soup.title and soup.title.string:
            title_text = _clean_metadata_text(soup.title.string)
            if title_text:
                title_candidates.append(title_text)

        og_title = get_meta_by('property', 'og:title')
        if og_title and og_title not in title_candidates:
            title_candidates.append(og_title)

        site_name = get_meta_by('property', 'og:site_name')

        description_candidates: List[str] = []
        meta_description = get_meta_by('name', 'description')
        if meta_description:
            description_candidates.append(meta_description)

        og_description = get_meta_by('property', 'og:description')
        if og_description and og_description not in description_candidates:
            description_candidates.append(og_description)

        keywords: List[str] = []
        keywords_lower: Set[str] = set()
        keywords_tag = soup.find('meta', attrs={'name': 'keywords'}) or soup.find('meta', attrs={'property': 'keywords'})
        if keywords_tag and keywords_tag.get('content'):
            for keyword in keywords_tag.get('content').split(','):
                cleaned = _clean_metadata_text(keyword, max_len=60)
                if cleaned:
                    lowered = cleaned.lower()
                    if lowered not in keywords_lower:
                        keywords.append(cleaned)
                        keywords_lower.add(lowered)

        heading_text: Optional[str] = None
        for heading_tag in ('h1', 'h2'):
            heading = soup.find(heading_tag)
            if heading and heading.get_text():
                cleaned_heading = _clean_metadata_text(heading.get_text())
                if cleaned_heading:
                    heading_text = cleaned_heading
                    break

        info_lines: List[str] = []
        if domain:
            info_lines.append(f"도메인: {domain}")
        info_lines.append(f"최종 URL: {final_url}")
        if site_name:
            info_lines.append(f"서비스 명: {site_name}")
        if title_candidates:
            info_lines.append(f"페이지 타이틀: {title_candidates[0]}")
        if heading_text:
            info_lines.append(f"주요 헤더: {heading_text}")
        if description_candidates:
            info_lines.append(f"설명: {description_candidates[0]}")
        if keywords:
            info_lines.append("태그 후보 키워드: " + ", ".join(keywords[:8]))

        context_block = "\n".join(line for line in info_lines if line)
        return context_block or None


workspace_ai_service = WorkspaceAiService()
