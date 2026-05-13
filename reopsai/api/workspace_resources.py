"""Workspace project, study, and artifact resource endpoints."""

import traceback

from flask import jsonify, request

from api_logger import log_error
from reopsai.api import workspace as workspace_module
from reopsai.shared.auth import tier_required
from reopsai.shared.request import _extract_request_user_id, _resolve_workspace_owner_ids


@workspace_module.workspace_bp.route('/workspace/projects', methods=['GET'])
@tier_required(['free'])
def workspace_get_projects():
    """
    [GET] 현재 사용자의 모든 프로젝트 조회
    - SQLAlchemy 'projects' 테이블에서 owner_id로 필터링
    - 최신순 정렬 (created_at DESC)
    """
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        projects = workspace_module.workspace_service.list_projects(owner_ids)
        return jsonify({'success': True, 'projects': projects})
    except Exception as e:
        log_error(e, "프로젝트 목록 조회")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/workspace/projects-with-studies', methods=['GET'])
@tier_required(['free'])
def workspace_get_projects_with_studies():
    """
    [GET] 현재 사용자의 모든 프로젝트와 각 프로젝트의 스터디를 한 번에 조회
    - 프로젝트와 스터디를 통합하여 반환 (N+1 쿼리 문제 해결)
    - 권한 체크를 한 번만 수행
    """
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)

        summary = workspace_module.workspace_service.get_workspace_summary(owner_ids)

        return jsonify({
            'success': True,
            'projects': summary.projects,
            'all_studies': summary.all_studies,
            'recent_artifacts': summary.recent_artifacts
        })
    except Exception as e:
        log_error(e, "프로젝트+스터디 목록 조회")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/workspace/projects', methods=['POST'])
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
        if not workspace_module._workspace_service_ready():
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

        created_project = workspace_module.workspace_service.create_project(
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


@workspace_module.workspace_bp.route('/workspace/projects/<int:project_id>', methods=['DELETE'])
@tier_required(['free'])
def workspace_delete_project(project_id):
    """
    [DELETE] 프로젝트 삭제
    - SQLAlchemy에서 해당 프로젝트 삭제
    - 관련된 studies도 CASCADE로 자동 삭제 (DB 설정 필요)
    """
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        workspace_module.workspace_service.delete_project(project_id=project_id, owner_id=int(user_id_int))
        return jsonify({'success': True, 'message': f'프로젝트 {project_id} 삭제 완료'})
    except Exception as e:
        log_error(e, f"프로젝트 {project_id} 삭제")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/workspace/projects/<int:project_id>', methods=['PUT'])
@tier_required(['free'])
def workspace_update_project(project_id):
    """
    [PUT] 프로젝트 정보 수정
    - SQLAlchemy에서 프로젝트 정보 업데이트
    """
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        data = request.json or {}
        result = workspace_module.workspace_service.update_project(
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


@workspace_module.workspace_bp.route('/studies/<int:study_id>', methods=['GET'])
@tier_required(['free'])
def get_study(study_id):
    """개별 연구 조회"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_module.workspace_service.get_study(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify(result.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_module.workspace_bp.route('/projects/<int:project_id>', methods=['GET'])
@tier_required(['free'])
def get_project(project_id):
    """개별 프로젝트 조회"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_module.workspace_service.get_project(project_id=project_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify(result.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_module.workspace_bp.route('/projects/<int:project_id>/studies', methods=['GET'])
@tier_required(['free'])
def get_project_studies(project_id):
    """프로젝트의 연구 목록 조회"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_module.workspace_service.list_project_studies(project_id=project_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify({'success': True, 'studies': result.data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/studies/<int:study_id>/schedule', methods=['GET'])
@tier_required(['free'])
def get_study_schedule(study_id):
    """연구의 일정 데이터 조회"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_module.workspace_service.get_study_schedule(study_id=study_id, owner_ids=owner_ids)
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


@workspace_module.workspace_bp.route('/artifacts/<int:artifact_id>', methods=['PUT'])
@tier_required(['free'])
def update_artifact(artifact_id):
    """아티팩트 내용 업데이트"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        content = data.get('content', '')
        if not content.strip():
            return jsonify({'success': False, 'error': '내용이 필요합니다.'}), 400

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        updated = workspace_module.workspace_service.update_artifact_content(
            artifact_id=artifact_id,
            owner_id=int(user_id_int),
            content=content,
        )
        if updated:
            return jsonify({'success': True, 'message': '아티팩트가 업데이트되었습니다.'})
        return jsonify({'success': False, 'error': '아티팩트를 찾을 수 없거나 접근 권한이 없습니다.'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/studies/<int:study_id>/artifacts', methods=['GET'])
@tier_required(['free'])
def get_study_artifacts(study_id):
    """연구의 아티팩트 목록 조회"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_module.workspace_service.list_study_artifacts(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify({'success': True, 'artifacts': result.data})
    except Exception as e:
        print(f"[ERROR] get_study_artifacts 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/studies/<int:study_id>/survey/deployments', methods=['GET'])
@tier_required(['free'])
def get_study_survey_deployments(study_id):
    """연구의 설문 배포 이력 조회.

    현재 배포 이력 저장 모델이 없으므로, 접근 가능한 연구에 대해서는 빈 배열을
    반환한다. 실제 배포 저장소가 추가되면 이 엔드포인트의 응답 배열을 채운다.
    """
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_workspace_owner_ids(user_id_int)
        result = workspace_module.workspace_service.authorize_study(study_id=study_id, owner_ids=owner_ids)
        if result.status == "not_found":
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403

        return jsonify({'success': True, 'deployments': []}), 200
    except Exception as e:
        print(f"[ERROR] get_study_survey_deployments 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/studies/<int:study_id>', methods=['DELETE'])
@tier_required(['free'])
def delete_study(study_id):
    """연구 삭제"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        result = workspace_module.workspace_service.delete_study(study_id=study_id, owner_id=int(user_id_int))
        if result.status == "not_found":
            return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '접근 권한이 없습니다.'}), 403

        return jsonify({'success': True, 'message': '연구가 삭제되었습니다.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/artifacts/<int:artifact_id>', methods=['DELETE'])
@tier_required(['free'])
def delete_artifact(artifact_id):
    """아티팩트 삭제"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        deleted = workspace_module.workspace_service.delete_artifact(
            artifact_id=artifact_id,
            owner_id=int(user_id_int),
        )
        if not deleted:
            return jsonify({'success': False, 'error': '아티팩트를 찾을 수 없거나 삭제 권한이 없습니다.'}), 404

        return jsonify({'success': True, 'message': '아티팩트가 삭제되었습니다.'})
    except Exception as e:
        log_error(e, f"아티팩트 {artifact_id} 삭제")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_module.workspace_bp.route('/studies/<int:study_id>', methods=['PUT'])
@tier_required(['free'])
def update_study(study_id):
    """연구 정보 업데이트"""
    try:
        if not workspace_module._workspace_service_ready():
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        result = workspace_module.workspace_service.update_study(
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
