from __future__ import annotations

from dataclasses import dataclass
import json
import traceback
from typing import Any

from db.repositories.guideline_repository import GuidelineRepository
from prompts.analysis_prompts import GuidelineGeneratorPrompts
from utils.llm_utils import parse_llm_json_response


@dataclass(frozen=True)
class GuidelineResult:
    status: str
    data: Any = None
    error: str | None = None


class GuidelineService:
    _DEFAULT_SESSION_FACTORY = object()
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        repository=None,
        session_factory=_DEFAULT_SESSION_FACTORY,
        openai_adapter=_DEFAULT_ADAPTER,
        vector_adapter=_DEFAULT_ADAPTER,
        prompt_builder=None,
        json_parser=None,
        project_keyword_fetcher=_DEFAULT_ADAPTER,
        contextual_keyword_extractor=_DEFAULT_ADAPTER,
    ):
        if repository is None:
            repository = GuidelineRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        if openai_adapter is self._DEFAULT_ADAPTER:
            from services.openai_service import openai_service

            openai_adapter = openai_service
        if vector_adapter is self._DEFAULT_ADAPTER:
            from services.vector_service import vector_service

            vector_adapter = vector_service
        if project_keyword_fetcher is self._DEFAULT_ADAPTER:
            from utils.keyword_utils import fetch_project_keywords

            project_keyword_fetcher = fetch_project_keywords
        if contextual_keyword_extractor is self._DEFAULT_ADAPTER:
            from utils.keyword_utils import extract_contextual_keywords_from_input

            contextual_keyword_extractor = extract_contextual_keywords_from_input

        self.repository = repository
        self.session_factory = session_factory
        self.openai_adapter = openai_adapter
        self.vector_adapter = vector_adapter
        self.prompt_builder = prompt_builder or GuidelineGeneratorPrompts
        self.json_parser = json_parser or parse_llm_json_response
        self.project_keyword_fetcher = project_keyword_fetcher
        self.contextual_keyword_extractor = contextual_keyword_extractor

    def db_ready(self):
        return self.session_factory is not None

    def extract_methods(self, *, research_plan, temperature=0.0, require_success=False) -> GuidelineResult:
        prompt = self.prompt_builder.prompt_extract_methodologies(research_plan or "")
        result = self.openai_adapter.generate_response(prompt, {"temperature": temperature})
        if require_success and not result.get("success"):
            return GuidelineResult("llm_failed", error="LLM 응답 실패")
        parsed = self.json_parser(result)
        return GuidelineResult("ok", {"methodologies": parsed.get("methodologies", [])})

    def create_guideline_generation(self, *, study_id) -> GuidelineResult:
        if not self.db_ready():
            return GuidelineResult("db_unavailable")
        with self.session_factory() as db_session:
            study = self.repository.get_study(db_session, study_id)
            if not study:
                return GuidelineResult("not_found")

            owner_id = self.repository.get_project_owner_id(db_session, study.project_id)
            if owner_id is None:
                return GuidelineResult("project_not_found")

            artifact = self.repository.create_guideline_artifact(
                db_session,
                study_id=study_id,
                owner_id=owner_id,
            )
            artifact_id = artifact.id
            project_id = study.project_id

        project_keywords = self.project_keyword_fetcher(project_id)
        return GuidelineResult(
            "ok",
            {
                "artifact_id": artifact_id,
                "project_id": project_id,
                "project_keywords": project_keywords,
            },
        )

    def generate_guideline_background(
        self,
        *,
        artifact_id,
        research_plan,
        methodologies,
        project_keywords,
    ) -> GuidelineResult:
        try:
            print(f"[Guideline Gen] 백그라운드 생성 시작: artifact_id={artifact_id}")

            if self.vector_adapter is None:
                raise Exception("Vector DB 서비스가 초기화되지 않았습니다.")
            if self.openai_adapter is None or self.openai_adapter.client is None:
                raise Exception("OpenAI 서비스가 초기화되지 않았습니다.")

            print("[Guideline Gen] 서비스 체크 완료")

            normalized_methodologies = methodologies or []
            options = {"methodology": ", ".join(normalized_methodologies)}
            options_json = json.dumps(options, ensure_ascii=False, indent=2)
            methodology = ", ".join(normalized_methodologies)
            rag_query = f"""
                계획: {research_plan}
                방법론: {methodology}
                ---
                위 계획과 방법론에 적합한 가이드라인 예시 (웜업, 핵심 질문 등)
                """

            print("[Guideline Gen] 키워드 추출 시작")
            keywords = self.contextual_keyword_extractor(research_plan)
            print(f"[Guideline Gen] 키워드 추출 완료: {keywords}")

            methodology_filter = "usability_test" if "UT" in methodology or "사용성" in methodology else "interview"

            print("[Guideline Gen] RAG 검색 시작")
            rag_results = self.vector_adapter.improved_service.hybrid_search(
                query_text=rag_query,
                principles_n=5,
                examples_n=3,
                topics=["가이드라인", methodology_filter],
                domain_keywords=project_keywords,
            )
            print("[Guideline Gen] RAG 검색 완료")

            prompt = self.prompt_builder.prompt_generate_guideline(
                research_plan,
                options_json,
                rag_results["principles"],
                rag_results["examples"],
            )

            print("[Guideline Gen] LLM 호출 시작")
            result = self.openai_adapter.generate_response(
                prompt,
                {"max_output_tokens": 8192},
                model_name="gpt-5",
            )
            if not result.get("success"):
                error_msg = result.get("error", "알 수 없는 오류")
                print(f"[Guideline Gen] LLM 생성 실패: {error_msg}")
                raise Exception(f"LLM 생성 실패: {error_msg}")

            content = result["content"]
            print(f"[Guideline Gen] LLM 호출 완료, content 길이: {len(content)}")
            if not self.db_ready():
                raise Exception("데이터베이스 연결 실패")
            with self.session_factory() as db_session:
                self.repository.complete_artifact(db_session, artifact_id=artifact_id, content=content)

            print(f"[Guideline Gen] 완료: artifact_id={artifact_id}")
            return GuidelineResult("ok", {"artifact_id": artifact_id})
        except Exception as exc:
            print(f"[ERROR] Guideline 생성 실패: {exc}")
            traceback.print_exc()
            self._handle_generation_failure(artifact_id=artifact_id, error=exc)
            return GuidelineResult("failed", error=str(exc))

    def _handle_generation_failure(self, *, artifact_id, error):
        try:
            if not self.db_ready():
                raise Exception("데이터베이스 연결 실패")
            with self.session_factory() as db_session:
                self.repository.delete_artifact(db_session, artifact_id)
            print(f"생성 실패로 인해 pending artifact 삭제: artifact_id={artifact_id}, 오류: {str(error)}")
        except Exception as delete_error:
            print(f"[ERROR] 생성 실패 후 artifact 삭제 실패: {delete_error}")
            try:
                if not self.db_ready():
                    return
                with self.session_factory() as db_session:
                    self.repository.mark_artifact_failed(
                        db_session,
                        artifact_id=artifact_id,
                        message=str(error),
                    )
            except Exception:
                pass


try:
    guideline_service = GuidelineService()
except Exception as exc:
    print(f"[WARN] GuidelineService 기본 어댑터 초기화 실패: {exc}")
    guideline_service = GuidelineService(
        openai_adapter=None,
        vector_adapter=None,
        project_keyword_fetcher=lambda _project_id: [],
        contextual_keyword_extractor=lambda _text: [],
    )
