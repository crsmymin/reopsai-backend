"""
워크스페이스 Blueprint - 프로젝트/스터디/아티팩트 CRUD.

app.py에서 분리됨. URL prefix: /api
"""
import threading
import traceback

from flask import Blueprint, Response, jsonify, request

from api_logger import log_error
from reopsai.application.workspace_ai_service import workspace_ai_service
from reopsai.application.workspace_service import workspace_service
from reopsai.shared.auth import tier_required
from reopsai.shared.request import _extract_request_user_id, _resolve_workspace_owner_ids
from reopsai.shared.usage_metering import build_llm_usage_context, run_with_llm_usage_context, stream_with_llm_usage_context

workspace_bp = Blueprint('workspace', __name__, url_prefix='/api')


def _workspace_service_ready():
    return getattr(workspace_service, "db_ready", lambda: True)()


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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        data = request.json or {}
        study_name = data.get('studyName', '')
        problem_definition = data.get('problemDefinition', '')

        if not study_name and not problem_definition:
            return jsonify({'success': False, 'error': '연구명 또는 문제 정의가 필요합니다.'}), 400

        result = workspace_ai_service.generate_project_name(
            study_name=study_name,
            problem_definition=problem_definition,
        )
        if result.status != "ok":
            return jsonify({'success': False, 'error': result.error or '프로젝트명 생성 실패'}), 500
        return jsonify({'success': True, **result.data})

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
        data = request.json or {}
        problem_definition = data.get('problemDefinition', '')

        if not problem_definition or len(problem_definition.strip()) < 10:
            return jsonify({'success': False, 'error': '문제 정의가 필요합니다.'}), 400

        result = workspace_ai_service.generate_study_name(problem_definition=problem_definition)
        if result.status != "ok":
            return jsonify({'success': False, 'error': result.error or '연구명 생성 실패'}), 500
        return jsonify({'success': True, **result.data})

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
        data = request.json or {}
        project_title = (data.get('project_title') or '').strip()
        product_url = (data.get('product_url') or '').strip()

        if len(project_title) < 2 and not product_url:
            return jsonify({'success': False, 'error': '프로젝트 제목 또는 URL이 필요합니다.'}), 400

        llm_usage_context = build_llm_usage_context(feature_key="workspace_ai")

        return Response(
            stream_with_llm_usage_context(
                llm_usage_context,
                workspace_ai_service.stream_tags(
                    project_title=project_title,
                    product_url=product_url,
                ),
            ),
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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

    if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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
        if not _workspace_service_ready():
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

        llm_usage_context = build_llm_usage_context(
            user_id=user_id_int,
            feature_key="plan_generation",
        )

        def generate_plan_background():
            workspace_ai_service.regenerate_plan_background(
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


@workspace_bp.route('/studies/<int:study_id>', methods=['PUT'])
@tier_required(['free'])
def update_study(study_id):
    """연구 정보 업데이트"""
    try:
        if not _workspace_service_ready():
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
