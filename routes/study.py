"""Study 및 Project 관련 Slug API 라우트"""

from flask import Blueprint, jsonify, abort, request
from flask_jwt_extended import get_jwt_identity

from reopsai_backend.application.study_service import study_service
from reopsai_backend.shared.auth import tier_required
from utils.request_utils import _resolve_workspace_owner_ids
import traceback


study_bp = Blueprint('study', __name__, url_prefix='/api')


def _get_user_id_from_request():
    """요청에서 사용자 ID를 추출 (X-User-ID 헤더 → JWT identity 폴백)"""
    user_id_header = request.headers.get('X-User-ID')
    if user_id_header:
        try:
            return int(user_id_header)
        except Exception:
            return user_id_header
    # Fallback: JWT 쿠키 인증 시 새로고침 후 헤더가 없는 경우
    try:
        identity = get_jwt_identity()
        if identity is not None:
            try:
                return int(identity)
            except Exception:
                return identity
    except Exception:
        pass
    return None


@study_bp.route('/studies/by-slug/<string:slug>', methods=['GET'])
@tier_required(['free'])
def get_study_by_slug(slug: str):
    """Slug로 Study 레코드를 조회 (숫자 ID 입력 시 하위호환 조회 지원)"""
    if not study_service.db_ready():
        abort(500, description='Database session is not initialized')
    
    user_id_int = _get_user_id_from_request()
    if user_id_int is None:
        return jsonify({'error': '사용자 인증이 필요합니다.'}), 401

    owner_ids = _resolve_workspace_owner_ids(user_id_int)
    try:
        result = study_service.get_study_by_slug(slug=slug, owner_ids=owner_ids)
    except Exception as e:
        print(f"[ERROR] Study 조회 실패: slug={slug}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'error': '연구 조회 중 오류가 발생했습니다.'}), 500

    if result.status == "not_found":
        abort(404)
    if result.status == "forbidden":
        print(f"[WARN] 접근 권한 없음 - study by slug: slug={slug}, user_id={user_id_int}")
        return jsonify({'error': '접근 권한이 없습니다.'}), 403
    return jsonify(result.data), 200


@study_bp.route('/projects/by-slug/<string:slug>', methods=['GET'])
@tier_required(['free'])
def get_project_by_slug(slug: str):
    """Slug로 Project 레코드를 조회 (숫자 ID 입력 시 하위호환 조회 지원)"""
    if not study_service.db_ready():
        abort(500, description='Database session is not initialized')
    
    user_id_int = _get_user_id_from_request()
    if user_id_int is None:
        return jsonify({'error': '사용자 인증이 필요합니다.'}), 401

    owner_ids = _resolve_workspace_owner_ids(user_id_int)

    try:
        result = study_service.get_project_by_slug(slug=slug, owner_ids=owner_ids)
    except Exception as e:
        print(f"[ERROR] Project 조회 실패: slug={slug}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'error': '프로젝트 조회 중 오류가 발생했습니다.'}), 500

    if result.status == "not_found":
        return jsonify({'error': '프로젝트를 찾을 수 없거나 접근 권한이 없습니다.'}), 404
    if result.status == "forbidden":
        return jsonify({'error': '접근 권한이 없습니다.'}), 403
    return jsonify(result.data), 200
