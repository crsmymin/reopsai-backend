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


def _resolve_workspace_owner_ids(user_id_int):
    """
    기본 워크스페이스 권한 범위를 반환합니다.

    현재 정책은 각 사용자가 직접 생성한 프로젝트/스터디/아티팩트만 접근하는
    개인 소유 모델입니다. 추후 명시적 project/study 공유 기능이 생기면 이
    헬퍼에서 공유 owner/resource 범위를 확장합니다.
    """
    return [user_id_int] if user_id_int is not None else []


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

    account_type = claims.get('account_type')
    company_id = claims.get('company_id')

    try:
        from db.engine import session_scope
    except Exception:
        return owner_ids

    if account_type == 'business' and company_id and session_scope:
        with session_scope() as db_session:
            try:
                from db.models.core import CompanyMember
                from sqlalchemy import select
                member_ids = db_session.execute(
                    select(CompanyMember.user_id).where(CompanyMember.company_id == int(company_id))
                ).scalars().all()
                if token_user_id and token_user_id not in member_ids:
                    member_ids.append(int(token_user_id))
                if member_ids:
                    owner_ids = member_ids
            except Exception:
                return owner_ids

    return owner_ids
