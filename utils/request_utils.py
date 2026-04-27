"""
요청(Request) 관련 유틸리티.

사용자 ID 추출 및 소유자 ID 해석 기능을 제공합니다.
"""
from flask import request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt


def _extract_request_user_id():
    """요청에서 사용자 ID를 추출합니다.
    
    우선순위:
    1. X-User-ID 헤더 (프론트엔드에서 명시적으로 전달)
    2. JWT 토큰의 identity (쿠키 인증 시 새로고침 후 헤더가 없는 경우)
    """
    user_id_header = request.headers.get('X-User-ID')
    if user_id_header:
        try:
            return int(user_id_header), None, None
        except Exception:
            return user_id_header, None, None

    # Fallback: JWT 토큰에서 사용자 ID 추출 (쿠키 인증 시 새로고침 후)
    try:
        identity = get_jwt_identity()
        if identity is not None:
            try:
                return int(identity), None, None
            except Exception:
                return identity, None, None
    except Exception:
        pass

    return None, jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401


def _resolve_owner_ids_sqlalchemy(user_id_int):
    owner_ids = [user_id_int]
    try:
        claims = get_jwt() or {}
    except Exception:
        claims = {}

    identity = get_jwt_identity()
    try:
        token_user_id = int(identity) if identity is not None else None
    except Exception:
        token_user_id = None

    tier = claims.get('tier')
    team_id = claims.get('team_id')

    try:
        from db.engine import session_scope
        from db.repositories.workspace_repository import WorkspaceRepository
    except Exception:
        return owner_ids

    if tier == 'enterprise' and session_scope and WorkspaceRepository:
        with session_scope() as db_session:
            if team_id:
                try:
                    from db.models.core import Team
                    from sqlalchemy import select
                    team_id = db_session.execute(
                        select(Team.id).where(Team.id == int(team_id), Team.status != 'deleted').limit(1)
                    ).scalar_one_or_none()
                except Exception:
                    team_id = None
            if not team_id and token_user_id:
                team_id = WorkspaceRepository.get_primary_team_id_for_user(db_session, int(token_user_id))
            if team_id:
                member_ids = WorkspaceRepository.get_team_member_ids(db_session, int(team_id))
                if token_user_id and token_user_id not in member_ids:
                    member_ids.append(int(token_user_id))
                if member_ids:
                    owner_ids = member_ids

    return owner_ids
