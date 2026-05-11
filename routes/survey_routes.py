"""
설문 진단/생성 Blueprint.

app.py에서 분리됨. URL prefix: /api
"""
import concurrent.futures
import json
import traceback
import threading

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from api_logger import (
    log_analysis_complete, log_expert_analysis, log_keyword_extraction,
    log_rag_quality_check, log_step_search, log_step_search_clean, log_user_request,
)
from db.engine import session_scope
from db.models.core import Artifact, Project, Study
from prompts.analysis_prompts import ScreenerPrompts, SurveyBuilderPrompts, SurveyDiagnosisPrompts, SurveyGenerationPrompts
from routes.auth import tier_required
from services.gemini_service import gemini_service
from services.openai_service import openai_service
from services.vector_service import vector_service
from utils.keyword_utils import extract_contextual_keywords_from_input, fetch_project_keywords
from utils.llm_utils import parse_llm_json_response
from utils.usage_metering import build_llm_usage_context, run_with_llm_usage_context

survey_bp = Blueprint('survey', __name__, url_prefix='/api')


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def get_survey_principles():
    """
    [개선] 설문 진단 전문가를 위해 개선된 RAG로 설문 원칙을 가져옵니다.
    """
    if vector_service:
        rag_results = vector_service.improved_service.hybrid_search(
            query_text="설문조사 설계의 모든 원칙",
            principles_n=20,
            examples_n=0,
            topics=["설문", "설계"]
        )

        log_step_search("설문진단원칙", "설문조사 설계의 모든 원칙", rag_results, "설문 진단용 원칙 수집")
        log_rag_quality_check("설문진단원칙", "설문조사 설계의 모든 원칙", rag_results)

        principles_context = vector_service.improved_service.context_optimization(
            rag_results["principles"],
            max_length=2000
        )
        return principles_context
    else:
        return "참고할 설문 원칙을 DB에서 로드하는 데 실패했습니다."


# ---------------------------------------------------------------------------
# 설문 진단 엔드포인트
# ---------------------------------------------------------------------------

@survey_bp.route('/survey-diagnoser/diagnose', methods=['POST'])
@tier_required(['free'])
def survey_diagnoser_diagnose():
    try:
        data = request.json
        survey_text = data.get('survey_text', '')

        log_user_request("설문 진단하기", survey_text)

        principles = get_survey_principles()

        expert_prompt_functions = [
            SurveyDiagnosisPrompts.prompt_diagnose_clarity,
            SurveyDiagnosisPrompts.prompt_diagnose_terminology,
            SurveyDiagnosisPrompts.prompt_diagnose_leading_questions,
            SurveyDiagnosisPrompts.prompt_diagnose_options_mec,
            SurveyDiagnosisPrompts.prompt_diagnose_flow
        ]

        keywords = extract_contextual_keywords_from_input(survey_text)
        log_keyword_extraction(keywords)

        log_step_search_clean("설문진단", f"설문 진단 {keywords}", {"principles": principles}, "설문 품질 진단")

        expert_names = [
            "명확성/간결성", "용어 사용", "유도 질문", "보기의 상호배타성/포괄성", "논리적 순서/스크리너 배치"
        ]

        for expert_name in expert_names:
            log_expert_analysis(expert_name, "진단중")

        def call_survey_expert_diagnosis(i, expert_name):
            try:
                prompt = expert_prompt_functions[i](survey_text, principles)
                raw_result = openai_service.generate_response(prompt, {"temperature": 0.1})
                parsed_json_object = parse_llm_json_response(raw_result)
                return parsed_json_object
            except Exception as e:
                return {
                    "check_item_key": expert_prompt_functions[i].__name__.replace('prompt_diagnose_', ''),
                    "pass": False,
                    "reason": f"진단 중 오류 발생: {str(e)}",
                    "quote": ""
                }

        diagnosis_results = []
        llm_usage_context = build_llm_usage_context(feature_key="survey_generation")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_expert = {
                executor.submit(
                    run_with_llm_usage_context,
                    llm_usage_context,
                    call_survey_expert_diagnosis,
                    i,
                    expert_names[i],
                ): i
                for i in range(len(expert_prompt_functions))
            }

            expert_results = [None] * len(expert_prompt_functions)
            for future in concurrent.futures.as_completed(future_to_expert):
                expert_index = future_to_expert[future]
                try:
                    result = future.result()
                    expert_results[expert_index] = result
                except Exception as exc:
                    expert_results[expert_index] = {
                        "check_item_key": expert_prompt_functions[expert_index].__name__.replace('prompt_diagnose_', ''),
                        "pass": False,
                        "reason": f"병렬 처리 중 오류 발생: {str(exc)}",
                        "quote": ""
                    }

            diagnosis_results = expert_results

        log_analysis_complete()

        return jsonify({'success': True, 'response': diagnosis_results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@survey_bp.route('/survey-diagnoser/generate-draft', methods=['POST'])
@tier_required(['free'])
def survey_diagnoser_generate_draft():
    try:
        data = request.json
        survey_text = data.get('survey_text', '')
        item_to_fix = data.get('item_to_fix', '')

        principles = get_survey_principles()

        prompt = SurveyGenerationPrompts.prompt_generate_survey_draft(survey_text, item_to_fix, principles)
        raw_result = openai_service.generate_response(prompt, {"temperature": 0.5})
        parsed_json = parse_llm_json_response(raw_result)
        response_data = {"draft": parsed_json.get("draft_suggestions", [])}

        return jsonify({'success': True, 'response': response_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@survey_bp.route('/survey-diagnoser/polish-plan', methods=['POST'])
@tier_required(['free'])
def survey_diagnoser_polish_plan():
    try:
        data = request.json
        survey_text = data.get('survey_text', '')
        confirmed_survey = data.get('confirmed_survey', {})

        confirmed_fixes_json = json.dumps(confirmed_survey, ensure_ascii=False, indent=2)

        prompt = SurveyGenerationPrompts.prompt_polish_survey(survey_text, confirmed_fixes_json)
        raw_result = openai_service.generate_response(prompt, {"temperature": 0.3})

        parsed_json = parse_llm_json_response(raw_result)
        return jsonify({'success': True, 'response': parsed_json})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 스크리너(설문) 생성 엔드포인트
# ---------------------------------------------------------------------------

@survey_bp.route('/survey/create-and-generate', methods=['POST'])
@tier_required(['free'])
def survey_create_and_generate():
    """스크리너(설문) artifact 생성 + 백그라운드 생성"""
    try:
        if not session_scope:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        study_id = data.get('study_id')
        research_plan = data.get('research_plan', '')

        try:
            study_id_int = int(study_id)
        except Exception:
            return jsonify({'success': False, 'error': '유효하지 않은 study_id입니다.'}), 400

        with session_scope() as db_session:
            study_obj = db_session.execute(
                select(Study).where(Study.id == study_id_int).limit(1)
            ).scalar_one_or_none()
            if not study_obj:
                return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다'}), 404

            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == study_obj.project_id).limit(1)
            ).scalar_one_or_none()
            if owner_id is None:
                return jsonify({'success': False, 'error': '프로젝트 정보를 찾을 수 없습니다'}), 404

            artifact_obj = Artifact(
                study_id=study_id_int,
                artifact_type='survey',
                content='',
                status='pending',
                owner_id=int(owner_id),
            )
            db_session.add(artifact_obj)
            db_session.flush()
            db_session.refresh(artifact_obj)
            artifact_id = artifact_obj.id
            project_id_for_keywords = study_obj.project_id

        project_keywords = fetch_project_keywords(project_id_for_keywords)
        llm_usage_context = build_llm_usage_context(feature_key="survey_generation")

        if artifact_id is None:
            return jsonify({'success': False, 'error': '스크리너 저장소 생성에 실패했습니다.'}), 500

        def generate_in_background():
            try:
                print(f"[Survey Gen] Step 1: 변수 추출 시작")

                variables_prompt = ScreenerPrompts.prompt_analyze_plan(research_plan)
                variables_result = openai_service.generate_response(variables_prompt, {"temperature": 0.3})

                if not variables_result['success']:
                    raise Exception('변수 추출 실패')

                variables_data = parse_llm_json_response(variables_result)
                key_variables = variables_data.get('key_variables', [])
                balance_variables = variables_data.get('balance_variables', [])
                target_groups = variables_data.get('target_groups', [])

                print(f"[Survey Gen] Step 1.5: 스크리닝 기준(행동 지표) 정규화")
                screening_criteria = []
                try:
                    criteria_prompt = ScreenerPrompts.prompt_normalize_screening_criteria(
                        research_plan=research_plan,
                        key_variables_json=json.dumps(key_variables, ensure_ascii=False, indent=2),
                    )
                    criteria_result = openai_service.generate_response(criteria_prompt, {"temperature": 0.2})
                    if criteria_result.get('success'):
                        criteria_data = parse_llm_json_response(criteria_result)
                        screening_criteria = criteria_data.get('screening_criteria', []) or []
                except Exception as e:
                    print(f"[Survey Gen] Step 1.5 경고 - 정규화 실패(계속 진행): {e}")

                survey_data = {
                    'key_variables': key_variables,
                    'balance_variables': balance_variables,
                    'target_groups': target_groups,
                    'screening_criteria': screening_criteria,
                    'form_elements': []
                }

                partial_content = f"<!-- SURVEY_DATA\n{json.dumps(survey_data, ensure_ascii=False, indent=2)}\n-->\n\n"
                partial_content += "# 스크리너 설문\n\n"
                partial_content += "## 📝 설문 문항\n\n"
                partial_content += "_문항을 생성하고 있습니다..._\n\n"

                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if target:
                        target.content = partial_content
                print(f"[Survey Gen] Step 1 완료 - 변수 분석 결과 표시")

                print(f"[Survey Gen] Step 2: 문항 구조 생성")

                rag_query = f"""
                조사 계획서: {research_plan}
                핵심 변수: {json.dumps(key_variables, ensure_ascii=False)}
                ---
                위 계획서와 변수에 가장 적합한 설문 문항 구조 및 예시
                """

                keywords = extract_contextual_keywords_from_input(research_plan)
                expanded_query = vector_service.improved_service.query_expansion(f"설문조사 설계 {research_plan}")

                rag_results = vector_service.improved_service.hybrid_search(
                    query_text=expanded_query,
                    principles_n=4,
                    examples_n=3,
                    topics=["설문", "스크리너"],
                    domain_keywords=project_keywords
                )

                rules_context_str = vector_service.improved_service.context_optimization(rag_results["principles"], max_length=1200)
                examples_context_str = vector_service.improved_service.context_optimization(rag_results["examples"], max_length=800)

                structure_prompt = SurveyBuilderPrompts.prompt_generate_survey_structure(
                    research_plan_content=research_plan,
                    key_variables_json=json.dumps(key_variables, ensure_ascii=False, indent=2),
                    balance_variables_json=json.dumps(balance_variables, ensure_ascii=False, indent=2),
                    screening_criteria_json=json.dumps(screening_criteria, ensure_ascii=False, indent=2),
                    rules_context_str=rules_context_str,
                    examples_context_str=examples_context_str
                )

                structure_result = gemini_service.generate_response(structure_prompt, {"temperature": 0.2}, model_name="gemini-2.5-pro")

                if not structure_result['success']:
                    raise Exception('문항 구조 생성 실패')

                structure_data = parse_llm_json_response(structure_result)
                blocks = structure_data.get('blocks', []) or []
                form_elements = structure_data.get('form_elements', [])
                questions_list = structure_data.get('questions', [])

                if form_elements:
                    questions_list = form_elements
                    print(f"[Survey Gen] 새로운 형식(form_elements) 사용: {len(form_elements)}개 문항")
                else:
                    print(f"[Survey Gen] 기존 형식(questions) 사용: {len(questions_list)}개 문항")

                if not isinstance(blocks, list) or len(blocks) == 0:
                    blocks = [
                        {"id": "intro", "title": "Block 0: 안내/동의", "kind": "intro", "ai_comment": "조사 안내와 동의 확인입니다."},
                        {"id": "A_qualification", "title": "Block A: 필수 자격 (Qualification)", "kind": "qualification", "ai_comment": "여기서 통과 못 하면 바로 종료됩니다."},
                        {"id": "B_demographics", "title": "Block B: 배경 정보 (Demographics)", "kind": "demographics", "ai_comment": "나중에 쿼터(성비/연령비) 맞출 때 쓰이는 변수들입니다."},
                        {"id": "C_open_ended", "title": "Block C: 심층 질문 (Open-ended)", "kind": "open_ended", "ai_comment": "인터뷰 대상자로 적합한지 판단할 때 읽어볼 내용입니다."},
                        {"id": "D_ops", "title": "Block D: 운영 정보 (Ops)", "kind": "ops", "ai_comment": "일정/연락 등 운영에 필요한 정보입니다."},
                    ]

                default_block_id = "D_ops"
                try:
                    default_block_id = (blocks[-1] or {}).get("id") or default_block_id
                except Exception:
                    pass
                for q in questions_list:
                    if isinstance(q, dict) and not q.get('block_id'):
                        q['block_id'] = default_block_id

                print(f"[Survey Gen] Step 3: 선택지 생성 시작")

                all_select_questions = []
                for q in questions_list:
                    if q.get('type') and ('선택' in q.get('type') or '객관식' in q.get('type')):
                        all_select_questions.append(q)
                    elif q.get('element') in ['RadioButtons', 'Checkboxes']:
                        all_select_questions.append(q)

                if all_select_questions:
                    rag_query = f"""
                    다음 질문 목록에 대한 '선택지(보기)' 예시를 찾아줘:
                    {json.dumps(all_select_questions, ensure_ascii=False, indent=2)}
                    ---
                    특히 '연령', '성별', '경험 유무', '사용 빈도' 등을 묻는 질문의 모범 답안 예시가 필요해.
                    """

                    relevant_context = vector_service.search(
                        query_text=rag_query,
                        n_results=10,
                        filter_metadata={"data_type": "예시"},
                        domain_keywords=project_keywords
                    )

                    options_prompt = SurveyBuilderPrompts.prompt_generate_all_answer_options(
                        questions_json_chunk=json.dumps(all_select_questions, ensure_ascii=False, indent=2),
                        relevant_examples_str=relevant_context
                    )

                    options_result = openai_service.generate_response(options_prompt, {"temperature": 0.3})

                    if options_result['success']:
                        options_data_parsed = parse_llm_json_response(options_result)
                        options_object = options_data_parsed.get('options', {})

                        for q in questions_list:
                            q_id = q.get('id')
                            if q_id and q_id in options_object:
                                opt_value = options_object[q_id]

                                if isinstance(opt_value, list) and len(opt_value) > 0 and isinstance(opt_value[0], dict):
                                    q['options'] = opt_value
                                elif isinstance(opt_value, str):
                                    q['options'] = [opt.strip().lstrip('-').strip() for opt in opt_value.split('\n') if opt.strip()]
                                elif isinstance(opt_value, list):
                                    q['options'] = opt_value

                        print(f"[Survey Gen] Step 3 완료 - {len(all_select_questions)}개 문항의 선택지 생성됨")
                    else:
                        print(f"[Survey Gen] Step 3 경고 - 선택지 생성 실패, 선택지 없이 진행")

                print(f"[Survey Gen] Step 4: 최종 변환")

                is_new_format = len(questions_list) > 0 and 'element' in questions_list[0]

                survey_data = {
                    'key_variables': key_variables,
                    'balance_variables': balance_variables,
                    'target_groups': target_groups,
                    'screening_criteria': screening_criteria,
                    'blocks': blocks,
                }

                if is_new_format:
                    survey_data['form_elements'] = questions_list
                    print(f"[Survey Gen] 새로운 형식(form_elements)으로 저장")
                else:
                    survey_data['questions'] = questions_list
                    print(f"[Survey Gen] 기존 형식(questions)으로 저장")

                content = f"<!-- SURVEY_DATA\n{json.dumps(survey_data, ensure_ascii=False, indent=2)}\n-->\n\n"
                content += "# 스크리너 설문\n\n"
                content += "## 📝 설문 문항\n\n"

                blocks_by_id = {b.get('id'): b for b in blocks if isinstance(b, dict) and b.get('id')}
                questions_by_block = {}
                for q in questions_list:
                    if not isinstance(q, dict):
                        continue
                    block_id = q.get('block_id') or q.get('section_id') or "D_ops"
                    if block_id not in questions_by_block:
                        questions_by_block[block_id] = []
                    questions_by_block[block_id].append(q)

                question_num = 1
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    block_id = b.get('id')
                    if not block_id or block_id not in questions_by_block:
                        continue
                    section_title = b.get('title') or block_id
                    ai_comment = (b.get('ai_comment') or '').strip()
                    content += f"### {section_title}\n\n"
                    if ai_comment:
                        content += f"> AI 코멘트: {ai_comment}\n\n"

                    for q in questions_by_block[block_id]:
                        content += f"#### {question_num}. {q.get('text')}\n"
                        question_type = q.get('type') or q.get('element', '')
                        content += f"**유형**: {question_type}\n"
                        if q.get('options') and len(q.get('options')) > 0:
                            content += "**선택지**:\n"
                            for opt in q.get('options'):
                                if isinstance(opt, dict):
                                    content += f"- {opt.get('text', opt.get('value', ''))}\n"
                                else:
                                    content += f"- {opt}\n"
                        content += "\n"
                        question_num += 1

                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if target:
                        target.content = content
                        target.status = 'completed'

                print(f"[Survey Gen] 완료!")

            except Exception as e:
                print(f"[ERROR] Survey 생성 실패: {e}")
                traceback.print_exc()

                try:
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            bg_session.delete(target)
                    print(f"🗑️ 생성 실패로 인해 pending artifact 삭제: artifact_id={artifact_id}, 오류: {str(e)}")
                except Exception as delete_error:
                    print(f"[ERROR] 생성 실패 후 artifact 삭제 실패: {delete_error}")
                    try:
                        with session_scope() as bg_session:
                            target = bg_session.execute(
                                select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                            ).scalar_one_or_none()
                            if target:
                                target.status = 'failed'
                                target.content = f'❌ 생성 실패: {str(e)}'
                    except Exception:
                        pass

        thread = threading.Thread(
            target=lambda: run_with_llm_usage_context(llm_usage_context, generate_in_background)
        )
        thread.start()

        return jsonify({'success': True, 'artifact_id': artifact_id})

    except Exception as e:
        print(f"[ERROR] Survey artifact 생성 실패: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
