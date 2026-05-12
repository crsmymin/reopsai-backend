from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import traceback
from typing import Any

from reopsai.infrastructure.repositories import ArtifactAiRepository


@dataclass(frozen=True)
class ArtifactAiResult:
    status: str
    data: Any = None
    error: str | None = None


class ArtifactAiService:
    _DEFAULT_SESSION_FACTORY = object()
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        repository=None,
        session_factory=_DEFAULT_SESSION_FACTORY,
        gemini_adapter=_DEFAULT_ADAPTER,
        vector_adapter=_DEFAULT_ADAPTER,
        usage_context_builder=_DEFAULT_ADAPTER,
        usage_runner=_DEFAULT_ADAPTER,
    ):
        if repository is None:
            repository = ArtifactAiRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai.infrastructure.database import session_scope

            session_factory = session_scope
        if gemini_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_gemini_service

            gemini_adapter = get_gemini_service()
        if vector_adapter is self._DEFAULT_ADAPTER:
            vector_adapter = self._build_default_vector_service()
        if usage_context_builder is self._DEFAULT_ADAPTER:
            from reopsai.shared.usage_metering import build_llm_usage_context

            usage_context_builder = build_llm_usage_context
        if usage_runner is self._DEFAULT_ADAPTER:
            from reopsai.shared.usage_metering import run_with_llm_usage_context

            usage_runner = run_with_llm_usage_context

        self.repository = repository
        self.session_factory = session_factory
        self.gemini_adapter = gemini_adapter
        self.vector_adapter = vector_adapter
        self.usage_context_builder = usage_context_builder
        self.usage_runner = usage_runner

    @staticmethod
    def _build_default_vector_service():
        try:
            from rag_system.improved.improved_vector_db_service import VectorDBServiceWrapper

            return VectorDBServiceWrapper(
                db_path=os.getenv("RAG_DB_PATH", "./chroma_db"),
                collection_name="ux_rag",
            )
        except Exception as exc:
            print(f"[WARN] artifact_ai: VectorDB 초기화 실패 (RAG 검색 비활성화): {exc}")
            return None

    def db_ready(self):
        return self.session_factory is not None

    @staticmethod
    def history_payload(row):
        return {
            "id": str(row.id),
            "artifact_id": row.artifact_id,
            "user_id": row.user_id,
            "prompt": row.prompt,
            "source": row.source,
            "before_markdown": row.before_markdown,
            "after_markdown": row.after_markdown,
            "selection_from": row.selection_from,
            "selection_to": row.selection_to,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def get_artifact_for_owner(self, *, artifact_id, owner_ids) -> ArtifactAiResult:
        if not self.db_ready():
            return ArtifactAiResult("db_unavailable")
        with self.session_factory() as db_session:
            artifact = self.repository.get_artifact(db_session, artifact_id)
            if not artifact:
                return ArtifactAiResult("not_found")
            payload = {
                "id": artifact.id,
                "content": artifact.content,
                "owner_id": artifact.owner_id,
                "study_id": artifact.study_id,
            }
        if str(payload.get("owner_id", "")) not in owner_ids:
            return ArtifactAiResult("forbidden")
        return ArtifactAiResult("ok", payload)

    def list_edit_history(self, *, artifact_id, owner_ids, limit) -> ArtifactAiResult:
        access = self.get_artifact_for_owner(artifact_id=artifact_id, owner_ids=owner_ids)
        if access.status != "ok":
            return access
        if not self.db_ready():
            return ArtifactAiResult("db_unavailable")
        with self.session_factory() as db_session:
            rows = self.repository.list_edit_history(db_session, artifact_id=artifact_id, limit=limit)
            return ArtifactAiResult("ok", [self.history_payload(row) for row in rows])

    def create_edit_history(
        self,
        *,
        artifact_id,
        owner_ids,
        user_id,
        before_markdown,
        after_markdown,
        prompt,
        source,
        selection_from,
        selection_to,
    ) -> ArtifactAiResult:
        access = self.get_artifact_for_owner(artifact_id=artifact_id, owner_ids=owner_ids)
        if access.status != "ok":
            return access
        if not self.db_ready():
            return ArtifactAiResult("db_unavailable")
        with self.session_factory() as db_session:
            row = self.repository.create_edit_history(
                db_session,
                artifact_id=artifact_id,
                user_id=user_id,
                prompt=prompt,
                source=source,
                before_markdown=before_markdown,
                after_markdown=after_markdown,
                selection_from=selection_from,
                selection_to=selection_to,
            )
            return ArtifactAiResult("ok", self.history_payload(row))

    def modify_artifact_text(
        self,
        *,
        artifact_id,
        owner_ids,
        user_id,
        selected_text,
        user_prompt,
        full_context,
        selected_markdown_hint,
    ) -> ArtifactAiResult:
        access = self.get_artifact_for_owner(artifact_id=artifact_id, owner_ids=owner_ids)
        if access.status != "ok":
            return access

        usage_context = self.usage_context_builder(user_id=user_id, feature_key="artifact_ai")
        rag_principles, rag_examples = self._search_rag_context(
            user_prompt=user_prompt,
            selected_text=selected_text,
        )
        llm_prompt = self._build_llm_prompt(
            selected_text=selected_text,
            user_prompt=user_prompt,
            full_context=full_context,
            selected_markdown_hint=selected_markdown_hint,
            rag_principles=rag_principles,
            rag_examples=rag_examples,
        )
        generation_config = {
            "temperature": 0.3,
            "max_output_tokens": max(8192, min(16384, len(selected_text) * 4)),
        }

        response = self.usage_runner(
            usage_context,
            self.gemini_adapter.generate_response,
            prompt=llm_prompt,
            generation_config=generation_config,
            model_name="gemini-2.5-flash",
        )
        if not response.get("success"):
            return ArtifactAiResult(
                "llm_failed",
                error=f"AI 수정 실패: {response.get('error', '알 수 없는 오류')}",
            )

        raw = self._strip_code_fences((response.get("content") or "").strip())
        if self._looks_truncated_json(raw):
            return ArtifactAiResult(
                "incomplete_response",
                error="AI 응답이 완전하지 않습니다. 응답이 중간에 잘렸을 수 있습니다. 다시 시도해주세요.",
            )

        parsed, parse_error = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            print(f"[WARN] JSON 파싱 실패. 원본 응답 (처음 500자): {raw[:500]}")
            print(f"[WARN] 파싱 오류: {parse_error}")
            return ArtifactAiResult(
                "ok",
                {
                    "original": selected_text,
                    "modified": raw,
                    "message": "AI 수정 제안을 생성했습니다. (JSON 파싱 경고: 응답이 완전한 JSON 형식이 아닐 수 있습니다.)",
                },
            )

        original_out = (parsed.get("original") or "").strip() or selected_text
        if original_out != selected_text:
            original_out = selected_text
        modified_out = (parsed.get("modified") or "").strip()
        if not modified_out:
            print("[WARN] modified 필드가 비어있음. raw_response를 사용합니다.")
            modified_out = raw if raw else selected_text

        if self._looks_partial(selected_text, modified_out):
            print(
                "[WARN] modified가 부분만 반환된 것으로 의심됨. "
                f"selected_text_len={len(selected_text)}, modified_len={len(modified_out)}"
            )
            modified_out = self._retry_full_text(
                usage_context=usage_context,
                llm_prompt=llm_prompt,
                generation_config=generation_config,
                selected_text=selected_text,
                current_modified=modified_out,
            )

        if self._looks_partial(selected_text, modified_out):
            return ArtifactAiResult(
                "partial_response",
                error="AI가 문서 전체가 아니라 일부만 반환했습니다. 다시 생성해 주세요. (문서 전체 반환 강제 중)",
            )

        return ArtifactAiResult(
            "ok",
            {
                "original": original_out,
                "modified": modified_out,
                "message": "AI 수정 제안을 생성했습니다.",
            },
        )

    def _search_rag_context(self, *, user_prompt, selected_text):
        rag_principles = ""
        rag_examples = ""
        if not self.vector_adapter:
            return rag_principles, rag_examples
        try:
            query_parts = re.findall(r"[가-힣]{2,}", user_prompt)[:5]
            for line in selected_text.split("\n")[:3]:
                query_parts.extend(re.findall(r"[가-힣]{2,}", line)[:3])
            rag_query = " ".join(set(query_parts))[:200]
            if not rag_query:
                return rag_principles, rag_examples
            rag_results = self.vector_adapter.improved_service.hybrid_search(
                query_text=rag_query,
                principles_n=2,
                examples_n=2,
                topics=["계획서", "리서치", "조사", "연구", "가설", "방법론", "대상자"],
            )
            rag_principles = self.vector_adapter.improved_service.context_optimization(
                rag_results.get("principles", ""),
                max_length=800,
            )
            rag_examples = self.vector_adapter.improved_service.context_optimization(
                rag_results.get("examples", ""),
                max_length=600,
            )
        except Exception as exc:
            print(f"[WARN] artifact_ai: RAG 검색 실패 (계속 진행): {exc}")
        return rag_principles, rag_examples

    @staticmethod
    def _build_llm_prompt(
        *,
        selected_text,
        user_prompt,
        full_context,
        selected_markdown_hint,
        rag_principles,
        rag_examples,
    ):
        system_prompt = (
            "You must output JSON. 당신은 리서치 계획서를 작성하고 검토하는 '수석 리서처'입니다. "
            "전문가 수준의 논리적 일관성, 전문 용어 사용, 그리고 전체 문서 맥락을 깊이 이해한 상태에서 수정해야 합니다.\n\n"
            "**[핵심 원칙]**\n"
            "1. **전체 맥락 활용**: full_context에 담긴 연구 목표, 조사 대상, 방법론, 일정 등 전체 구조를 먼저 파악하고, "
            "selected_text가 그 맥락 안에서 어떤 역할을 하는지 이해한 뒤 수정하세요.\n"
            "2. **논리적 일관성**: 수정한 부분이 문서의 다른 섹션(연구 목표, 방법론, 일정 등)과 논리적으로 모순되지 않아야 합니다.\n"
            "3. **전문 용어 유지**: 리서치 전문 용어(예: FGI, UT, 정성/정량, 타겟 그룹, 선별 기준 등)를 정확하게 사용하고, "
            "일반인 수준의 표현으로 격하하지 마세요.\n"
            "4. **원문 구조 최대한 유지**: selected_markdown_hint가 제공되면, 그 구조(헤딩/리스트/표/강조/인용 등)를 가능한 한 유지하세요.\n"
            "5. **요청만 정확히 반영**: user_prompt에서 요청한 수정만 정확하게 반영하고, 요청하지 않은 부분은 원문을 최대한 보존하세요.\n"
            "6. **전체 텍스트 유지(매우 중요)**: modified에는 selected_text의 '일부'만 반환하면 안 됩니다. "
            "반드시 selected_text 전체를 반환하되, user_prompt가 요구하는 부분만 수정하고 나머지는 가능한 한 원문을 그대로 유지하세요.\n"
            "6. **예시 기반 학습**: user_prompt에 '~처럼', '~스타일로', '~참고해서' 같은 표현이 있으면, "
            "full_context에서 해당 패턴/예시를 찾아서 그 스타일을 적용하세요.\n"
            "7. **도메인 지식 활용**: 아래 '참고 원칙/예시'를 활용하여 전문가 수준의 추론을 수행하세요. "
            "도메인 지식이 부족해도, full_context의 다른 섹션과 참고 자료를 종합해서 논리적으로 추론하세요.\n"
            "8. **추론을 통한 풍부한 작성**: user_prompt의 요청을 단순히 반영하는 것을 넘어서, 전체 맥락과 도메인 지식을 종합적으로 추론하여 "
            "selected_text를 더 풍부하고 완성도 높게 작성하세요. 관련 근거, 구체적인 예시, 논리적 연결고리 등을 자연스럽게 보강하되, "
            "원문의 핵심 의도와 구조는 유지하세요.\n\n"
            "**[출력 형식]**\n"
            "- 오직 JSON만 출력한다. 다른 텍스트는 절대 포함하지 않는다.\n"
            "- 설명, 주석, 따옴표로 감싼 추가 텍스트, 마크다운 코드블록(```)을 절대 포함하지 않는다.\n"
            "- 출력 JSON 형식은 반드시 {\"original\": \"...\", \"modified\": \"...\"} 이어야 한다.\n"
            "- original은 반드시 입력으로 받은 selected_text와 동일한 문자열이어야 한다.\n"
            "- modified는 가능한 한 Markdown 형식으로 작성한다.\n"
            "- JSON을 반드시 완전하게 출력해야 한다. 중간에 잘리면 안 된다.\n"
            "- modified 필드의 값이 길어도 반드시 완전한 JSON으로 출력해야 한다.\n"
        )
        rag_section = ""
        if rag_principles or rag_examples:
            rag_section = "\n**[참고 원칙 및 예시 (RAG 검색 결과)]**\n"
            if rag_principles:
                rag_section += f"원칙:\n{rag_principles}\n\n"
            if rag_examples:
                rag_section += f"예시:\n{rag_examples}\n\n"
            rag_section += "위 원칙과 예시를 참고하여 전문가 수준으로 수정하세요.\n\n"
        return (
            f"{system_prompt}\n\n"
            f"{rag_section}"
            f"**[전체 문서 맥락 (full_context)]**\n{full_context}\n\n"
            f"**[수정할 부분 (selected_text)]**\n{selected_text}\n\n"
            f"**[원문 구조 힌트 (selected_markdown_hint)]**\n{selected_markdown_hint}\n\n"
            f"**[사용자 요청 (user_prompt)]**\n{user_prompt}\n\n"
            "위 정보를 바탕으로, 전체 맥락과 참고 원칙/예시를 활용하여 "
            "selected_text 전체를(원문 유지 + 요청 부분만 수정) 전문가 수준으로 수정한 후 JSON으로만 출력하세요."
        )

    @staticmethod
    def _strip_code_fences(raw):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
        raw = re.sub(r"\n?\s*```$", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"```(?:json)?\s*\n", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\n\s*```", "", raw)
        return raw

    @staticmethod
    def _looks_truncated_json(raw):
        if not raw:
            return False
        raw_stripped = raw.strip()
        if "{" not in raw_stripped or raw_stripped.endswith("}"):
            return False
        return raw_stripped.count("{") > raw_stripped.count("}")

    @staticmethod
    def _parse_json_object(raw):
        parse_error = None
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(raw[start:end + 1]), parse_error
        except (json.JSONDecodeError, ValueError) as exc:
            parse_error = f"{parse_error}; {str(exc)}"
        try:
            brace_count = 0
            start = -1
            for index, char in enumerate(raw):
                if char == "{":
                    if start == -1:
                        start = index
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0 and start != -1:
                        return json.loads(raw[start:index + 1]), parse_error
        except (json.JSONDecodeError, ValueError) as exc:
            parse_error = f"{parse_error}; {str(exc)}"
        return None, parse_error

    @staticmethod
    def _looks_partial(full_text, candidate):
        try:
            if not full_text or not candidate:
                return False
            if len(full_text) < 1200:
                return False
            return len(candidate) < int(len(full_text) * 0.7)
        except Exception:
            return False

    def _retry_full_text(self, *, usage_context, llm_prompt, generation_config, selected_text, current_modified):
        harden_prompt = (
            llm_prompt
            + "\n\n🚨 재요청: 방금 응답은 selected_text의 일부만 반환한 것으로 보입니다.\n"
            + "반드시 selected_text 전체를 modified에 반환하세요. (요청한 부분만 수정 + 나머지는 원문 유지)\n"
            + "형식은 동일하게 {\"original\": \"...\", \"modified\": \"...\"} JSON만 출력하세요."
        )
        retry = self.usage_runner(
            usage_context,
            self.gemini_adapter.generate_response,
            prompt=harden_prompt,
            generation_config=generation_config,
            model_name="gemini-2.5-flash",
        )
        if not retry.get("success"):
            return current_modified
        raw = self._strip_code_fences((retry.get("content") or "").strip())
        parsed, _error = self._parse_json_object(raw)
        if isinstance(parsed, dict):
            modified = (parsed.get("modified") or "").strip()
            if modified and not self._looks_partial(selected_text, modified):
                return modified
        return current_modified


try:
    artifact_ai_service = ArtifactAiService()
except Exception as exc:
    print(f"[WARN] ArtifactAiService 기본 어댑터 초기화 실패: {exc}")
    artifact_ai_service = ArtifactAiService(
        gemini_adapter=None,
        vector_adapter=None,
        usage_context_builder=lambda **_kwargs: {},
        usage_runner=lambda _context, func, *args, **kwargs: func(*args, **kwargs),
    )
