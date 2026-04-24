"""Study 및 Project 관련 Slug API 라우트"""

from flask import Blueprint, jsonify, abort, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import Project, Study
from routes.auth import tier_required
from utils.b2b_access import get_owner_ids_for_request
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
    if session_scope is None:
        abort(500, description='Database session is not initialized')
    
    user_id_int = _get_user_id_from_request()
    if user_id_int is None:
        return jsonify({'error': '사용자 인증이 필요합니다.'}), 401

    owner_ids, _team_id = get_owner_ids_for_request(user_id_int)
    allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}

    try:
        with session_scope() as db_session:
            row = db_session.execute(
                select(Study, Project.owner_id)
                .join(Project, Project.id == Study.project_id)
                .where(Study.slug == slug)
                .limit(1)
            ).first()

            # 하위호환: URL 파라미터가 숫자면 study.id로 재조회
            if not row and slug.isdigit():
                row = db_session.execute(
                    select(Study, Project.owner_id)
                    .join(Project, Project.id == Study.project_id)
                    .where(Study.id == int(slug))
                    .limit(1)
                ).first()
    except Exception as e:
        print(f"[ERROR] Study 조회 실패: slug={slug}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'error': '연구 조회 중 오류가 발생했습니다.'}), 500

    if not row:
        abort(404)

    study, owner_id = row
    if owner_id is not None and str(owner_id) not in allowed_owner_ids:
        print(f"[WARN] 접근 권한 없음 - study by slug: slug={slug}, user_id={user_id_header}, owner_id={owner_id}")
        return jsonify({'error': '접근 권한이 없습니다.'}), 403

    study_payload = {
        'id': study.id,
        'project_id': study.project_id,
        'name': study.name,
        'slug': study.slug,
        'initial_input': study.initial_input,
        'keywords': study.keywords,
        'methodologies': study.methodologies,
        'participant_count': study.participant_count,
        'start_date': study.start_date.isoformat() if study.start_date else None,
        'end_date': study.end_date.isoformat() if study.end_date else None,
        'timeline': study.timeline,
        'budget': study.budget,
        'target_audience': study.target_audience,
        'additional_requirements': study.additional_requirements,
        'created_at': study.created_at.isoformat() if study.created_at else None,
        'updated_at': study.updated_at.isoformat() if study.updated_at else None,
        'projects': {'owner_id': owner_id},
    }
    return jsonify(study_payload), 200


@study_bp.route('/projects/by-slug/<string:slug>', methods=['GET'])
@tier_required(['free'])
def get_project_by_slug(slug: str):
    """Slug로 Project 레코드를 조회 (숫자 ID 입력 시 하위호환 조회 지원)"""
    if session_scope is None:
        abort(500, description='Database session is not initialized')
    
    user_id_int = _get_user_id_from_request()
    if user_id_int is None:
        return jsonify({'error': '사용자 인증이 필요합니다.'}), 401

    owner_ids, _team_id = get_owner_ids_for_request(user_id_int)

    try:
        with session_scope() as db_session:
            project = db_session.execute(
                select(Project).where(Project.slug == slug).limit(1)
            ).scalar_one_or_none()

            # 하위호환: URL 파라미터가 숫자면 project.id로 재조회
            if project is None and slug.isdigit():
                project = db_session.execute(
                    select(Project).where(Project.id == int(slug)).limit(1)
                ).scalar_one_or_none()
    except Exception as e:
        print(f"[ERROR] Project 조회 실패: slug={slug}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'error': '프로젝트 조회 중 오류가 발생했습니다.'}), 500

    if not project:
        return jsonify({'error': '프로젝트를 찾을 수 없거나 접근 권한이 없습니다.'}), 404

    owner_id = project.owner_id
    allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}

    if owner_id is not None and str(owner_id) not in allowed_owner_ids:
        return jsonify({'error': '접근 권한이 없습니다.'}), 403

    project_payload = {
        'id': project.id,
        'owner_id': project.owner_id,
        'name': project.name,
        'slug': project.slug,
        'product_url': project.product_url,
        'keywords': project.keywords,
        'created_at': project.created_at.isoformat() if project.created_at else None,
        'updated_at': project.updated_at.isoformat() if project.updated_at else None,
    }
    return jsonify(project_payload), 200
