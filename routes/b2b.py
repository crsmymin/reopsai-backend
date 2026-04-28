"""
B2B(Enterprise) team management routes.
Extracted from backend/app.py to keep the main app file smaller.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity
from sqlalchemy import and_, func, select, update
from werkzeug.security import generate_password_hash

from api_logger import log_error
from db.engine import session_scope
from db.models.core import Company, Team, TeamMember, User
from routes.auth import get_primary_team_id_for_user, tier_required


b2b_bp = Blueprint("b2b", __name__, url_prefix="/api/b2b")
DEFAULT_ENTERPRISE_PASSWORD = "0000"


def _serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _get_identity_int():
    identity = get_jwt_identity()
    try:
        return int(identity) if identity is not None else None
    except Exception:
        return identity


def _to_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_team_id_for_enterprise(db_session, user_id_int):
    claims = get_jwt()
    team_id = claims.get("team_id")
    if team_id:
        active_team_id = db_session.execute(
            select(Team.id)
            .where(Team.id == int(team_id), Team.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()
        if active_team_id:
            return active_team_id
    return get_primary_team_id_for_user(db_session, user_id_int)


def _company_name_for(db_session, company_id):
    if not company_id:
        return None
    return db_session.execute(
        select(Company.name).where(Company.id == int(company_id)).limit(1)
    ).scalar_one_or_none()


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
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
            ).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404

            owner_id = team.owner_id
            company_name = _company_name_for(db_session, team.company_id)
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
                        "account_type": (user_info.account_type if user_info and user_info.account_type else "individual"),
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
                        "account_type": (owner_info.account_type if owner_info and owner_info.account_type else "individual"),
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
                        "company_id": team.company_id,
                        "company_name": company_name,
                        "plan_code": team.plan_code or "starter",
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
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
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
                    .values(
                        tier="enterprise",
                        account_type="enterprise",
                        company_id=team.company_id,
                        company_name=_company_name_for(db_session, team.company_id),
                    )
                )
            except Exception as exc:
                log_error(exc, "B2B - 팀원 enterprise 등급 업데이트 실패")

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 팀원 추가 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>", methods=["PUT"])
@tier_required(["enterprise"])
def b2b_update_team_member(member_user_id: int):
    """
    [PUT] 현재 팀 owner가 멤버의 이름과 소속팀을 수정
    Body: { "name": "홍길동", "team_id": 12 }
    """
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401
        if int(member_user_id) == int(user_id_int):
            return jsonify({"success": False, "error": "본인 정보는 /api/auth/enterprise/profile에서 수정해주세요."}), 400

        data = request.get_json() or {}
        allowed_fields = {"name", "team_id"}
        unknown_fields = sorted(set(data.keys()) - allowed_fields)
        if unknown_fields:
            return jsonify({"success": False, "error": f"수정할 수 없는 필드입니다: {unknown_fields}"}), 400
        if not any(field in data for field in allowed_fields):
            return jsonify({"success": False, "error": "수정할 name 또는 team_id가 필요합니다."}), 400

        with session_scope() as db_session:
            team_id = _get_team_id_for_enterprise(db_session, user_id_int)
            if not team_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 팀이 없습니다."}), 404

            owner_team = db_session.execute(
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
            ).scalar_one_or_none()
            if not owner_team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404
            if owner_team.owner_id != user_id_int:
                return jsonify({"success": False, "error": "멤버 정보 수정은 팀 owner만 가능합니다."}), 403
            if not owner_team.company_id:
                return jsonify({"success": False, "error": "팀 회사 정보가 없어 멤버를 수정할 수 없습니다."}), 400

            target_user = db_session.execute(
                select(User).where(User.id == int(member_user_id)).limit(1)
            ).scalar_one_or_none()
            if not target_user:
                return jsonify({"success": False, "error": "수정할 멤버를 찾을 수 없습니다."}), 404
            if (target_user.account_type or "") != "enterprise":
                return jsonify({"success": False, "error": "기업 계정 멤버만 수정할 수 있습니다."}), 400
            if target_user.company_id != owner_team.company_id:
                return jsonify({"success": False, "error": "같은 회사 소속 멤버만 수정할 수 있습니다."}), 403

            current_membership = db_session.execute(
                select(TeamMember)
                .join(Team, Team.id == TeamMember.team_id)
                .where(
                    TeamMember.user_id == int(member_user_id),
                    Team.company_id == owner_team.company_id,
                    Team.status != "deleted",
                )
                .order_by((Team.id == int(team_id)).desc(), TeamMember.joined_at.asc())
                .limit(1)
            ).scalar_one_or_none()
            if not current_membership:
                return jsonify({"success": False, "error": "같은 회사의 활성 팀에 속한 멤버만 수정할 수 있습니다."}), 403
            if (current_membership.role or "").strip().lower() == "owner" or owner_team.owner_id == int(member_user_id):
                return jsonify({"success": False, "error": "owner 계정은 이 API로 수정할 수 없습니다."}), 400

            if "name" in data:
                name = (data.get("name") or "").strip()
                if not name:
                    return jsonify({"success": False, "error": "name은 비워둘 수 없습니다."}), 400
                target_user.name = name

            target_team = db_session.execute(
                select(Team)
                .where(Team.id == current_membership.team_id, Team.status != "deleted")
                .limit(1)
            ).scalar_one_or_none()
            if "team_id" in data:
                target_team_id = _to_int_or_none(data.get("team_id"))
                if target_team_id is None:
                    return jsonify({"success": False, "error": "team_id가 올바르지 않습니다."}), 400

                target_team = db_session.execute(
                    select(Team)
                    .where(
                        Team.id == int(target_team_id),
                        Team.status != "deleted",
                        Team.company_id == owner_team.company_id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if not target_team:
                    return jsonify({"success": False, "error": "같은 회사의 활성 팀으로만 이동할 수 있습니다."}), 404
                if target_team.owner_id == int(member_user_id):
                    return jsonify({"success": False, "error": "대상 멤버가 owner인 팀으로는 이동할 수 없습니다."}), 400

                same_company_team_ids = select(Team.id).where(
                    Team.company_id == owner_team.company_id,
                    Team.status != "deleted",
                )
                db_session.execute(
                    TeamMember.__table__.delete().where(
                        and_(
                            TeamMember.user_id == int(member_user_id),
                            TeamMember.team_id.in_(same_company_team_ids),
                            TeamMember.team_id != int(target_team_id),
                        )
                    )
                )

                new_membership = db_session.execute(
                    select(TeamMember)
                    .where(
                        TeamMember.user_id == int(member_user_id),
                        TeamMember.team_id == int(target_team_id),
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if new_membership:
                    new_membership.role = "member"
                else:
                    db_session.add(TeamMember(team_id=int(target_team_id), user_id=int(member_user_id), role="member"))

            target_user.company_id = owner_team.company_id
            if not target_user.company_name:
                target_user.company_name = _company_name_for(db_session, owner_team.company_id)
            db_session.flush()

            team_role = "owner" if target_team and target_team.owner_id == int(member_user_id) else "member"
            user_payload = {
                "id": target_user.id,
                "email": target_user.email,
                "name": target_user.name,
                "team_id": target_team.id if target_team else current_membership.team_id,
                "team_name": target_team.name if target_team else None,
                "team_role": team_role,
            }

        return jsonify(
            {
                "success": True,
                "message": "멤버 정보가 수정되었습니다.",
                "user": user_payload,
            }
        ), 200
    except Exception as e:
        log_error(e, "B2B - 멤버 정보 수정 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>/reset-password", methods=["POST"])
@tier_required(["enterprise"])
def b2b_reset_team_member_password(member_user_id: int):
    """[POST] 현재 팀 owner가 같은 회사 활성 팀 멤버의 비밀번호를 0000으로 초기화"""
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401
        if int(member_user_id) == int(user_id_int):
            return jsonify({"success": False, "error": "본인 비밀번호는 이 API로 초기화할 수 없습니다."}), 400

        with session_scope() as db_session:
            team_id = _get_team_id_for_enterprise(db_session, user_id_int)
            if not team_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 팀이 없습니다."}), 404

            owner_team = db_session.execute(
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
            ).scalar_one_or_none()
            if not owner_team:
                return jsonify({"success": False, "error": "팀 정보를 찾을 수 없습니다."}), 404
            if owner_team.owner_id != user_id_int:
                return jsonify({"success": False, "error": "비밀번호 초기화는 팀 owner만 가능합니다."}), 403
            if not owner_team.company_id:
                return jsonify({"success": False, "error": "팀 회사 정보가 없어 멤버를 관리할 수 없습니다."}), 409

            target_user = db_session.execute(
                select(User).where(User.id == int(member_user_id)).limit(1)
            ).scalar_one_or_none()
            if not target_user:
                return jsonify({"success": False, "error": "대상 사용자를 찾을 수 없습니다."}), 404
            if (target_user.tier or "").strip().lower() == "super":
                return jsonify({"success": False, "error": "super 계정은 초기화할 수 없습니다."}), 403
            if (target_user.account_type or "") != "enterprise":
                return jsonify({"success": False, "error": "기업 계정 멤버만 초기화할 수 있습니다."}), 400
            if target_user.company_id != owner_team.company_id:
                return jsonify({"success": False, "error": "같은 회사 소속 멤버만 초기화할 수 있습니다."}), 403

            membership = db_session.execute(
                select(TeamMember)
                .join(Team, Team.id == TeamMember.team_id)
                .where(
                    TeamMember.user_id == int(member_user_id),
                    Team.company_id == owner_team.company_id,
                    Team.status != "deleted",
                )
                .order_by((Team.id == int(team_id)).desc(), TeamMember.joined_at.asc())
                .limit(1)
            ).scalar_one_or_none()
            if not membership:
                return jsonify({"success": False, "error": "같은 회사의 활성 팀 멤버만 초기화할 수 있습니다."}), 403
            if (membership.role or "").strip().lower() == "owner" or owner_team.owner_id == int(member_user_id):
                return jsonify({"success": False, "error": "owner 계정은 이 API로 초기화할 수 없습니다."}), 403

            member_team = db_session.execute(
                select(Team)
                .where(
                    Team.id == int(membership.team_id),
                    Team.company_id == owner_team.company_id,
                    Team.status != "deleted",
                )
                .limit(1)
            ).scalar_one_or_none()
            if not member_team:
                return jsonify({"success": False, "error": "삭제되었거나 비활성화된 팀 소속 멤버는 초기화할 수 없습니다."}), 409

            target_user.password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
            target_user.password_reset_required = True
            db_session.flush()

            user_payload = {
                "id": target_user.id,
                "email": target_user.email,
                "name": target_user.name,
                "team_id": member_team.id,
                "team_name": member_team.name,
                "team_role": "member",
                "password_reset_required": True,
            }

        return jsonify(
            {
                "success": True,
                "message": "비밀번호가 초기화되었습니다.",
                "temporary_password": DEFAULT_ENTERPRISE_PASSWORD,
                "user": user_payload,
            }
        ), 200
    except Exception as e:
        log_error(e, "B2B - 멤버 비밀번호 초기화 실패")
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
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
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
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
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
                        TeamMember.user_id != member_user_id,
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
                    .values(tier="enterprise", account_type="enterprise")
                )
            except Exception as exc:
                log_error(exc, "B2B - 소유자 enterprise 등급 업데이트 실패")

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 팀원 역할 변경 실패")
        return jsonify({"success": False, "error": str(e)}), 500
