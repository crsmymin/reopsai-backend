"""
계획서 생성 및 대화형 리서치 Blueprint.

app.py에서 분리됨. URL prefix: /api
"""
import threading
import time
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt
from reopsai_backend.shared.request import _extract_request_user_id

from api_logger import (
    log_api_call, log_error, log_performance,
)
from debug_utils import analyze_error_patterns, get_stats, request_tracker
from reopsai_backend.application.plan_generation_service import plan_generation_service
from reopsai_backend.application.plan_service import plan_service
from reopsai_backend.shared.auth import tier_required
from reopsai_backend.shared.idempotency import (
    _complete_idempotency_entry, _fail_idempotency_entry,
    _reserve_idempotency_entry, _respond_from_entry,
)
from reopsai_backend.shared.usage_metering import build_llm_usage_context, run_with_llm_usage_context, stream_with_llm_usage_context

plan_bp = Blueprint('plan', __name__, url_prefix='/api')


def _plan_service_ready():
    return getattr(plan_service, "db_ready", lambda: True)()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_llm_usage_context(user_id, request_id):
    return build_llm_usage_context(
        user_id=user_id,
        request_id=request_id,
        feature_key="plan_generation",
    )


def _ledger_cards_to_context_text(ledger_cards: object, max_chars: int = 12000) -> str:
    return plan_generation_service.ledger_cards_to_context_text(ledger_cards, max_chars=max_chars)


def _extract_selected_methodologies_from_ledger(ledger_cards: object):
    return plan_generation_service.extract_selected_methodologies_from_ledger(ledger_cards)


def handle_oneshot_parallel_experts(form_data, project_keywords=None):
    """Compatibility wrapper used by the workspace controller."""
    return plan_generation_service.generate_oneshot_parallel_experts(form_data, project_keywords)


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@plan_bp.route('/study-helper/chat', methods=['POST'])
@tier_required(['free'])
def study_helper_chat():
    """연구 생성 폼의 챗봇 도우미 (스트리밍 응답)"""
    try:
        data = request.json or {}
        llm_usage_context = build_llm_usage_context(feature_key="plan_generation")
        return current_app.response_class(
            stream_with_llm_usage_context(
                llm_usage_context,
                plan_generation_service.stream_study_helper_chat(data=data),
            ),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/generator/create-plan-oneshot', methods=['POST'])
@tier_required(['free'])
def generator_create_plan_oneshot():
    """원샷 계획서 생성기 - study 먼저 생성 후 즉시 반환, 계획서는 백그라운드 생성"""
    idempotency_key = None
    idempotency_completed = False
    created_study_id = None
    created_artifact_id = None

    def _cleanup_created_records():
        plan_service.cleanup_created_records(
            study_id=created_study_id,
            artifact_id=created_artifact_id,
        )

    def _fail_with(message: str, status: int = 500, cleanup: bool = False):
        nonlocal idempotency_completed
        if cleanup:
            _cleanup_created_records()
        error_payload = {'success': False, 'error': message}
        if idempotency_key:
            _fail_idempotency_entry(idempotency_key, error_payload, status)
            idempotency_completed = True
        return jsonify(error_payload), status

    try:
        if not _plan_service_ready():
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        log_api_call('/api/generator/create-plan-oneshot', 'POST', data)
        form_data = data.get('formData') or {}
        project_id = data.get('projectId')
        request_id = data.get('requestId') or data.get('request_id') or uuid.uuid4().hex

        problem_definition = (form_data.get('problemDefinition') or '').strip()
        study_name = (form_data.get('studyName') or '').strip()
        methodologies = form_data.get('methodologies') or []

        if not problem_definition:
            return jsonify({'success': False, 'error': '문제 정의는 필수입니다.'}), 400
        if not study_name:
            return jsonify({'success': False, 'error': '연구명은 필수입니다.'}), 400
        if not project_id:
            return jsonify({'success': False, 'error': 'projectId는 필수입니다.'}), 400

        try:
            project_id_int = int(project_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': '유효하지 않은 projectId 입니다.'}), 400

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        idempotency_key = f"{user_id_int}:{project_id_int}:{request_id}"
        idempotency_entry, is_new_request = _reserve_idempotency_entry(idempotency_key)
        if not is_new_request:
            return _respond_from_entry(idempotency_entry)

        try:
            claims = get_jwt() or {}
        except Exception:
            claims = {}
        tier = claims.get('tier') or 'free'
        llm_usage_context = _build_llm_usage_context(user_id_int, request_id)

        created = plan_service.create_oneshot_records(
            project_id=project_id_int,
            user_id=user_id_int,
            tier=tier,
            form_data=form_data,
        )
        if created.status == "project_not_found":
            return _fail_with('프로젝트 정보를 찾을 수 없습니다.', 404)
        if created.status == "forbidden":
            return _fail_with('접근 권한이 없습니다.', 403)
        if created.status == "study_quota_exceeded":
            return _fail_with('Free 플랜에서는 스터디는 1개까지만 생성할 수 있습니다.', 403)
        if created.status == "plan_quota_exceeded":
            return _fail_with('Free 플랜에서는 계획서는 1개까지만 생성할 수 있습니다.', 403)
        if created.status == "db_unavailable":
            return _fail_with('DB 연결 실패', 500)

        created_study_id = created.data["study_id"]
        created_artifact_id = created.data["artifact_id"]
        study_id = created.data["study_id"]
        study_slug = created.data["study_slug"]
        artifact_id = created.data["artifact_id"]
        project_keywords = created.data["project_keywords"]

        def generate_plan_background():
            plan_generation_service.generate_oneshot_plan_background(
                artifact_id=artifact_id,
                study_id=study_id,
                form_data=form_data,
                project_keywords=project_keywords,
            )

        thread = threading.Thread(
            target=lambda: run_with_llm_usage_context(llm_usage_context, generate_plan_background),
            daemon=True,
        )
        thread.start()

        response_payload = {
            'success': True,
            'study_id': study_id,
            'study_slug': study_slug,
            'artifact_id': artifact_id,
            'request_id': request_id,
            'message': '연구가 생성되었습니다. 계획서를 생성하고 있습니다...'
        }
        _complete_idempotency_entry(idempotency_key, response_payload, 200)
        idempotency_completed = True
        return jsonify(response_payload)
    except Exception as e:
        log_error(e, "원샷 계획서 생성")
        _cleanup_created_records()
        if idempotency_key and not idempotency_completed:
            _fail_idempotency_entry(idempotency_key, {'success': False, 'error': str(e)}, 500)
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/conversation/message', methods=['POST'])
@tier_required(['free'])
def send_conversation_message():
    """대화형 리서치 생성기 - 사용자 메시지 처리 및 AI 응답 생성."""
    try:
        data = request.json or {}
        log_api_call('/api/conversation/message', 'POST', data)
        result = plan_generation_service.build_conversation_recommendation(data=data)
        if result.status == "ok":
            return jsonify(result.data)
        return jsonify({"success": False, "error": result.error}), 500
    except Exception as e:
        log_error(e, "Conversation message 오류")
        return jsonify({"success": False, "error": str(e)}), 500


@plan_bp.route('/generator/conversation-maker/finalize-oneshot', methods=['POST'])
@tier_required(['free'])
def conversation_maker_finalize_oneshot():
    """카드 누적형 ConversationStudyMaker - Study+pending plan artifact 생성 후 백그라운드 계획서 생성."""
    start_time = time.time()
    idempotency_key = None
    idempotency_completed = False
    created_study_id = None
    created_artifact_id = None

    try:
        if not _plan_service_ready():
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        log_api_call('/api/generator/conversation-maker/finalize-oneshot', 'POST', data)

        project_id = data.get('projectId')
        study_name = (data.get('studyName') or '').strip()
        ledger_cards = data.get('ledger_cards') or []
        request_id = data.get('requestId') or data.get('request_id') or uuid.uuid4().hex

        if not project_id:
            return jsonify({"success": False, "error": "projectId는 필수입니다."}), 400
        if not study_name:
            return jsonify({"success": False, "error": "studyName은 필수입니다."}), 400
        if not isinstance(ledger_cards, list) or len(ledger_cards) == 0:
            return jsonify({"success": False, "error": "ledger_cards가 비어있습니다."}), 400

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        try:
            project_id_int = int(project_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': '유효하지 않은 projectId 입니다.'}), 400

        idempotency_key = f"{user_id_int}:{project_id_int}:{request_id}"
        idempotency_entry, is_new_request = _reserve_idempotency_entry(idempotency_key)
        if not is_new_request:
            return _respond_from_entry(idempotency_entry)

        def cleanup_created_records():
            plan_service.cleanup_created_records(
                study_id=created_study_id,
                artifact_id=created_artifact_id,
            )

        def fail_with(message: str, status: int = 500, cleanup_fn=None):
            nonlocal idempotency_completed
            if cleanup_fn:
                try:
                    cleanup_fn()
                except Exception as cleanup_error:
                    log_error(cleanup_error, f"실패 처리 중 정리 작업 실패: {message}")
            error_payload = {'success': False, 'error': message}
            _fail_idempotency_entry(idempotency_key, error_payload, status)
            idempotency_completed = True
            return jsonify(error_payload), status

        try:
            claims = get_jwt()
        except Exception:
            claims = {}
        tier = (claims or {}).get('tier') or 'free'
        llm_usage_context = _build_llm_usage_context(user_id_int, request_id)
        ledger_text = _ledger_cards_to_context_text(ledger_cards, max_chars=12000)
        selected_methods = _extract_selected_methodologies_from_ledger(ledger_cards)

        created = plan_service.create_conversation_records(
            project_id=project_id_int,
            user_id=user_id_int,
            tier=tier,
            study_name=study_name,
            ledger_text=ledger_text,
            selected_methods=selected_methods,
            ledger_cards=ledger_cards,
        )
        if created.status == "project_not_found":
            return fail_with('프로젝트 정보를 찾을 수 없습니다.', 404)
        if created.status == "forbidden":
            return fail_with('접근 권한이 없습니다.', 403)
        if created.status == "study_quota_exceeded":
            return fail_with('Free 플랜에서는 스터디는 1개까지만 생성할 수 있습니다.', 403)
        if created.status == "plan_quota_exceeded":
            return fail_with('Free 플랜에서는 계획서는 1개까지만 생성할 수 있습니다.', 403)
        if created.status == "db_unavailable":
            return fail_with('DB 연결 실패', 500)

        created_study_id = created.data["study_id"]
        created_artifact_id = created.data["artifact_id"]
        study_id = created.data["study_id"]
        study_slug = created.data["study_slug"]
        artifact_id = created.data["artifact_id"]
        project_keywords = created.data["project_keywords"]

        def generate_plan_background():
            plan_generation_service.generate_conversation_plan_background(
                artifact_id=artifact_id,
                study_id=study_id,
                ledger_text=ledger_text,
                selected_methods=selected_methods,
                project_keywords=project_keywords,
            )

        thread = threading.Thread(
            target=lambda: run_with_llm_usage_context(llm_usage_context, generate_plan_background),
            daemon=True,
        )
        thread.start()

        response_payload = {
            'success': True,
            'study_id': study_id,
            'study_slug': study_slug,
            'artifact_id': artifact_id,
            'request_id': request_id,
            'message': '연구가 생성되었습니다. 계획서를 생성하고 있습니다...'
        }
        _complete_idempotency_entry(idempotency_key, response_payload, 200)
        idempotency_completed = True
        duration = time.time() - start_time
        log_performance("conversation_maker_finalize_oneshot", duration)
        return jsonify(response_payload)

    except Exception as e:
        log_error(e, "ConversationStudyMaker finalize 오류")
        try:
            if 'cleanup_created_records' in locals():
                cleanup_created_records()
        except Exception as cleanup_error:
            log_error(cleanup_error, "예외 처리 중 정리 작업 실패")
        if idempotency_key and not idempotency_completed:
            _fail_idempotency_entry(idempotency_key, {'success': False, 'error': str(e)}, 500)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 디버그 엔드포인트
# ---------------------------------------------------------------------------

@plan_bp.route('/debug/stats', methods=['GET'])
@tier_required(['free'])
def debug_get_stats():
    """요청 통계 및 에러 분석"""
    try:
        stats = get_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/debug/analyze-errors', methods=['GET'])
@tier_required(['free'])
def debug_analyze_errors():
    """에러 패턴 분석"""
    try:
        analyze_error_patterns()
        return jsonify({'success': True, 'message': '에러 분석 완료 (콘솔 확인)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/debug/health', methods=['GET'])
@tier_required(['free'])
def debug_health_check():
    """서버 상태 확인"""
    try:
        health_info = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            **plan_generation_service.adapter_status(),
            'active_requests': len(request_tracker.active_requests),
            'completed_requests': len(request_tracker.completed_requests)
        }
        return jsonify({'success': True, 'health': health_info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
