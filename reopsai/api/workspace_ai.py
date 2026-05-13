"""Workspace AI and streaming endpoints."""

import json as _json
import threading
import time as _time

from flask import Response, current_app, jsonify, request

from api_logger import log_error
from reopsai.api import workspace as workspace_module
from reopsai.shared.auth import tier_required
from reopsai.shared.request import _extract_request_user_id
from reopsai.shared.usage_metering import build_llm_usage_context, run_with_llm_usage_context, stream_with_llm_usage_context


@workspace_module.workspace_bp.route('/workspace/generate-project-name', methods=['POST'])
@tier_required(['free'])
def generate_project_name():
    """
    [POST] 프로젝트명 자동 생성
    - studyName과 problemDefinition을 기반으로 AI가 프로젝트명 생성
    - 프로젝트명과 관련 태그를 함께 생성하여 반환
    """
    try:
        data = request.json or {}
        study_name = data.get('studyName', '')
        problem_definition = data.get('problemDefinition', '')

        if not study_name and not problem_definition:
            return jsonify({'success': False, 'error': '연구명 또는 문제 정의가 필요합니다.'}), 400

        result = workspace_module.workspace_ai_service.generate_project_name(
            study_name=study_name,
            problem_definition=problem_definition,
        )
        if result.status != "ok":
            return jsonify({'success': False, 'error': result.error or '프로젝트명 생성 실패'}), 500
        return jsonify({'success': True, **result.data})

    except Exception as e:
        log_error(e, "프로젝트명 생성")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/workspace/generate-study-name', methods=['POST'])
@tier_required(['free'])
def generate_study_name():
    """
    [POST] 연구명 자동 생성
    - problemDefinition을 기반으로 AI가 연구명 생성
    """
    try:
        data = request.json or {}
        problem_definition = data.get('problemDefinition', '')

        if not problem_definition or len(problem_definition.strip()) < 10:
            return jsonify({'success': False, 'error': '문제 정의가 필요합니다.'}), 400

        result = workspace_module.workspace_ai_service.generate_study_name(problem_definition=problem_definition)
        if result.status != "ok":
            return jsonify({'success': False, 'error': result.error or '연구명 생성 실패'}), 500
        return jsonify({'success': True, **result.data})

    except Exception as e:
        log_error(e, "연구명 생성")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/workspace/generate-tags', methods=['POST'])
@tier_required(['free'])
def workspace_generate_tags():
    """
    [POST] 프로젝트 제목 기반 관련 태그 자동 생성
    - Gemini LLM 스트리밍 모드로 태그 실시간 생성
    - 프론트엔드에서 Server-Sent Events (SSE)로 수신
    - 쉼표 단위로 태그가 하나씩 추가되는 효과
    """
    try:
        data = request.json or {}
        project_title = (data.get('project_title') or '').strip()
        product_url = (data.get('product_url') or '').strip()

        if len(project_title) < 2 and not product_url:
            return jsonify({'success': False, 'error': '프로젝트 제목 또는 URL이 필요합니다.'}), 400

        llm_usage_context = build_llm_usage_context(feature_key="workspace_ai")

        return Response(
            stream_with_llm_usage_context(
                llm_usage_context,
                workspace_module.workspace_ai_service.stream_tags(
                    project_title=project_title,
                    product_url=product_url,
                ),
            ),
            mimetype='text/event-stream',
        )

    except Exception as e:
        log_error(e, "태그 생성 API 오류")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/artifacts/<int:artifact_id>/stream', methods=['GET'])
@tier_required(['free'])
def stream_artifact_generation(artifact_id):
    """Artifact 생성 상태 실시간 스트리밍"""

    if not workspace_module._workspace_service_ready():
        return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

    # 사용자 ID 확인 (스트리밍 시작 전에)
    user_id_int, err_resp, err_code = _extract_request_user_id()
    if err_resp:
        return err_resp, err_code

    def generate():
        artifact = workspace_module.workspace_service.get_artifact_for_stream_start(
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
                artifact = workspace_module.workspace_service.get_artifact_for_stream_poll(artifact_id=artifact_id)
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

    return current_app.response_class(generate(), mimetype='text/event-stream')


@workspace_module.workspace_bp.route('/studies/<int:study_id>/regenerate-plan', methods=['POST'])
@tier_required(['free'])
def regenerate_study_plan(study_id):
    """기존 연구의 계획서 재생성 - 비동기 처리"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        form_data = data.get('formData', {})
        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        prepared = workspace_module.workspace_service.prepare_plan_regeneration(
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

        llm_usage_context = build_llm_usage_context(
            user_id=user_id_int,
            feature_key="plan_generation",
        )

        def generate_plan_background():
            workspace_module.workspace_ai_service.regenerate_plan_background(
                artifact_id=artifact_id,
                study_id=study_id,
                project_id=project_id,
                form_data=form_data,
            )

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
