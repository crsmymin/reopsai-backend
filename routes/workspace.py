"""
워크스페이스 Blueprint - 프로젝트/스터디/아티팩트 CRUD.

app.py에서 분리됨. URL prefix: /api
"""
import re
import threading
import traceback
from typing import List, Optional, Set

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, Response, jsonify, request
from urllib.parse import urlparse

from api_logger import log_error
from db.engine import session_scope
from db.repositories.workspace_repository import WorkspaceRepository
from reopsai_backend.application.workspace_service import workspace_service
from reopsai_backend.shared.auth import tier_required
from services.openai_service import openai_service
from utils.keyword_utils import (
    _clean_metadata_text, fetch_project_keywords,
)
from utils.request_utils import _extract_request_user_id, _resolve_workspace_owner_ids
from utils.usage_metering import build_llm_usage_context, run_with_llm_usage_context, stream_with_llm_usage_context
from api_logger import (
    log_analysis_complete, log_data_processing, log_error,
    log_expert_analysis,
)

workspace_bp = Blueprint('workspace', __name__, url_prefix='/api')


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_url_analysis_context(product_url: str) -> Optional[str]:
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


# ---------------------------------------------------------------------------
# 워크스페이스 엔드포인트
# ---------------------------------------------------------------------------

@workspace_bp.route('/workspace/projects', methods=['GET'])
@tier_required(['free'])
def workspace_get_projects():
    """
    [GET] 현재 사용자의 모든 프로젝트 조회
    - SQLAlchemy 'projects' 테이블에서 owner_id로 필터링
    - 최신순 정렬 (created_at DESC)
    """
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        projects = workspace_service.list_projects(owner_ids)
        return jsonify({'success': True, 'projects': projects})
    except Exception as e:
        log_error(e, "프로젝트 목록 조회")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/projects-with-studies', methods=['GET'])
@tier_required(['free'])
def workspace_get_projects_with_studies():
    """
    [GET] 현재 사용자의 모든 프로젝트와 각 프로젝트의 스터디를 한 번에 조회
    - 프로젝트와 스터디를 통합하여 반환 (N+1 쿼리 문제 해결)
    - 권한 체크를 한 번만 수행
    """
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)

        summary = workspace_service.get_workspace_summary(owner_ids)

        return jsonify({
            'success': True,
            'projects': summary.projects,
            'all_studies': summary.all_studies,
            'recent_artifacts': summary.recent_artifacts
        })
    except Exception as e:
        log_error(e, "프로젝트+스터디 목록 조회")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/projects', methods=['POST'])
@tier_required(['free'])
def workspace_create_project():
    """
    [POST] 새 프로젝트 생성
    - SQLAlchemy 'projects' 테이블에 저장
    - 필수: name
    - 선택: product_url, keywords (배열)
    - description은 사용 안 함 (UI에서 제거됨)
    """
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        data = request.json or {}
        name = data.get('name')
        tags = data.get('tags', [])
        product_url = data.get('productUrl', '')

        if not name:
            return jsonify({'success': False, 'error': '프로젝트 이름은 필수입니다.'}), 400

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        created_project = workspace_service.create_project(
            owner_id=int(user_id_int),
            name=name,
            product_url=product_url,
            tags=tags,
        )
        return jsonify({'success': True, 'project': created_project})
    except Exception as e:
        log_error(e, "프로젝트 생성")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/projects/<int:project_id>', methods=['DELETE'])
@tier_required(['free'])
def workspace_delete_project(project_id):
    """
    [DELETE] 프로젝트 삭제
    - SQLAlchemy에서 해당 프로젝트 삭제
    - 관련된 studies도 CASCADE로 자동 삭제 (DB 설정 필요)
    """
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        workspace_service.delete_project(project_id=project_id, owner_id=int(user_id_int))
        return jsonify({'success': True, 'message': f'프로젝트 {project_id} 삭제 완료'})
    except Exception as e:
        log_error(e, f"프로젝트 {project_id} 삭제")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/projects/<int:project_id>', methods=['PUT'])
@tier_required(['free'])
def workspace_update_project(project_id):
    """
    [PUT] 프로젝트 정보 수정
    - SQLAlchemy에서 프로젝트 정보 업데이트
    """
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        data = request.json or {}
        result = workspace_service.update_project(
            project_id=project_id,
            owner_id=int(user_id_int),
            data=data,
        )
        if result.status == "empty_update":
            return jsonify({'success': False, 'error': '업데이트할 데이터가 없습니다.'}), 400
        if result.status == "not_found":
            return jsonify({'success': False, 'error': '프로젝트를 찾을 수 없습니다.'}), 404
        return jsonify({'success': True, 'message': '프로젝트 정보가 업데이트되었습니다.', 'data': result.data})
    except Exception as e:
        log_error(e, f"프로젝트 {project_id} 업데이트")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/generate-project-name', methods=['POST'])
@tier_required(['free'])
def generate_project_name():
    """
    [POST] 프로젝트명 자동 생성
    - studyName과 problemDefinition을 기반으로 AI가 프로젝트명 생성
    - 프로젝트명과 관련 태그를 함께 생성하여 반환
    """
    try:
        data = request.json
        study_name = data.get('studyName', '')
        problem_definition = data.get('problemDefinition', '')

        if not study_name and not problem_definition:
            return jsonify({'success': False, 'error': '연구명 또는 문제 정의가 필요합니다.'}), 400

        # 프롬프트 생성
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

        result = openai_service.generate_response(
            prompt,
            generation_config={'temperature': 0.3, 'max_output_tokens': 200}
        )

        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', '프로젝트명 생성 실패')}), 500

        # JSON 파싱
        content = result.get('content', '').strip()
        import json as json_lib

        try:
            # JSON 부분만 추출
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json_lib.loads(json_match.group())
                project_name = data.get('projectName', '').strip()
                tags = data.get('tags', [])

                # 유효성 검증
                if not project_name:
                    project_name = content.split('\n')[0].strip().replace('"', '').strip()
                    if ':' in project_name:
                        project_name = project_name.split(':')[-1].strip()
                    tags = []
            else:
                # JSON 파싱 실패 시 기본 처리
                project_name = content.strip().replace('"', '').strip()
                if ':' in project_name:
                    project_name = project_name.split(':')[-1].strip()
                tags = []
        except Exception as e:
            print(f"[ERROR] JSON 파싱 실패: {e}, content: {content}")
            # 기본 처리
            project_name = content.strip().replace('"', '').strip()
            if ':' in project_name:
                project_name = project_name.split(':')[-1].strip()
            tags = []

        # 숫자나 불필요한 문자 제거
        project_name = re.sub(r'^\d+\.\s*', '', project_name)

        return jsonify({'success': True, 'projectName': project_name, 'tags': tags})

    except Exception as e:
        log_error(e, "프로젝트명 생성")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/generate-study-name', methods=['POST'])
@tier_required(['free'])
def generate_study_name():
    """
    [POST] 연구명 자동 생성
    - problemDefinition을 기반으로 AI가 연구명 생성
    """
    try:
        data = request.json
        problem_definition = data.get('problemDefinition', '')

        if not problem_definition or len(problem_definition.strip()) < 10:
            return jsonify({'success': False, 'error': '문제 정의가 필요합니다.'}), 400

        # 프롬프트 생성
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

        result = openai_service.generate_response(
            prompt,
            generation_config={'temperature': 0.4, 'max_output_tokens': 100}
        )

        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', '연구명 생성 실패')}), 500

        content = result.get('content', '').strip()

        # 여러 줄이 있을 경우 첫 번째 줄만 추출
        lines = content.split('\n')
        study_name = lines[0].strip()

        # 불필요한 문자 제거
        study_name = study_name.replace('"', '').replace('*', '').replace('-', '').strip()

        # ": " 같은 구분자 제거
        if ':' in study_name:
            study_name = study_name.split(':')[-1].strip()

        # 숫자나 불필요한 문자 제거 (예: "1. ", "2. ")
        study_name = re.sub(r'^\d+\.\s*', '', study_name)

        return jsonify({'success': True, 'studyName': study_name})

    except Exception as e:
        log_error(e, "연구명 생성")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/workspace/generate-tags', methods=['POST'])
@tier_required(['free'])
def workspace_generate_tags():
    """
    [POST] 프로젝트 제목 기반 관련 태그 자동 생성
    - Gemini LLM 스트리밍 모드로 태그 실시간 생성
    - 프론트엔드에서 Server-Sent Events (SSE)로 수신
    - 쉼표 단위로 태그가 하나씩 추가되는 효과
    """
    try:
        import json as _json
        data = request.json or {}
        project_title = (data.get('project_title') or '').strip()
        product_url = (data.get('product_url') or '').strip()

        if len(project_title) < 2 and not product_url:
            return jsonify({'success': False, 'error': '프로젝트 제목 또는 URL이 필요합니다.'}), 400

        url_context = _build_url_analysis_context(product_url) if product_url else None

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

        llm_usage_context = build_llm_usage_context(feature_key="workspace_ai")

        # 스트리밍 응답 생성
        def generate_stream():
            result = openai_service.generate_response(
                prompt,
                {"temperature": 0.3}
            )

            if not result.get('success'):
                error_data = {'error': result.get('error', '생성 실패'), 'done': True}
                yield f"data: {_json.dumps(error_data, ensure_ascii=False)}\n\n"
                return

            accumulated_text = result.get('content') or ''
            tags = [tag.strip() for tag in accumulated_text.split(',') if tag.strip()]

            # 스트리밍 효과를 위해 태그를 하나씩 보내기
            current_tags = []
            for tag in tags[:8]:
                current_tags.append(tag)
                yield f"data: {_json.dumps({'tags': current_tags[:8]}, ensure_ascii=False)}\n\n"

            # 최종 완료 신호
            final_tags = tags[:8]
            yield f"data: {_json.dumps({'tags': final_tags, 'done': True}, ensure_ascii=False)}\n\n"

        return Response(
            stream_with_llm_usage_context(llm_usage_context, generate_stream()),
            mimetype='text/event-stream',
        )

    except Exception as e:
        log_error(e, "태그 생성 API 오류")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 스터디 / 프로젝트 / 아티팩트 CRUD
# ---------------------------------------------------------------------------

@workspace_bp.route('/studies/<int:study_id>', methods=['GET'])
@tier_required(['free'])
def get_study(study_id):
    """개별 연구 조회"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_service.get_study(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify(result.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/projects/<int:project_id>', methods=['GET'])
@tier_required(['free'])
def get_project(project_id):
    """개별 프로젝트 조회"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_service.get_project(project_id=project_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify(result.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/projects/<int:project_id>/studies', methods=['GET'])
@tier_required(['free'])
def get_project_studies(project_id):
    """프로젝트의 연구 목록 조회"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_service.list_project_studies(project_id=project_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify({'success': True, 'studies': result.data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/studies/<int:study_id>/schedule', methods=['GET'])
@tier_required(['free'])
def get_study_schedule(study_id):
    """연구의 일정 데이터 조회"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_service.get_study_schedule(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        schedule = result.data
        if schedule:
            return jsonify({'success': True, 'schedule': schedule})
        return jsonify({'success': False, 'schedule': None})
    except Exception as e:
        print(f"[ERROR] get_study_schedule 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/artifacts/<int:artifact_id>', methods=['PUT'])
@tier_required(['free'])
def update_artifact(artifact_id):
    """아티팩트 내용 업데이트"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        content = data.get('content', '')
        if not content.strip():
            return jsonify({'success': False, 'error': '내용이 필요합니다.'}), 400

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        updated = workspace_service.update_artifact_content(
            artifact_id=artifact_id,
            owner_id=int(user_id_int),
            content=content,
        )
        if updated:
            return jsonify({'success': True, 'message': '아티팩트가 업데이트되었습니다.'})
        return jsonify({'success': False, 'error': '아티팩트를 찾을 수 없거나 접근 권한이 없습니다.'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/studies/<int:study_id>/artifacts', methods=['GET'])
@tier_required(['free'])
def get_study_artifacts(study_id):
    """연구의 아티팩트 목록 조회"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_service.list_study_artifacts(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify({'success': True, 'artifacts': result.data})
    except Exception as e:
        print(f"[ERROR] get_study_artifacts 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/studies/<int:study_id>/survey/deployments', methods=['GET'])
@tier_required(['free'])
def get_study_survey_deployments(study_id):
    """연구의 설문 배포 이력 조회.

    현재 배포 이력 저장 모델이 없으므로, 접근 가능한 연구에 대해서는 빈 배열을
    반환한다. 실제 배포 저장소가 추가되면 이 엔드포인트의 응답 배열을 채운다.
    """
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_service.authorize_study(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403

        return jsonify({'success': True, 'deployments': []}), 200
    except Exception as e:
        print(f"[ERROR] get_study_survey_deployments 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/artifacts/<int:artifact_id>/stream', methods=['GET'])
@tier_required(['free'])
def stream_artifact_generation(artifact_id):
    """Artifact 생성 상태 실시간 스트리밍"""
    import time as _time
    import json as _json

    if not session_scope:
        return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

    # 사용자 ID 확인 (스트리밍 시작 전에)
    user_id_int, err_resp, err_code = _extract_request_user_id()
    if err_resp:
        return err_resp, err_code

    def generate():
        artifact = workspace_service.get_artifact_for_stream_start(
            artifact_id=artifact_id,
            owner_id=int(user_id_int),
        )
        if not artifact:
            yield f"data: {_json.dumps({'error': 'Artifact not found or access denied'})}\n\n"
            return

        # 이미 완료된 경우
        if artifact.get('status') == 'completed':
            yield f"data: {_json.dumps({'content': artifact.get('content'), 'done': True})}\n\n"
            return

        # pending 상태면 폴링하면서 content 스트리밍
        last_content = ''

        for i in range(180):  # 최대 3분
            _time.sleep(1)

            try:
                # artifact 다시 조회
                artifact = workspace_service.get_artifact_for_stream_poll(artifact_id=artifact_id)
                if artifact:
                    artifact_content = artifact.get('content')
                    artifact_status = artifact.get('status')
                    if artifact_content and artifact_content != last_content:
                        last_content = artifact_content
                        yield f"data: {_json.dumps({'content': artifact_content}, ensure_ascii=False)}\n\n"

                    if artifact_status == 'completed':
                        yield f"data: {_json.dumps({'done': True})}\n\n"
                        return

                    if artifact_status == 'failed':
                        yield f"data: {_json.dumps({'error': '생성 실패', 'done': True})}\n\n"
                        return
            except Exception as e:
                # 일시적인 리소스 오류는 무시 (EAGAIN)
                if 'temporarily unavailable' not in str(e):
                    print(f"[ERROR] 스트리밍 폴링 오류: {e}")
                continue

        # 타임아웃
        yield f"data: {_json.dumps({'error': '시간 초과', 'done': True})}\n\n"

    from flask import current_app
    return current_app.response_class(generate(), mimetype='text/event-stream')


@workspace_bp.route('/studies/<int:study_id>', methods=['DELETE'])
@tier_required(['free'])
def delete_study(study_id):
    """연구 삭제"""
    try:
        if not session_scope:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        result = workspace_service.delete_study(study_id=study_id, owner_id=int(user_id_int))
        if result.status == "not_found":
            return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403

        return jsonify({'success': True, 'message': '연구가 삭제되었습니다.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/artifacts/<int:artifact_id>', methods=['DELETE'])
@tier_required(['free'])
def delete_artifact(artifact_id):
    """아티팩트 삭제"""
    try:
        if not session_scope:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        deleted = workspace_service.delete_artifact(
            artifact_id=artifact_id,
            owner_id=int(user_id_int),
        )
        if not deleted:
            return jsonify({'success': False, 'error': '아티팩트를 찾을 수 없거나 삭제 권한이 없습니다.'}), 404

        return jsonify({'success': True, 'message': '아티팩트가 삭제되었습니다.'})
    except Exception as e:
        log_error(e, f"아티팩트 {artifact_id} 삭제")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/studies/<int:study_id>/regenerate-plan', methods=['POST'])
@tier_required(['free'])
def regenerate_study_plan(study_id):
    """기존 연구의 계획서 재생성 - 비동기 처리"""
    try:
        if not session_scope:
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        form_data = data.get('formData', {})
        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        prepared = workspace_service.prepare_plan_regeneration(
            study_id=study_id,
            owner_id=int(user_id_int),
        )
        if prepared.status == "not_found":
            return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404
        if prepared.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403

        artifact_id = prepared.data['artifact_id']
        study_slug = prepared.data['study_slug']
        project_id = prepared.data['project_id']

        project_keywords = fetch_project_keywords(project_id)
        llm_usage_context = build_llm_usage_context(
            user_id=user_id_int,
            feature_key="plan_generation",
        )

        def generate_plan_background():
            # Import here to avoid circular imports
            from routes.plan_routes import handle_oneshot_parallel_experts
            try:
                log_expert_analysis("백그라운드 계획서 재생성", f"시작: artifact_id={artifact_id}, study_id={study_id}")
                response = handle_oneshot_parallel_experts(form_data, project_keywords)
                if response.get('success'):
                    updated = workspace_service.complete_artifact(
                        artifact_id=artifact_id,
                        content=response.get('final_plan', ''),
                    )
                    if not updated:
                        return
                    log_analysis_complete()
                    log_data_processing(
                        "계획서 재생성 완료",
                        {"artifact_id": artifact_id, "study_id": study_id},
                        "백그라운드 계획서 재생성 성공",
                    )
                else:
                    workspace_service.delete_artifact_by_id(artifact_id=artifact_id)
            except Exception as e:
                log_error(e, f"백그라운드 계획서 재생성 오류: artifact_id={artifact_id}, study_id={study_id}")
                try:
                    workspace_service.delete_artifact_by_id(artifact_id=artifact_id)
                except Exception as delete_error:
                    log_error(delete_error, f"재생성 오류 후 artifact 삭제 실패: artifact_id={artifact_id}")

        thread = threading.Thread(
            target=lambda: run_with_llm_usage_context(llm_usage_context, generate_plan_background),
            daemon=True,
        )
        thread.start()

        return jsonify({
            'success': True,
            'study_id': study_id,
            'study_slug': study_slug,
            'artifact_id': artifact_id,
            'message': '계획서를 생성하고 있습니다...'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/studies/<int:study_id>', methods=['PUT'])
@tier_required(['free'])
def update_study(study_id):
    """연구 정보 업데이트"""
    try:
        if not (session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        result = workspace_service.update_study(
            study_id=study_id,
            owner_id=int(user_id_int),
            data=data,
        )
        if result.status == "empty_update":
            return jsonify({'success': False, 'error': '업데이트할 데이터가 없습니다.'}), 400
        if result.status == "not_found":
            return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403

        return jsonify({
            'success': True,
            'message': '연구 정보가 업데이트되었습니다.',
            'data': result.data
        })
    except Exception as e:
        print(f"[ERROR] 업데이트 오류: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
