"""
B2B(Enterprise) team management routes.
Extracted from backend/app.py to keep the main app file smaller.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity
from sqlalchemy import and_, func, select, update

from api_logger import log_error
from db.engine import session_scope
from db.models.core import Team, TeamMember, User
from routes.auth import get_primary_team_id_for_user, tier_required


b2b_bp = Blueprint("b2b", __name__, url_prefix="/api/b2b")


def _serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _get_identity_int():
    identity = get_jwt_identity()
    try:
        return int(identity) if identity is not None else None
    except Exception:
        return identity


def _get_team_id_for_enterprise(db_session, user_id_int):
    claims = get_jwt()
    return claims.get("team_id") or get_primary_team_id_for_user(db_session, user_id_int)


@b2b_bp.route("/team", methods=["GET"])
@tier_required(["enterprise"])
def b2b_get_my_team():
    """
    [GET] 현재 엔터프라이즈 사용자의 팀 및 팀원 정보 조회
    - JWT의 team_id 또는 get_primary_team_id_for_user() 기반
    """
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        with session_scope() as db_session:
            team_id = _get_team_id_for_enterprise(db_session, user_id_int)
            if not team_id:
                return jsonify(
                    {
                        "success": False,
                        "error": "이 계정에 연결된 팀이 없습니다. Admin에게 문의해 팀을 설정해주세요.",
                    }
                ), 404

            team = db_session.execute(
                select(Team).where(Team.id == int(team_id)).limit(1)
            ).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404

            owner_id = team.owner_id
            member_rows = db_session.execute(
                select(TeamMember)
                .where(TeamMember.team_id == int(team_id))
                .order_by(TeamMember.joined_at.asc())
            ).scalars().all()

            member_user_ids = {
                row.user_id for row in member_rows if row.user_id is not None
            }
            if owner_id and owner_id not in member_user_ids:
                member_user_ids.add(owner_id)

            users_by_id = {}
            if member_user_ids:
                users = db_session.execute(
                    select(User).where(User.id.in_(list(member_user_ids)))
                ).scalars().all()
                users_by_id = {u.id: u for u in users}

            members = []
            for row in member_rows:
                uid = row.user_id
                user_info = users_by_id.get(uid)
                role = row.role or "member"
                is_owner = (uid == owner_id) or (role == "owner")
                members.append(
                    {
                        "user_id": uid,
                        "email": user_info.email if user_info else None,
                        "name": user_info.name if user_info else None,
                        "tier": (user_info.tier if user_info and user_info.tier else "free"),
                        "role": "owner" if is_owner else role,
                        "joined_at": _serialize_dt(row.joined_at),
                    }
                )

            if owner_id and owner_id not in [m["user_id"] for m in members]:
                owner_info = users_by_id.get(owner_id)
                members.insert(
                    0,
                    {
                        "user_id": owner_id,
                        "email": owner_info.email if owner_info else None,
                        "name": owner_info.name if owner_info else None,
                        "tier": (owner_info.tier if owner_info and owner_info.tier else "free"),
                        "role": "owner",
                        "joined_at": None,
                    },
                )

            return jsonify(
                {
                    "success": True,
                    "team": {
                        "id": team.id,
                        "name": team.name,
                        "description": team.description,
                        "status": team.status,
                        "owner_id": owner_id,
                        "created_at": _serialize_dt(team.created_at),
                    },
                    "members": members,
                }
            )
    except Exception as e:
        log_error(e, "B2B - 팀 정보 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members", methods=["POST"])
@tier_required(["enterprise"])
def b2b_add_team_member():
    """
    [POST] 현재 팀에 기존 사용자(이미 가입된 이메일)를 팀원으로 추가
    Body: { "email": "user@example.com", "role": "member" }
    """
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        role = data.get("role") or "member"
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        with session_scope() as db_session:
            team_id = _get_team_id_for_enterprise(db_session, user_id_int)
            if not team_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 팀이 없습니다."}), 404

            team = db_session.execute(
                select(Team).where(Team.id == int(team_id)).limit(1)
            ).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404

            owner_id = team.owner_id
            if owner_id != user_id_int:
                return (
                    jsonify({"success": False, "error": "팀원 추가 권한이 없습니다 (오너만 가능)."}),
                    403,
                )

            target_user = db_session.execute(
                select(User)
                .where(func.lower(User.email) == email)
                .limit(1)
            ).scalar_one_or_none()
            if not target_user:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "해당 이메일로 가입된 사용자가 없습니다. (초대 URL 기능은 추후 지원 예정입니다.)",
                        }
                    ),
                    404,
                )

            existing = db_session.execute(
                select(TeamMember.id)
                .where(
                    and_(
                        TeamMember.team_id == int(team_id),
                        TeamMember.user_id == target_user.id,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if existing:
                return jsonify({"success": True})

            db_session.add(
                TeamMember(team_id=int(team_id), user_id=target_user.id, role=role)
            )

            try:
                db_session.execute(
                    update(User)
                    .where(User.id == target_user.id)
                    .values(tier="enterprise")
                )
            except Exception as exc:
                log_error(exc, "B2B - 팀원 enterprise 등급 업데이트 실패")

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 팀원 추가 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def b2b_remove_team_member(member_user_id: int):
    """[DELETE] 팀원 삭제 (오너만 가능)"""
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        with session_scope() as db_session:
            team_id = _get_team_id_for_enterprise(db_session, user_id_int)
            if not team_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 팀이 없습니다."}), 404

            team = db_session.execute(
                select(Team).where(Team.id == int(team_id)).limit(1)
            ).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404

            owner_id = team.owner_id
            if owner_id != user_id_int:
                return jsonify({"success": False, "error": "팀원 삭제 권한이 없습니다 (오너만 가능)."}), 403

            if member_user_id == owner_id:
                return jsonify({"success": False, "error": "소유자는 삭제할 수 없습니다."}), 400

            db_session.execute(
                update(TeamMember)
                .where(
                    and_(
                        TeamMember.team_id == int(team_id),
                        TeamMember.user_id == member_user_id,
                    )
                )
                .values(role="member")
            )
            db_session.execute(
                TeamMember.__table__.delete().where(
                    and_(
                        TeamMember.team_id == int(team_id),
                        TeamMember.user_id == member_user_id,
                    )
                )
            )

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 팀원 삭제 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>/role", methods=["POST"])
@tier_required(["enterprise"])
def b2b_change_team_member_role(member_user_id: int):
    """
    [POST] 팀원 역할 변경 (현재는 owner로 승격만 지원)
    Body: { "role": "owner" }
    """
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        data = request.get_json() or {}
        new_role = (data.get("role") or "member").strip()
        if new_role != "owner":
            return jsonify({"success": False, "error": "현재는 소유자로 변경만 지원합니다."}), 400

        with session_scope() as db_session:
            team_id = _get_team_id_for_enterprise(db_session, user_id_int)
            if not team_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 팀이 없습니다."}), 404

            team = db_session.execute(
                select(Team).where(Team.id == int(team_id)).limit(1)
            ).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404

            owner_id = team.owner_id
            if owner_id != user_id_int:
                return jsonify({"success": False, "error": "권한 변경은 소유자만 가능합니다."}), 403

            if member_user_id == owner_id:
                return jsonify({"success": False, "error": "본인을 대상으로 권한을 변경할 수 없습니다."}), 400

            member = db_session.execute(
                select(TeamMember)
                .where(
                    and_(
                        TeamMember.team_id == int(team_id),
                        TeamMember.user_id == member_user_id,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if member:
                member.role = "owner"
            else:
                db_session.add(
                    TeamMember(team_id=int(team_id), user_id=member_user_id, role="owner")
                )

            db_session.execute(
                update(TeamMember)
                .where(
                    and_(
                        TeamMember.team_id == int(team_id),
                        TeamMember.user_id == owner_id,
                    )
                )
                .values(role="member")
            )

            db_session.execute(
                update(Team)
                .where(Team.id == int(team_id))
                .values(owner_id=member_user_id)
            )

            try:
                db_session.execute(
                    update(User)
                    .where(User.id == member_user_id)
                    .values(tier="enterprise")
                )
            except Exception as exc:
                log_error(exc, "B2B - 소유자 enterprise 등급 업데이트 실패")

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 팀원 역할 변경 실패")
        return jsonify({"success": False, "error": str(e)}), 500
