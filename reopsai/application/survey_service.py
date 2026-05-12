from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
import json
import traceback
from typing import Any

from api_logger import (
    log_analysis_complete,
    log_expert_analysis,
    log_keyword_extraction,
    log_rag_quality_check,
    log_step_search,
    log_step_search_clean,
    log_user_request,
)
from reopsai.infrastructure.repositories import SurveyRepository
from prompts.analysis_prompts import (
    ScreenerPrompts,
    SurveyBuilderPrompts,
    SurveyDiagnosisPrompts,
    SurveyGenerationPrompts,
)
from reopsai.shared.llm import parse_llm_json_response


@dataclass(frozen=True)
class SurveyResult:
    status: str
    data: Any = None
    error: str | None = None


class SurveyService:
    _DEFAULT_SESSION_FACTORY = object()
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        repository=None,
        session_factory=_DEFAULT_SESSION_FACTORY,
        openai_adapter=_DEFAULT_ADAPTER,
        gemini_adapter=_DEFAULT_ADAPTER,
        vector_adapter=_DEFAULT_ADAPTER,
        json_parser=None,
        project_keyword_fetcher=_DEFAULT_ADAPTER,
        contextual_keyword_extractor=_DEFAULT_ADAPTER,
        usage_context_builder=_DEFAULT_ADAPTER,
        usage_runner=_DEFAULT_ADAPTER,
    ):
        if repository is None:
            repository = SurveyRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai.infrastructure.database import session_scope

            session_factory = session_scope
        if openai_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_openai_service

            openai_adapter = get_openai_service()
        if gemini_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_gemini_service

            gemini_adapter = get_gemini_service()
        if vector_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.rag import get_vector_service

            vector_adapter = get_vector_service()
        if project_keyword_fetcher is self._DEFAULT_ADAPTER:
            from reopsai.application.keywords import fetch_project_keywords

            project_keyword_fetcher = fetch_project_keywords
        if contextual_keyword_extractor is self._DEFAULT_ADAPTER:
            from reopsai.application.keywords import extract_contextual_keywords_from_input

            contextual_keyword_extractor = extract_contextual_keywords_from_input
        if usage_context_builder is self._DEFAULT_ADAPTER:
            from reopsai.shared.usage_metering import build_llm_usage_context

            usage_context_builder = build_llm_usage_context
        if usage_runner is self._DEFAULT_ADAPTER:
            from reopsai.shared.usage_metering import run_with_llm_usage_context

            usage_runner = run_with_llm_usage_context

        self.repository = repository
        self.session_factory = session_factory
        self.openai_adapter = openai_adapter
        self.gemini_adapter = gemini_adapter
        self.vector_adapter = vector_adapter
        self.json_parser = json_parser or parse_llm_json_response
        self.project_keyword_fetcher = project_keyword_fetcher
        self.contextual_keyword_extractor = contextual_keyword_extractor
        self.usage_context_builder = usage_context_builder
        self.usage_runner = usage_runner

    def db_ready(self):
        return self.session_factory is not None

    def get_survey_principles(self):
        if self.vector_adapter:
            rag_results = self.vector_adapter.improved_service.hybrid_search(
                query_text="설문조사 설계의 모든 원칙",
                principles_n=20,
                examples_n=0,
                topics=["설문", "설계"],
            )
            log_step_search("설문진단원칙", "설문조사 설계의 모든 원칙", rag_results, "설문 진단용 원칙 수집")
            log_rag_quality_check("설문진단원칙", "설문조사 설계의 모든 원칙", rag_results)
            return self.vector_adapter.improved_service.context_optimization(
                rag_results["principles"],
                max_length=2000,
            )
        return "참고할 설문 원칙을 DB에서 로드하는 데 실패했습니다."

    def diagnose_survey(self, *, survey_text) -> SurveyResult:
        log_user_request("설문 진단하기", survey_text)
        principles = self.get_survey_principles()
        prompt_functions = [
            SurveyDiagnosisPrompts.prompt_diagnose_clarity,
            SurveyDiagnosisPrompts.prompt_diagnose_terminology,
            SurveyDiagnosisPrompts.prompt_diagnose_leading_questions,
            SurveyDiagnosisPrompts.prompt_diagnose_options_mec,
            SurveyDiagnosisPrompts.prompt_diagnose_flow,
        ]
        expert_names = [
            "명확성/간결성",
            "용어 사용",
            "유도 질문",
            "보기의 상호배타성/포괄성",
            "논리적 순서/스크리너 배치",
        ]

        keywords = self.contextual_keyword_extractor(survey_text)
        log_keyword_extraction(keywords)
        log_step_search_clean("설문진단", f"설문 진단 {keywords}", {"principles": principles}, "설문 품질 진단")
        for expert_name in expert_names:
            log_expert_analysis(expert_name, "진단중")

        usage_context = self.usage_context_builder(feature_key="survey_generation")

        def call_expert(index, expert_name):
            try:
                prompt = prompt_functions[index](survey_text, principles)
                raw_result = self.openai_adapter.generate_response(prompt, {"temperature": 0.1})
                return self.json_parser(raw_result)
            except Exception as exc:
                return {
                    "check_item_key": prompt_functions[index].__name__.replace("prompt_diagnose_", ""),
                    "pass": False,
                    "reason": f"진단 중 오류 발생: {str(exc)}",
                    "quote": "",
                }

        expert_results = [None] * len(prompt_functions)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_index = {
                executor.submit(self.usage_runner, usage_context, call_expert, i, expert_names[i]): i
                for i in range(len(prompt_functions))
            }
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    expert_results[index] = future.result()
                except Exception as exc:
                    expert_results[index] = {
                        "check_item_key": prompt_functions[index].__name__.replace("prompt_diagnose_", ""),
                        "pass": False,
                        "reason": f"병렬 처리 중 오류 발생: {str(exc)}",
                        "quote": "",
                    }

        log_analysis_complete()
        return SurveyResult("ok", expert_results)

    def generate_draft(self, *, survey_text, item_to_fix) -> SurveyResult:
        principles = self.get_survey_principles()
        prompt = SurveyGenerationPrompts.prompt_generate_survey_draft(survey_text, item_to_fix, principles)
        raw_result = self.openai_adapter.generate_response(prompt, {"temperature": 0.5})
        parsed = self.json_parser(raw_result)
        return SurveyResult("ok", {"draft": parsed.get("draft_suggestions", [])})

    def polish_plan(self, *, survey_text, confirmed_survey) -> SurveyResult:
        confirmed_fixes_json = json.dumps(confirmed_survey, ensure_ascii=False, indent=2)
        prompt = SurveyGenerationPrompts.prompt_polish_survey(survey_text, confirmed_fixes_json)
        raw_result = self.openai_adapter.generate_response(prompt, {"temperature": 0.3})
        return SurveyResult("ok", self.json_parser(raw_result))

    def create_survey_generation(self, *, study_id) -> SurveyResult:
        if not self.db_ready():
            return SurveyResult("db_unavailable")
        with self.session_factory() as db_session:
            study = self.repository.get_study(db_session, study_id)
            if not study:
                return SurveyResult("not_found")
            owner_id = self.repository.get_project_owner_id(db_session, study.project_id)
            if owner_id is None:
                return SurveyResult("project_not_found")
            artifact = self.repository.create_survey_artifact(
                db_session,
                study_id=study_id,
                owner_id=owner_id,
            )
            artifact_id = artifact.id
            project_id = study.project_id

        project_keywords = self.project_keyword_fetcher(project_id)
        return SurveyResult(
            "ok",
            {
                "artifact_id": artifact_id,
                "project_id": project_id,
                "project_keywords": project_keywords,
            },
        )

    def generate_survey_background(self, *, artifact_id, research_plan, project_keywords) -> SurveyResult:
        try:
            print("[Survey Gen] Step 1: 변수 추출 시작")
            variables_prompt = ScreenerPrompts.prompt_analyze_plan(research_plan)
            variables_result = self.openai_adapter.generate_response(variables_prompt, {"temperature": 0.3})
            if not variables_result["success"]:
                raise Exception("변수 추출 실패")

            variables_data = self.json_parser(variables_result)
            key_variables = variables_data.get("key_variables", [])
            balance_variables = variables_data.get("balance_variables", [])
            target_groups = variables_data.get("target_groups", [])

            print("[Survey Gen] Step 1.5: 스크리닝 기준(행동 지표) 정규화")
            screening_criteria = []
            try:
                criteria_prompt = ScreenerPrompts.prompt_normalize_screening_criteria(
                    research_plan=research_plan,
                    key_variables_json=json.dumps(key_variables, ensure_ascii=False, indent=2),
                )
                criteria_result = self.openai_adapter.generate_response(criteria_prompt, {"temperature": 0.2})
                if criteria_result.get("success"):
                    criteria_data = self.json_parser(criteria_result)
                    screening_criteria = criteria_data.get("screening_criteria", []) or []
            except Exception as exc:
                print(f"[Survey Gen] Step 1.5 경고 - 정규화 실패(계속 진행): {exc}")

            survey_data = {
                "key_variables": key_variables,
                "balance_variables": balance_variables,
                "target_groups": target_groups,
                "screening_criteria": screening_criteria,
                "form_elements": [],
            }
            partial_content = f"<!-- SURVEY_DATA\n{json.dumps(survey_data, ensure_ascii=False, indent=2)}\n-->\n\n"
            partial_content += "# 스크리너 설문\n\n"
            partial_content += "## 📝 설문 문항\n\n"
            partial_content += "_문항을 생성하고 있습니다..._\n\n"
            self._update_artifact_content(artifact_id=artifact_id, content=partial_content)
            print("[Survey Gen] Step 1 완료 - 변수 분석 결과 표시")

            print("[Survey Gen] Step 2: 문항 구조 생성")
            self.contextual_keyword_extractor(research_plan)
            expanded_query = self.vector_adapter.improved_service.query_expansion(f"설문조사 설계 {research_plan}")
            rag_results = self.vector_adapter.improved_service.hybrid_search(
                query_text=expanded_query,
                principles_n=4,
                examples_n=3,
                topics=["설문", "스크리너"],
                domain_keywords=project_keywords,
            )
            rules_context = self.vector_adapter.improved_service.context_optimization(
                rag_results["principles"],
                max_length=1200,
            )
            examples_context = self.vector_adapter.improved_service.context_optimization(
                rag_results["examples"],
                max_length=800,
            )

            structure_prompt = SurveyBuilderPrompts.prompt_generate_survey_structure(
                research_plan_content=research_plan,
                key_variables_json=json.dumps(key_variables, ensure_ascii=False, indent=2),
                balance_variables_json=json.dumps(balance_variables, ensure_ascii=False, indent=2),
                screening_criteria_json=json.dumps(screening_criteria, ensure_ascii=False, indent=2),
                rules_context_str=rules_context,
                examples_context_str=examples_context,
            )
            structure_result = self.gemini_adapter.generate_response(
                structure_prompt,
                {"temperature": 0.2},
                model_name="gemini-2.5-pro",
            )
            if not structure_result["success"]:
                raise Exception("문항 구조 생성 실패")

            structure_data = self.json_parser(structure_result)
            blocks = structure_data.get("blocks", []) or []
            form_elements = structure_data.get("form_elements", [])
            questions_list = structure_data.get("questions", [])
            if form_elements:
                questions_list = form_elements
                print(f"[Survey Gen] 새로운 형식(form_elements) 사용: {len(form_elements)}개 문항")
            else:
                print(f"[Survey Gen] 기존 형식(questions) 사용: {len(questions_list)}개 문항")

            blocks = self._ensure_blocks(blocks)
            default_block_id = self._default_block_id(blocks)
            for question in questions_list:
                if isinstance(question, dict) and not question.get("block_id"):
                    question["block_id"] = default_block_id

            print("[Survey Gen] Step 3: 선택지 생성 시작")
            all_select_questions = self._select_questions(questions_list)
            if all_select_questions:
                relevant_context = self.vector_adapter.search(
                    query_text=self._options_rag_query(all_select_questions),
                    n_results=10,
                    filter_metadata={"data_type": "예시"},
                    domain_keywords=project_keywords,
                )
                options_prompt = SurveyBuilderPrompts.prompt_generate_all_answer_options(
                    questions_json_chunk=json.dumps(all_select_questions, ensure_ascii=False, indent=2),
                    relevant_examples_str=relevant_context,
                )
                options_result = self.openai_adapter.generate_response(options_prompt, {"temperature": 0.3})
                if options_result["success"]:
                    options_data = self.json_parser(options_result)
                    self._apply_options(questions_list, options_data.get("options", {}))
                    print(f"[Survey Gen] Step 3 완료 - {len(all_select_questions)}개 문항의 선택지 생성됨")
                else:
                    print("[Survey Gen] Step 3 경고 - 선택지 생성 실패, 선택지 없이 진행")

            print("[Survey Gen] Step 4: 최종 변환")
            content = self._build_final_content(
                key_variables=key_variables,
                balance_variables=balance_variables,
                target_groups=target_groups,
                screening_criteria=screening_criteria,
                blocks=blocks,
                questions_list=questions_list,
            )
            self._complete_artifact(artifact_id=artifact_id, content=content)
            print("[Survey Gen] 완료!")
            return SurveyResult("ok", {"artifact_id": artifact_id})
        except Exception as exc:
            print(f"[ERROR] Survey 생성 실패: {exc}")
            traceback.print_exc()
            self._handle_generation_failure(artifact_id=artifact_id, error=exc)
            return SurveyResult("failed", error=str(exc))

    def _update_artifact_content(self, *, artifact_id, content):
        if not self.db_ready():
            raise Exception("데이터베이스 연결 실패")
        with self.session_factory() as db_session:
            self.repository.update_artifact_content(db_session, artifact_id=artifact_id, content=content)

    def _complete_artifact(self, *, artifact_id, content):
        if not self.db_ready():
            raise Exception("데이터베이스 연결 실패")
        with self.session_factory() as db_session:
            self.repository.complete_artifact(db_session, artifact_id=artifact_id, content=content)

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

    @staticmethod
    def _ensure_blocks(blocks):
        if isinstance(blocks, list) and len(blocks) > 0:
            return blocks
        return [
            {"id": "intro", "title": "Block 0: 안내/동의", "kind": "intro", "ai_comment": "조사 안내와 동의 확인입니다."},
            {"id": "A_qualification", "title": "Block A: 필수 자격 (Qualification)", "kind": "qualification", "ai_comment": "여기서 통과 못 하면 바로 종료됩니다."},
            {"id": "B_demographics", "title": "Block B: 배경 정보 (Demographics)", "kind": "demographics", "ai_comment": "나중에 쿼터(성비/연령비) 맞출 때 쓰이는 변수들입니다."},
            {"id": "C_open_ended", "title": "Block C: 심층 질문 (Open-ended)", "kind": "open_ended", "ai_comment": "인터뷰 대상자로 적합한지 판단할 때 읽어볼 내용입니다."},
            {"id": "D_ops", "title": "Block D: 운영 정보 (Ops)", "kind": "ops", "ai_comment": "일정/연락 등 운영에 필요한 정보입니다."},
        ]

    @staticmethod
    def _default_block_id(blocks):
        try:
            return (blocks[-1] or {}).get("id") or "D_ops"
        except Exception:
            return "D_ops"

    @staticmethod
    def _select_questions(questions_list):
        selected = []
        for question in questions_list:
            question_type = question.get("type") if isinstance(question, dict) else None
            element = question.get("element") if isinstance(question, dict) else None
            if question_type and ("선택" in question_type or "객관식" in question_type):
                selected.append(question)
            elif element in ["RadioButtons", "Checkboxes"]:
                selected.append(question)
        return selected

    @staticmethod
    def _options_rag_query(all_select_questions):
        return f"""
                    다음 질문 목록에 대한 '선택지(보기)' 예시를 찾아줘:
                    {json.dumps(all_select_questions, ensure_ascii=False, indent=2)}
                    ---
                    특히 '연령', '성별', '경험 유무', '사용 빈도' 등을 묻는 질문의 모범 답안 예시가 필요해.
                    """

    @staticmethod
    def _apply_options(questions_list, options_object):
        for question in questions_list:
            question_id = question.get("id") if isinstance(question, dict) else None
            if not question_id or question_id not in options_object:
                continue
            option_value = options_object[question_id]
            if isinstance(option_value, list) and len(option_value) > 0 and isinstance(option_value[0], dict):
                question["options"] = option_value
            elif isinstance(option_value, str):
                question["options"] = [
                    opt.strip().lstrip("-").strip()
                    for opt in option_value.split("\n")
                    if opt.strip()
                ]
            elif isinstance(option_value, list):
                question["options"] = option_value

    @staticmethod
    def _build_final_content(
        *,
        key_variables,
        balance_variables,
        target_groups,
        screening_criteria,
        blocks,
        questions_list,
    ):
        is_new_format = len(questions_list) > 0 and "element" in questions_list[0]
        survey_data = {
            "key_variables": key_variables,
            "balance_variables": balance_variables,
            "target_groups": target_groups,
            "screening_criteria": screening_criteria,
            "blocks": blocks,
        }
        if is_new_format:
            survey_data["form_elements"] = questions_list
            print("[Survey Gen] 새로운 형식(form_elements)으로 저장")
        else:
            survey_data["questions"] = questions_list
            print("[Survey Gen] 기존 형식(questions)으로 저장")

        content = f"<!-- SURVEY_DATA\n{json.dumps(survey_data, ensure_ascii=False, indent=2)}\n-->\n\n"
        content += "# 스크리너 설문\n\n"
        content += "## 📝 설문 문항\n\n"

        questions_by_block = {}
        for question in questions_list:
            if not isinstance(question, dict):
                continue
            block_id = question.get("block_id") or question.get("section_id") or "D_ops"
            questions_by_block.setdefault(block_id, []).append(question)

        question_num = 1
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_id = block.get("id")
            if not block_id or block_id not in questions_by_block:
                continue
            content += f"### {block.get('title') or block_id}\n\n"
            ai_comment = (block.get("ai_comment") or "").strip()
            if ai_comment:
                content += f"> AI 코멘트: {ai_comment}\n\n"
            for question in questions_by_block[block_id]:
                content += f"#### {question_num}. {question.get('text')}\n"
                content += f"**유형**: {question.get('type') or question.get('element', '')}\n"
                if question.get("options") and len(question.get("options")) > 0:
                    content += "**선택지**:\n"
                    for option in question.get("options"):
                        if isinstance(option, dict):
                            content += f"- {option.get('text', option.get('value', ''))}\n"
                        else:
                            content += f"- {option}\n"
                content += "\n"
                question_num += 1
        return content


try:
    survey_service = SurveyService()
except Exception as exc:
    print(f"[WARN] SurveyService 기본 어댑터 초기화 실패: {exc}")
    survey_service = SurveyService(
        openai_adapter=None,
        gemini_adapter=None,
        vector_adapter=None,
        project_keyword_fetcher=lambda _project_id: [],
        contextual_keyword_extractor=lambda _text: [],
        usage_context_builder=lambda **_kwargs: {},
        usage_runner=lambda _context, func, *args, **kwargs: func(*args, **kwargs),
    )
