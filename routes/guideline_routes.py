"""
가이드라인 생성 Blueprint.

app.py에서 분리됨. URL prefix: /api
"""
import json
import traceback
import threading

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import Artifact, Project, Study
from prompts.analysis_prompts import GuidelineGeneratorPrompts
from routes.auth import tier_required
from services.openai_service import openai_service
from services.vector_service import vector_service
from utils.keyword_utils import extract_contextual_keywords_from_input, fetch_project_keywords
from utils.llm_utils import parse_llm_json_response
from utils.usage_metering import build_llm_usage_context, run_with_llm_usage_context

guideline_bp = Blueprint('guideline', __name__, url_prefix='/api')


# ---------------------------------------------------------------------------
# 가이드라인 엔드포인트
# ---------------------------------------------------------------------------

@guideline_bp.route('/guideline/extract-methods', methods=['POST'])
@tier_required(['free'])
def guideline_extract_methods():
    try:
        data = request.json
        research_plan = data.get('research_plan', '')
        prompt = GuidelineGeneratorPrompts.prompt_extract_methodologies(research_plan)

        raw_result = openai_service.generate_response(prompt, {"temperature": 0.0})
        parsed_json = parse_llm_json_response(raw_result)

        return jsonify({'success': True, 'methodologies': parsed_json.get('methodologies', [])})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@guideline_bp.route('/extract-methodologies', methods=['POST'])
@tier_required(['free'])
def extract_methodologies():
    """계획서에서 방법론 추출"""
    try:
        data = request.json
        research_plan = data.get('research_plan', '')

        if not research_plan:
            return jsonify({'success': False, 'error': '계획서가 비어있습니다'}), 400

        prompt = GuidelineGeneratorPrompts.prompt_extract_methodologies(research_plan)
        result = openai_service.generate_response(prompt, {"temperature": 0.2})

        if result['success']:
            parsed = parse_llm_json_response(result)
            methodologies = parsed.get('methodologies', [])
            return jsonify({'success': True, 'methodologies': methodologies})
        else:
            return jsonify({'success': False, 'error': 'LLM 응답 실패'}), 500

    except Exception as e:
        print(f"[ERROR] 방법론 추출 실패: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@guideline_bp.route('/guideline/create-and-generate', methods=['POST'])
@tier_required(['free'])
def guideline_create_and_generate():
    """가이드라인 artifact 생성 + 백그라운드 생성"""
    try:
        if not session_scope:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        study_id = data.get('study_id')
        research_plan = data.get('research_plan', '')
        methodologies = data.get('methodologies', [])

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
                artifact_type='guideline',
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
        llm_usage_context = build_llm_usage_context(feature_key="guideline_generation")

        def generate_in_background():
            try:
                print(f"[Guideline Gen] 백그라운드 생성 시작: artifact_id={artifact_id}")

                if vector_service is None:
                    raise Exception('Vector DB 서비스가 초기화되지 않았습니다.')

                if openai_service is None or openai_service.client is None:
                    raise Exception('OpenAI 서비스가 초기화되지 않았습니다.')

                print(f"[Guideline Gen] 서비스 체크 완료")

                options = {'methodology': ', '.join(methodologies)}
                options_json = json.dumps(options, ensure_ascii=False, indent=2)

                methodology = ', '.join(methodologies)
                rag_query = f"""
                계획: {research_plan}
                방법론: {methodology}
                ---
                위 계획과 방법론에 적합한 가이드라인 예시 (웜업, 핵심 질문 등)
                """

                print(f"[Guideline Gen] 키워드 추출 시작")
                keywords = extract_contextual_keywords_from_input(research_plan)
                print(f"[Guideline Gen] 키워드 추출 완료: {keywords}")

                methodology_filter = "usability_test" if "UT" in methodology or "사용성" in methodology else "interview"

                print(f"[Guideline Gen] RAG 검색 시작")
                rag_results = vector_service.improved_service.hybrid_search(
                    query_text=rag_query,
                    principles_n=5,
                    examples_n=3,
                    topics=["가이드라인", methodology_filter],
                    domain_keywords=project_keywords
                )
                print(f"[Guideline Gen] RAG 검색 완료")

                rules_context_str = rag_results['principles']
                examples_context_str = rag_results['examples']

                print(f"[Guideline Gen] 프롬프트 생성 시작")
                prompt = GuidelineGeneratorPrompts.prompt_generate_guideline(
                    research_plan, options_json, rules_context_str, examples_context_str
                )

                print(f"[Guideline Gen] LLM 호출 시작")
                result = openai_service.generate_response(
                    prompt,
                    {"max_output_tokens": 8192},
                    model_name="gpt-5"
                )

                if result['success']:
                    content = result['content']
                    print(f"[Guideline Gen] LLM 호출 완료, content 길이: {len(content)}")

                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            target.content = content
                            target.status = 'completed'

                    print(f"[Guideline Gen] 완료: artifact_id={artifact_id}")
                else:
                    error_msg = result.get('error', '알 수 없는 오류')
                    print(f"[Guideline Gen] LLM 생성 실패: {error_msg}")
                    raise Exception(f'LLM 생성 실패: {error_msg}')

            except Exception as e:
                print(f"[ERROR] Guideline 생성 실패: {e}")
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
        print(f"[ERROR] Guideline artifact 생성 실패: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
