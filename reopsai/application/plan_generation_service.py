from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
import json
import time
from typing import Any, List, Optional

from api_logger import (
    log_analysis_complete,
    log_error,
    log_expert_analysis,
    log_performance,
)
from prompts.analysis_prompts import GenerationPrompts
from reopsai.application.plan_artifacts import (
    cleanup_oneshot_plan_artifact_after_error,
    complete_conversation_plan_artifact,
    complete_oneshot_plan_artifact,
    delete_oneshot_plan_artifact,
    fail_conversation_plan_artifact,
)
from reopsai.application.plan_conversation import (
    allowed_card_types,
    build_conversation_final_plan_prompt,
    build_conversation_recommendation_prompt,
    build_previous_context_summary,
    build_transition_hint,
    get_interrogation_rules,
    parse_conversation_recommendation_payload,
)
from reopsai.application.plan_context import (
    analyze_previous_step_selections,
    extract_selected_methodologies_from_ledger,
    ledger_cards_to_context_text,
)
from reopsai.application.plan_experts import (
    build_expert_outputs,
    build_input_with_methodology,
    build_oneshot_combined_input,
    build_oneshot_final_prompt,
    one_shot_expert_configs,
)
from reopsai.application.plan_keywords import (
    extract_and_log_keywords,
    fetch_project_keywords_for_project,
    normalize_project_keywords,
)
from reopsai.application.plan_service import plan_service
from reopsai.application.plan_rag import (
    prepare_conversation_final_rag_context,
    prepare_conversation_recommendation_rag_context,
    prepare_oneshot_expert_rag_context,
)
from reopsai.application.plan_study_helper import (
    build_form_context_info,
    build_study_helper_prompt,
    study_helper_prompt_functions,
)


@dataclass(frozen=True)
class PlanGenerationResult:
    status: str
    data: Any = None
    error: str | None = None


class PlanGenerationService:
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        *,
        openai_adapter=_DEFAULT_ADAPTER,
        gemini_adapter=_DEFAULT_ADAPTER,
        vector_adapter=_DEFAULT_ADAPTER,
        contextual_keyword_extractor=_DEFAULT_ADAPTER,
        project_keyword_fetcher=_DEFAULT_ADAPTER,
        usage_context_getter=_DEFAULT_ADAPTER,
        usage_runner=_DEFAULT_ADAPTER,
        record_service=None,
    ):
        if openai_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_openai_service

            openai_adapter = get_openai_service()
        if gemini_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_gemini_service

            gemini_adapter = get_gemini_service()
        if vector_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.rag import get_vector_service

            vector_adapter = get_vector_service()
        if contextual_keyword_extractor is self._DEFAULT_ADAPTER:
            from reopsai.application.keywords import extract_contextual_keywords_from_input

            contextual_keyword_extractor = extract_contextual_keywords_from_input
        if project_keyword_fetcher is self._DEFAULT_ADAPTER:
            from reopsai.application.keywords import fetch_project_keywords

            project_keyword_fetcher = fetch_project_keywords
        if usage_context_getter is self._DEFAULT_ADAPTER:
            from reopsai.shared.usage_metering import get_llm_usage_context

            usage_context_getter = get_llm_usage_context
        if usage_runner is self._DEFAULT_ADAPTER:
            from reopsai.shared.usage_metering import run_with_llm_usage_context

            usage_runner = run_with_llm_usage_context

        self.openai_adapter = openai_adapter
        self.gemini_adapter = gemini_adapter
        self.vector_adapter = vector_adapter
        self.contextual_keyword_extractor = contextual_keyword_extractor
        self.project_keyword_fetcher = project_keyword_fetcher
        self.usage_context_getter = usage_context_getter
        self.usage_runner = usage_runner
        self.record_service = record_service or plan_service

    def adapter_status(self):
        return {
            "vector_service": self.vector_adapter is not None,
            "gemini_service": self.gemini_adapter is not None,
        }

    @staticmethod
    def analyze_previous_step_selections(ledger_cards, step_int):
        return analyze_previous_step_selections(ledger_cards, step_int)

    @staticmethod
    def ledger_cards_to_context_text(ledger_cards: object, max_chars: int = 12000) -> str:
        return ledger_cards_to_context_text(ledger_cards, max_chars=max_chars)

    @staticmethod
    def extract_selected_methodologies_from_ledger(ledger_cards: object) -> List[str]:
        return extract_selected_methodologies_from_ledger(ledger_cards)

    def generate_oneshot_parallel_experts(self, form_data, project_keywords: Optional[List[str]] = None):
        try:
            project_keywords = normalize_project_keywords(project_keywords)

            problem_definition = form_data.get('problemDefinition', '')
            methodologies = form_data.get('methodologies', [])
            combined_input = build_oneshot_combined_input(form_data)

            keywords = extract_and_log_keywords(
                problem_definition,
                contextual_keyword_extractor=self.contextual_keyword_extractor,
                project_keywords=project_keywords,
            )

            principles_context, examples_context = prepare_oneshot_expert_rag_context(
                self.vector_adapter,
                keywords=keywords,
                project_keywords=project_keywords,
            )

            log_expert_analysis("방법론 전문가", "우선 실행")
            methodology_prompt = GenerationPrompts.prompt_generate_methodology_fit(
                combined_input, principles_context, examples_context
            )
            methodology_result = self.openai_adapter.generate_response(methodology_prompt, {"temperature": 0.4})

            if not methodology_result['success']:
                raise Exception(f"방법론 전문가 호출 실패: {methodology_result.get('error')}")

            methodology_expert_result = {
                'expert': '방법론 적합성',
                'content': methodology_result['content'],
                'success': True,
            }
            methodology_result_content = methodology_result['content']

            expert_configs = one_shot_expert_configs(GenerationPrompts)

            def call_expert(expert_name, prompt_func):
                try:
                    combined_input_with_methodology = build_input_with_methodology(
                        combined_input,
                        methodology_result_content,
                    )
                    if expert_name == "분석 방법":
                        prompt = prompt_func(combined_input, methodology_result_content, principles_context, examples_context)
                    else:
                        prompt = prompt_func(combined_input_with_methodology, principles_context, examples_context)

                    result = self.openai_adapter.generate_response(prompt, {"temperature": 0.3})
                    if result['success']:
                        return {'expert': expert_name, 'content': result['content'], 'success': True}
                    return {'expert': expert_name, 'error': result.get('error'), 'success': False}
                except Exception as exc:
                    return {'expert': expert_name, 'error': str(exc), 'success': False}

            log_expert_analysis("7개 전문가", "병렬 호출 시작 (방법론 결과 포함 + 일정 전문가)")

            expert_results = [methodology_expert_result]
            executor_usage_context = self.usage_context_getter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
                futures = [
                    executor.submit(self.usage_runner, executor_usage_context, call_expert, name, func)
                    for name, func in expert_configs
                ]
                for future in concurrent.futures.as_completed(futures):
                    expert_results.append(future.result())

            successful_experts = [r for r in expert_results if r['success']]
            if len(successful_experts) < 7:
                raise Exception(f"전문가 호출 실패: {len(successful_experts)}/8 성공")

            expert_outputs = build_expert_outputs(successful_experts)
            final_prompt = build_oneshot_final_prompt(
                methodologies=methodologies,
                combined_input=combined_input,
                expert_outputs=expert_outputs,
            )

            log_expert_analysis("최종통합", "Pro 모델로 취합")
            final_result = self.gemini_adapter.generate_response(
                final_prompt,
                {"temperature": 0.3},
                model_name="gemini-2.5-pro",
            )

            if final_result['success']:
                log_analysis_complete()
                return {
                    'success': True,
                    'final_plan': final_result['content'],
                    'expert_count': len(successful_experts),
                    'generation_type': 'parallel_experts',
                }
            raise Exception("최종 통합 실패")

        except Exception as exc:
            log_error(exc, "원샷 전문가 병렬 처리")
            return {'success': False, 'error': str(exc)}

    def generate_oneshot_plan_background(self, *, artifact_id, study_id, form_data, project_keywords) -> PlanGenerationResult:
        try:
            log_expert_analysis("백그라운드 계획서 생성", f"시작: artifact_id={artifact_id}")
            response = self.generate_oneshot_parallel_experts(form_data, project_keywords)
            if response.get('success'):
                complete_oneshot_plan_artifact(
                    self.record_service,
                    artifact_id=artifact_id,
                    study_id=study_id,
                    final_plan=response.get('final_plan', ''),
                )
                return PlanGenerationResult("ok")

            delete_oneshot_plan_artifact(self.record_service, artifact_id=artifact_id)
            return PlanGenerationResult("failed", error=response.get("error"))
        except Exception as exc:
            log_error(exc, f"백그라운드 계획서 생성 오류: artifact_id={artifact_id}, study_id={study_id}")
            cleanup_oneshot_plan_artifact_after_error(self.record_service, artifact_id=artifact_id)
            return PlanGenerationResult("failed", error=str(exc))

    def stream_study_helper_chat(self, *, data):
        helper_prompt, generation_config = self._build_study_helper_prompt(data)

        try:
            result = self.openai_adapter.generate_response(helper_prompt, generation_config)
            if result['success']:
                content = result['content']
                words = content.split(' ')
                for index, word in enumerate(words):
                    chunk_data = {
                        'content': word + (' ' if index < len(words) - 1 else ''),
                        'done': index == len(words) - 1,
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                    time.sleep(0.02)
            else:
                error_data = {'error': '응답 생성에 실패했습니다.', 'done': True}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
        except Exception as exc:
            error_data = {'error': str(exc), 'done': True}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    def _build_study_helper_prompt(self, data):
        return build_study_helper_prompt(data)

    @staticmethod
    def _build_form_context_info(current_form, project_name):
        return build_form_context_info(current_form, project_name)

    @staticmethod
    def _study_helper_prompt_functions(*, current_form, context_info, user_message, task):
        return study_helper_prompt_functions(
            current_form=current_form,
            context_info=context_info,
            user_message=user_message,
            task=task,
        )

    def build_conversation_recommendation(self, *, data) -> PlanGenerationResult:
        start_time = time.time()
        try:
            step = data.get('step', 0)
            mode = data.get('mode', 'recommend')
            conversation = data.get('conversation', []) or []
            ledger_cards = data.get('ledger_cards', []) or []
            project_id = data.get('projectId')

            try:
                step_int = int(step)
            except (TypeError, ValueError):
                step_int = 0

            ledger_text = self.ledger_cards_to_context_text(ledger_cards, max_chars=4000)
            conversation_text = "\n".join(
                [
                    f"{msg.get('type', 'user')}: {msg.get('content', '')}"
                    for msg in conversation
                    if isinstance(msg, dict)
                ]
            )

            combined_input = f"""[LEDGER]
{ledger_text}

[CONVERSATION]
{conversation_text}
""".strip()

            project_keywords = fetch_project_keywords_for_project(
                project_id,
                self.project_keyword_fetcher,
            )

            concise_source = combined_input[:5000]
            keywords = extract_and_log_keywords(
                concise_source,
                contextual_keyword_extractor=self.contextual_keyword_extractor,
                project_keywords=project_keywords,
            )

            principles_context, examples_context = prepare_conversation_recommendation_rag_context(
                self.vector_adapter,
                step_int=step_int,
                keywords=keywords,
                project_keywords=project_keywords,
            )

            previous_analysis = self.analyze_previous_step_selections(ledger_cards, step_int)
            prompt = self._build_conversation_recommendation_prompt(
                step_int=step_int,
                conversation_text=conversation_text,
                ledger_text=ledger_text,
                ledger_cards=ledger_cards,
                previous_analysis=previous_analysis,
                principles_context=principles_context,
                examples_context=examples_context,
            )

            llm_result = self.openai_adapter.generate_response(prompt, {"temperature": 0.4})
            if not llm_result.get('success'):
                raise Exception(llm_result.get('error', 'LLM 호출 실패'))

            payload = self._parse_conversation_recommendation_payload(
                raw_content=llm_result.get('content', ''),
                step_int=step_int,
                mode=mode,
            )
            duration = time.time() - start_time
            log_performance("send_conversation_message", duration, f"step_{step_int}")
            return PlanGenerationResult("ok", payload)
        except Exception as exc:
            log_error(exc, "Conversation message 오류")
            return PlanGenerationResult("failed", error=str(exc))

    def _build_conversation_recommendation_prompt(
        self,
        *,
        step_int,
        conversation_text,
        ledger_text,
        ledger_cards,
        previous_analysis,
        principles_context,
        examples_context,
    ):
        return build_conversation_recommendation_prompt(
            step_int=step_int,
            conversation_text=conversation_text,
            ledger_text=ledger_text,
            ledger_cards=ledger_cards,
            previous_analysis=previous_analysis,
            principles_context=principles_context,
            examples_context=examples_context,
        )

    @staticmethod
    def _build_previous_context_summary(step_int, previous_analysis):
        return build_previous_context_summary(step_int, previous_analysis)

    @staticmethod
    def _build_transition_hint(step_int, has_previous_selections, is_step_transition):
        return build_transition_hint(step_int, has_previous_selections, is_step_transition)

    @staticmethod
    def get_interrogation_rules(step_int: int) -> str:
        return get_interrogation_rules(step_int)

    def _parse_conversation_recommendation_payload(self, *, raw_content, step_int, mode):
        return parse_conversation_recommendation_payload(
            raw_content=raw_content,
            step_int=step_int,
            mode=mode,
        )

    @staticmethod
    def _allowed_card_types(step):
        return allowed_card_types(step)

    def generate_conversation_plan_background(
        self,
        *,
        artifact_id,
        study_id,
        ledger_text,
        selected_methods,
        project_keywords,
    ) -> PlanGenerationResult:
        try:
            log_expert_analysis("ConversationStudyMaker 최종계획서", f"시작: artifact_id={artifact_id}")

            keywords = extract_and_log_keywords(
                ledger_text,
                contextual_keyword_extractor=self.contextual_keyword_extractor,
                project_keywords=project_keywords,
            )

            principles_context, examples_context = prepare_conversation_final_rag_context(
                self.vector_adapter,
                keywords=keywords,
                project_keywords=project_keywords,
            )

            final_prompt = build_conversation_final_plan_prompt(
                ledger_text=ledger_text,
                selected_methods=selected_methods,
                project_keywords=project_keywords,
                principles_context=principles_context,
                examples_context=examples_context,
            )

            final_result = self.gemini_adapter.generate_response(
                final_prompt,
                {"temperature": 0.3},
                model_name="gemini-2.5-pro",
            )
            if not final_result.get('success'):
                raise Exception(final_result.get('error', '최종 생성 실패'))

            complete_conversation_plan_artifact(
                self.record_service,
                artifact_id=artifact_id,
                study_id=study_id,
                content=final_result.get('content', ''),
            )
            return PlanGenerationResult("ok")

        except Exception as exc:
            log_error(exc, f"ConversationStudyMaker 계획서 생성 실패: artifact_id={artifact_id}, study_id={study_id}")
            fail_conversation_plan_artifact(self.record_service, artifact_id=artifact_id, error=exc)
            return PlanGenerationResult("failed", error=str(exc))


plan_generation_service = PlanGenerationService()
