"""
요청(Request) 관련 유틸리티.

사용자 ID 추출 및 소유자 ID 해석 기능을 제공합니다.
"""
from flask import request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt


def _extract_request_user_id():
    user_id_header = request.headers.get('X-User-ID')
    if not user_id_header:
        return None, jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401
    try:
        return int(user_id_header), None, None
    except Exception:
        return user_id_header, None, None


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
            if not team_id and token_user_id:
                team_id = WorkspaceRepository.get_primary_team_id_for_user(db_session, int(token_user_id))
            if team_id:
                member_ids = WorkspaceRepository.get_team_member_ids(db_session, int(team_id))
                if token_user_id and token_user_id not in member_ids:
                    member_ids.append(int(token_user_id))
                if member_ids:
                    owner_ids = member_ids

    return owner_ids
