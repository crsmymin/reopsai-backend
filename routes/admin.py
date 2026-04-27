"""
Admin 전용 API 라우트
"""

import traceback
from datetime import datetime
import math

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import and_, func, or_, select, update
from werkzeug.security import generate_password_hash

from db.engine import session_scope
from db.models.core import Artifact, Project, Study, Team, TeamMember, TeamUsageEvent, User, UserFeedback
from routes.auth import get_primary_team_id_for_user, tier_required


admin_bp = Blueprint("admin", __name__)
ALLOWED_PLAN_CODES = {"starter", "pro", "enterprise_plus"}
ALLOWED_USER_PLAN_CODES = {"free", "basic", "premium"}
USER_PLAN_CODE_ALIASES = {
    "starter": "free",
    "pro": "basic",
    "enterprise_plus": "premium",
}
DEFAULT_ENTERPRISE_PASSWORD = "0000"


def log_error(error, context=""):
    """에러 로깅"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] ❌ 에러 발생: {context}")
    print(f"에러 내용: {str(error)}")
    traceback.print_exc()


def _serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _to_int_or_none(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _ensure_db():
    if session_scope is None:
        return False
    return True


def _parse_iso_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _pagination_params():
    page = _to_int_or_none(request.args.get("page")) or 1
    per_page = _to_int_or_none(request.args.get("per_page")) or 20
    page = max(1, page)
    per_page = max(1, min(100, per_page))
    return page, per_page


def _validate_plan_code(plan_code, *, required=False):
    value = (plan_code or "").strip().lower()
    if not value:
        if required:
            return None
        return None
    return value if value in ALLOWED_PLAN_CODES else None


def _validate_user_plan_code(plan_code, *, required=False):
    value = (plan_code or "").strip().lower()
    if not value:
        if required:
            return None
        return None
    value = USER_PLAN_CODE_ALIASES.get(value, value)
    return value if value in ALLOWED_USER_PLAN_CODES else None


def _user_auth_type(user: User):
    account_type = user.account_type or "individual"
    if account_type == "enterprise":
        return "enterprise"
    if user.google_id:
        return "google"
    return "individual"


def _account_list_payload(db_session, user: User):
    owner_team = db_session.execute(
        select(Team).where(Team.owner_id == user.id).order_by(Team.created_at.asc()).limit(1)
    ).scalar_one_or_none()
    member_row = None
    member_team = None
    if owner_team is None:
        member_row = db_session.execute(
            select(TeamMember)
            .where(TeamMember.user_id == user.id)
            .order_by(TeamMember.joined_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if member_row:
            member_team = db_session.execute(
                select(Team).where(Team.id == member_row.team_id).limit(1)
            ).scalar_one_or_none()

    team = owner_team or member_team
    team_role = "owner" if owner_team else ("member" if member_row else None)
    owner = None
    if team and team.owner_id:
        owner = db_session.execute(
            select(User).where(User.id == team.owner_id).limit(1)
        ).scalar_one_or_none()

    account_type = user.account_type or "individual"
    auth_type = _user_auth_type(user)
    company_name = user.company_name or (owner.company_name if owner else None)
    plan_code = team.plan_code if team else (user.tier or "free")
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "company_name": company_name,
        "plan_code": plan_code,
        "account_type": account_type,
        "auth_type": auth_type,
        "team_role": team_role,
        "is_owner": team_role == "owner",
        "team_id": team.id if team else None,
        "team_name": team.name if team else None,
        "created_at": _serialize_dt(user.created_at),
    }


def _team_payload(db_session, team: Team, owner: User = None):
    if owner is None and team.owner_id is not None:
        owner = db_session.execute(
            select(User).where(User.id == team.owner_id).limit(1)
        ).scalar_one_or_none()

    member_count = (
        db_session.execute(
            select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)
        ).scalar_one()
        or 0
    )
    if team.owner_id:
        owner_is_member = db_session.execute(
            select(TeamMember.id)
            .where(and_(TeamMember.team_id == team.id, TeamMember.user_id == team.owner_id))
            .limit(1)
        ).scalar_one_or_none()
        if owner_is_member is None:
            member_count += 1

    company_name = owner.company_name if owner else None
    return {
        "id": team.id,
        "team_name": team.name,
        "description": team.description,
        "plan_code": team.plan_code or "starter",
        "enterprise_name": company_name,
        "company_name": company_name,
        "owner_email": owner.email if owner else None,
        "enterprise_account_id": team.owner_id,
        "owner_id": team.owner_id,
        "member_count": int(member_count),
        "created_at": _serialize_dt(team.created_at),
    }


@admin_bp.route("/api/admin/enterprise/accounts", methods=["GET"])
@tier_required(["super"])
def list_enterprise_accounts():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        search = (request.args.get("search") or "").strip()
        plan_code_raw = (request.args.get("plan_code") or "").strip().lower()
        account_list_plan_codes = ALLOWED_PLAN_CODES | ALLOWED_USER_PLAN_CODES
        plan_code = plan_code_raw if plan_code_raw in account_list_plan_codes else None
        account_type = (request.args.get("account_type") or "").strip().lower()
        team_role = (request.args.get("team_role") or "").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": "유효하지 않은 plan_code입니다."}), 400
        if account_type and account_type not in {"google", "enterprise", "individual"}:
            return jsonify({"success": False, "error": "유효하지 않은 account_type입니다."}), 400
        if team_role and team_role not in {"owner", "member"}:
            return jsonify({"success": False, "error": "유효하지 않은 team_role입니다."}), 400
        page, per_page = _pagination_params()

        with session_scope() as db_session:
            query = select(User).order_by(User.created_at.desc())
            if search:
                pattern = f"%{search.lower()}%"
                query = query.where(
                    or_(
                        func.lower(User.email).like(pattern),
                        func.lower(User.name).like(pattern),
                        func.lower(User.company_name).like(pattern),
                        User.id.in_(
                            select(TeamMember.user_id)
                            .join(Team, Team.id == TeamMember.team_id)
                            .join(User, User.id == Team.owner_id)
                            .where(func.lower(User.company_name).like(pattern))
                        ),
                    )
                )
            if account_type == "enterprise":
                query = query.where(User.account_type == "enterprise")
            elif account_type == "individual":
                query = query.where(User.account_type != "enterprise")
            elif account_type == "google":
                query = query.where(User.account_type != "enterprise", User.google_id.is_not(None))

            users = db_session.execute(query).scalars().all()
            accounts = []
            for user in users:
                payload = _account_list_payload(db_session, user)
                if plan_code and payload["plan_code"] != plan_code:
                    continue
                if team_role and payload["team_role"] != team_role:
                    continue
                accounts.append(payload)
            total_count = len(accounts)
            start = (page - 1) * per_page
            end = start + per_page
            payload = accounts[start:end]

        return jsonify(
            {
                "accounts": payload,
                "total_count": total_count,
                "total_pages": math.ceil(total_count / per_page) if total_count else 0,
                "current_page": page,
            }
        ), 200
    except Exception as e:
        log_error(e, "Admin - 기업 계정 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/accounts", methods=["POST"])
@tier_required(["super"])
def create_enterprise_account():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip()
        company_name = (data.get("company_name") or "").strip()
        plan_code = _validate_plan_code(data.get("plan_code"), required=True)
        team_name = (data.get("team_name") or "").strip()
        team_description = (data.get("team_description") or data.get("description") or "").strip()

        if not all([email, name, company_name, plan_code, team_name]):
            return jsonify({"success": False, "error": "email, name, company_name, plan_code, team_name은 필수입니다."}), 400

        with session_scope() as db_session:
            exists = db_session.execute(
                select(User.id).where(func.lower(User.email) == email).limit(1)
            ).scalar_one_or_none()
            if exists:
                return jsonify({"success": False, "error": "이미 존재하는 이메일입니다."}), 409

            user = User(
                email=email,
                name=name,
                company_name=company_name,
                tier="enterprise",
                account_type="enterprise",
                password_hash=generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD),
                password_reset_required=True,
            )
            db_session.add(user)
            db_session.flush()
            db_session.refresh(user)

            team = Team(
                owner_id=user.id,
                name=team_name,
                description=team_description or None,
                status="active",
                plan_code=plan_code,
            )
            db_session.add(team)
            db_session.flush()
            db_session.refresh(team)
            db_session.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))

            account_payload = {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "company_name": user.company_name,
                "account_type": user.account_type,
                "tier": user.tier,
                "plan_code": team.plan_code,
                "password_reset_required": bool(user.password_reset_required),
            }
            team_payload = {
                "id": team.id,
                "team_name": team.name,
                "description": team.description,
                "plan_code": team.plan_code,
                "owner_id": team.owner_id,
            }

        return jsonify({"success": True, "account": account_payload, "team": team_payload}), 201
    except Exception as e:
        log_error(e, "Admin - 기업 계정 생성")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/accounts/<int:account_id>", methods=["PUT"])
@tier_required(["super"])
def update_enterprise_account(account_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        has_name = "name" in data
        has_company_name = "company_name" in data
        has_plan_code = "plan_code" in data
        if not has_name and not has_company_name and not has_plan_code:
            return jsonify({"success": False, "error": "수정할 name, company_name 또는 plan_code가 필요합니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == account_id).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "계정을 찾을 수 없습니다."}), 404

            auth_type = _user_auth_type(user)
            plan_code = None
            if has_plan_code:
                if auth_type != "google":
                    return jsonify({"success": False, "error": "Google SSO 계정만 이 API에서 플랜을 변경할 수 있습니다."}), 403
                if (user.tier or "").strip().lower() not in ALLOWED_USER_PLAN_CODES:
                    return jsonify({"success": False, "error": "free/basic/premium Google SSO 계정만 플랜을 변경할 수 있습니다."}), 403
                plan_code = _validate_user_plan_code(data.get("plan_code"), required=True)
                if not plan_code:
                    allowed = sorted(ALLOWED_USER_PLAN_CODES | set(USER_PLAN_CODE_ALIASES.keys()))
                    return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {allowed}"}), 400

            if has_name:
                user.name = (data.get("name") or "").strip() or None
            if has_company_name:
                user.company_name = (data.get("company_name") or "").strip() or None
            if plan_code:
                user.tier = plan_code
            db_session.flush()

            account_payload = _account_list_payload(db_session, user)

        return jsonify({"success": True, "account": account_payload}), 200
    except Exception as e:
        log_error(e, f"Admin - 계정 수정 (account_id: {account_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/accounts/<int:account_id>/reset-password", methods=["POST"])
@tier_required(["super"])
def reset_enterprise_account_password(account_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == account_id).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "계정을 찾을 수 없습니다."}), 404
            user.password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
            user.password_reset_required = True

        return jsonify({"success": True, "message": "비밀번호가 0000으로 초기화되었습니다. 오너에게 알려주세요."}), 200
    except Exception as e:
        log_error(e, f"Admin - 기업 계정 비밀번호 초기화 (account_id: {account_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams", methods=["GET"])
@tier_required(["super"])
def list_admin_teams():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        search = (request.args.get("search") or "").strip()
        plan_code = _validate_plan_code(request.args.get("plan_code"))
        enterprise_account_id = _to_int_or_none(request.args.get("enterprise_account_id"))
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400
        if request.args.get("enterprise_account_id") and enterprise_account_id is None:
            return jsonify({"success": False, "error": "enterprise_account_id가 올바르지 않습니다."}), 400
        page, per_page = _pagination_params()

        with session_scope() as db_session:
            query = select(Team).order_by(Team.created_at.desc())
            if search:
                query = query.where(func.lower(Team.name).like(f"%{search.lower()}%"))
            if plan_code:
                query = query.where(Team.plan_code == plan_code)
            if enterprise_account_id is not None:
                query = query.where(Team.owner_id == enterprise_account_id)

            teams = db_session.execute(query).scalars().all()
            total_count = len(teams)
            start = (page - 1) * per_page
            end = start + per_page
            payload = [_team_payload(db_session, team) for team in teams[start:end]]

        return jsonify(
            {
                "teams": payload,
                "total_count": total_count,
                "total_pages": math.ceil(total_count / per_page) if total_count else 0,
                "current_page": page,
            }
        ), 200
    except Exception as e:
        log_error(e, "Admin - 팀 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams", methods=["POST"])
@tier_required(["super"])
def create_admin_team():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        enterprise_account_id = _to_int_or_none(data.get("enterprise_account_id"))
        team_name = (data.get("team_name") or "").strip()
        description = (data.get("description") or "").strip()
        requested_plan = _validate_plan_code(data.get("plan_code"))

        if enterprise_account_id is None or not team_name:
            return jsonify({"success": False, "error": "enterprise_account_id와 team_name은 필수입니다."}), 400
        if data.get("plan_code") and not requested_plan:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400

        with session_scope() as db_session:
            owner = db_session.execute(
                select(User).where(User.id == enterprise_account_id).limit(1)
            ).scalar_one_or_none()
            if not owner:
                return jsonify({"success": False, "error": "기업 계정을 찾을 수 없습니다."}), 404
            if owner.account_type != "enterprise":
                return jsonify({"success": False, "error": "enterprise 계정만 팀 owner로 지정할 수 있습니다."}), 400

            inherited_plan = db_session.execute(
                select(Team.plan_code).where(Team.owner_id == enterprise_account_id).order_by(Team.created_at.asc()).limit(1)
            ).scalar_one_or_none()
            plan_code = requested_plan or inherited_plan or "starter"

            team = Team(
                owner_id=enterprise_account_id,
                name=team_name,
                description=description or None,
                status="active",
                plan_code=plan_code,
            )
            db_session.add(team)
            db_session.flush()
            db_session.refresh(team)
            db_session.add(TeamMember(team_id=team.id, user_id=enterprise_account_id, role="owner"))
            db_session.flush()

            team_payload = _team_payload(db_session, team, owner)

        return jsonify({"success": True, "team": team_payload}), 201
    except Exception as e:
        log_error(e, "Admin - 팀 생성")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users", methods=["GET"])
@tier_required(["super"])
def get_all_users_with_tier():
    """모든 사용자 조회 (tier 정보 및 통계 포함) - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            users = db_session.execute(
                select(User).order_by(User.created_at.desc())
            ).scalars().all()

            payload_users = []
            for user in users:
                project_ids = db_session.execute(
                    select(Project.id).where(Project.owner_id == user.id)
                ).scalars().all()
                project_count = len(project_ids)

                if project_ids:
                    study_ids = db_session.execute(
                        select(Study.id).where(Study.project_id.in_(project_ids))
                    ).scalars().all()
                else:
                    study_ids = []

                study_count = len(study_ids)
                plan_count = 0
                guideline_count = 0
                screener_count = 0

                if study_ids:
                    plan_count = (
                        db_session.execute(
                            select(func.count())
                            .select_from(Artifact)
                            .where(
                                and_(
                                    Artifact.study_id.in_(study_ids),
                                    Artifact.artifact_type == "plan",
                                )
                            )
                        ).scalar_one()
                        or 0
                    )
                    guideline_count = (
                        db_session.execute(
                            select(func.count())
                            .select_from(Artifact)
                            .where(
                                and_(
                                    Artifact.study_id.in_(study_ids),
                                    Artifact.artifact_type == "guideline",
                                )
                            )
                        ).scalar_one()
                        or 0
                    )
                    screener_count = (
                        db_session.execute(
                            select(func.count())
                            .select_from(Artifact)
                            .where(
                                and_(
                                    Artifact.study_id.in_(study_ids),
                                    Artifact.artifact_type == "survey",
                                )
                            )
                        ).scalar_one()
                        or 0
                    )

                tier = user.tier or "free"
                enterprise_team_id = None
                enterprise_team_name = None
                if tier == "enterprise":
                    try:
                        team_id = get_primary_team_id_for_user(db_session, user.id)
                        if team_id:
                            team = db_session.execute(
                                select(Team).where(Team.id == int(team_id)).limit(1)
                            ).scalar_one_or_none()
                            if team:
                                enterprise_team_id = team.id
                                enterprise_team_name = team.name
                    except Exception as exc:
                        log_error(exc, f"Admin - 엔터프라이즈 팀 정보 조회 실패 (user_id: {user.id})")

                payload_users.append(
                    {
                        "id": user.id,
                        "email": user.email,
                        "tier": tier,
                        "account_type": user.account_type or "individual",
                        "password_reset_required": bool(user.password_reset_required),
                        "created_at": _serialize_dt(user.created_at),
                        "google_id": user.google_id,
                        "project_count": project_count,
                        "study_count": study_count,
                        "plan_count": int(plan_count),
                        "guideline_count": int(guideline_count),
                        "screener_count": int(screener_count),
                        "enterprise_team_id": enterprise_team_id,
                        "enterprise_team_name": enterprise_team_name,
                        "enterprise_plan_code": (
                            db_session.execute(
                                select(Team.plan_code).where(Team.id == enterprise_team_id).limit(1)
                            ).scalar_one_or_none()
                            if enterprise_team_id
                            else None
                        ),
                    }
                )

        return jsonify({"success": True, "users": payload_users, "count": len(payload_users)})
    except Exception as e:
        log_error(e, "Admin - 사용자 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/tier", methods=["PUT"])
@tier_required(["super"])
def update_user_tier(user_id):
    """사용자 tier 변경 - admin 전용"""
    try:
        data = request.json or {}
        new_tier = (data.get("tier") or "").strip().lower()
        if new_tier == "admin":
            new_tier = "super"

        valid_tiers = ["free", "basic", "premium", "enterprise", "super"]
        if new_tier not in valid_tiers:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 tier입니다. 가능한 값: {valid_tiers}",
                }
            ), 400

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
            user.tier = new_tier

            payload = {
                "id": user.id,
                "email": user.email,
                "tier": user.tier,
                "created_at": _serialize_dt(user.created_at),
                "google_id": user.google_id,
            }

        return jsonify(
            {
                "success": True,
                "message": f"사용자 tier가 {new_tier}로 변경되었습니다.",
                "user": payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 사용자 tier 변경 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise", methods=["GET"])
@tier_required(["super"])
def get_user_enterprise_info(user_id):
    """
    특정 사용자의 엔터프라이즈/B2B 관련 정보 조회 (admin 전용)
    - tier
    - 대표 team_id
    - owner로 있는 팀 목록
    - member로 속한 팀 목록
    """
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            tier = user.tier or "free"
            primary_team_id = None
            try:
                primary_team_id = get_primary_team_id_for_user(db_session, user_id_int)
            except Exception as exc:
                log_error(exc, "Admin - 대표 팀 ID 조회 실패")

            owner_teams = db_session.execute(
                select(Team).where(Team.owner_id == user_id_int)
            ).scalars().all()
            owner_teams_payload = [
                {
                    "id": team.id,
                    "name": team.name,
                    "description": team.description,
                    "status": team.status,
                    "plan_code": team.plan_code,
                    "created_at": _serialize_dt(team.created_at),
                    "updated_at": _serialize_dt(team.updated_at),
                }
                for team in owner_teams
            ]

            member_rows = db_session.execute(
                select(TeamMember).where(TeamMember.user_id == user_id_int)
            ).scalars().all()
            member_team_ids = list({row.team_id for row in member_rows if row.team_id})

            teams_by_id = {}
            if member_team_ids:
                member_teams = db_session.execute(
                    select(Team).where(Team.id.in_(member_team_ids))
                ).scalars().all()
                teams_by_id = {t.id: t for t in member_teams}

            member_teams_payload = []
            for row in member_rows:
                team_obj = teams_by_id.get(row.team_id)
                member_teams_payload.append(
                    {
                        "team_id": row.team_id,
                        "role": row.role or "member",
                        "team": {
                            "id": team_obj.id if team_obj else row.team_id,
                            "name": team_obj.name if team_obj else None,
                            "description": team_obj.description if team_obj else None,
                            "status": team_obj.status if team_obj else None,
                            "plan_code": team_obj.plan_code if team_obj else None,
                            "created_at": _serialize_dt(team_obj.created_at) if team_obj else None,
                            "updated_at": _serialize_dt(team_obj.updated_at) if team_obj else None,
                        },
                    }
                )

            user_payload = {
                "id": user.id,
                "email": user.email,
                "tier": tier,
                "account_type": user.account_type or "individual",
                "password_reset_required": bool(user.password_reset_required),
                "created_at": _serialize_dt(user.created_at),
            }

        return jsonify(
            {
                "success": True,
                "user": user_payload,
                "tier": tier,
                "primary_team_id": primary_team_id,
                "owner_teams": owner_teams_payload,
                "member_teams": member_teams_payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 사용자 엔터프라이즈 정보 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise/init-team", methods=["POST"])
@tier_required(["super"])
def init_enterprise_team_for_user(user_id):
    """
    Admin이 특정 사용자를 엔터프라이즈 장으로 지정하면서
    - 해당 사용자의 tier를 'enterprise'로 올리고
    - teams 테이블에 새 팀을 생성하고 owner로 등록
    - team_members에도 owner로 추가
    """
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        data = request.json or {}
        team_name = data.get("team_name")
        team_description = data.get("description") or ""
        requested_plan_code = (data.get("plan_code") or "starter").strip().lower()
        plan_code = requested_plan_code if requested_plan_code in ALLOWED_PLAN_CODES else "starter"

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            existing_owner_team = db_session.execute(
                select(Team).where(Team.owner_id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if existing_owner_team:
                return jsonify(
                    {
                        "success": True,
                        "message": "이미 대표 팀이 존재합니다.",
                        "user": {
                            "id": user.id,
                            "email": user.email,
                            "tier": user.tier or "free",
                            "account_type": user.account_type or "individual",
                            "password_reset_required": bool(user.password_reset_required),
                            "created_at": _serialize_dt(user.created_at),
                        },
                        "team": {
                            "id": existing_owner_team.id,
                            "name": existing_owner_team.name,
                            "plan_code": existing_owner_team.plan_code or "starter",
                        },
                    }
                )

            if not team_name:
                email = user.email or ""
                if "@" in email:
                    domain_part = email.split("@", 1)[1].split(".")[0]
                    team_name = f"{domain_part} 팀"
                else:
                    team_name = f"Enterprise 팀 ({str(user_id)[:8]})"

            try:
                user.tier = "enterprise"
                user.account_type = "enterprise"
                user.password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
                user.password_reset_required = True
            except Exception as exc:
                log_error(exc, "Admin - 사용자 tier enterprise 업데이트 실패")

            team = Team(
                name=team_name,
                description=team_description,
                owner_id=user_id_int,
                status="active",
                plan_code=plan_code,
            )
            db_session.add(team)
            db_session.flush()
            db_session.refresh(team)

            member_exists = db_session.execute(
                select(TeamMember.id)
                .where(
                    and_(
                        TeamMember.team_id == team.id,
                        TeamMember.user_id == user_id_int,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if not member_exists:
                db_session.add(TeamMember(team_id=team.id, user_id=user_id_int, role="owner"))

            user_payload = {
                "id": user.id,
                "email": user.email,
                "tier": user.tier or "enterprise",
                "account_type": user.account_type or "enterprise",
                "password_reset_required": bool(user.password_reset_required),
                "created_at": _serialize_dt(user.created_at),
            }
            team_payload = {
                "id": team.id,
                "name": team.name,
                "description": team.description,
                "owner_id": team.owner_id,
                "status": team.status,
                "plan_code": team.plan_code,
                "created_at": _serialize_dt(team.created_at),
                "updated_at": _serialize_dt(team.updated_at),
            }

        return jsonify(
            {
                "success": True,
                "message": "엔터프라이즈 팀이 생성되고 사용자가 오너로 등록되었습니다.",
                "user": user_payload,
                "team": team_payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 엔터프라이즈 팀 생성 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/users", methods=["POST"])
@jwt_required()
def create_enterprise_user():
    """super 또는 팀 owner: 기업 계정 생성 + 임시 비밀번호 발급 + 팀 소속 지정"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip() or None
        team_id = _to_int_or_none(data.get("team_id"))
        role = (data.get("role") or "member").strip().lower()
        if role not in {"owner", "member"}:
            role = "member"
        if not email:
            return jsonify({"success": False, "error": "email이 필요합니다."}), 400
        if not team_id:
            return jsonify({"success": False, "error": "team_id가 필요합니다."}), 400

        password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)

        with session_scope() as db_session:
            team = db_session.execute(select(Team).where(Team.id == int(team_id)).limit(1)).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404

            requester_id = _to_int_or_none(get_jwt_identity())
            claims = get_jwt() or {}
            requester_tier = (claims.get("tier") or "").strip().lower()
            if requester_tier == "admin":
                requester_tier = "super"

            is_super = requester_tier == "super"
            is_team_owner = requester_id is not None and team.owner_id is not None and int(team.owner_id) == int(requester_id)
            if not (is_super or is_team_owner):
                return jsonify({"success": False, "error": "권한이 없습니다. super 또는 팀 owner만 가능합니다."}), 403

            existing = db_session.execute(
                select(User).where(func.lower(User.email) == email).limit(1)
            ).scalar_one_or_none()
            if existing:
                user = existing
                user.name = name or user.name
                user.tier = "enterprise"
                user.account_type = "enterprise"
                user.password_hash = password_hash
                user.password_reset_required = True
            else:
                user = User(
                    email=email,
                    name=name,
                    tier="enterprise",
                    account_type="enterprise",
                    password_hash=password_hash,
                    password_reset_required=True,
                )
                db_session.add(user)
                db_session.flush()

            existing_member = db_session.execute(
                select(TeamMember)
                .where(and_(TeamMember.team_id == int(team_id), TeamMember.user_id == user.id))
                .limit(1)
            ).scalar_one_or_none()
            if existing_member:
                existing_member.role = role
            else:
                db_session.add(TeamMember(team_id=int(team_id), user_id=user.id, role=role))

            if role == "owner":
                team.owner_id = user.id

            db_session.flush()
            db_session.refresh(user)

            return jsonify(
                {
                    "success": True,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.name,
                        "tier": user.tier,
                        "account_type": user.account_type,
                        "password_reset_required": bool(user.password_reset_required),
                        "created_at": _serialize_dt(user.created_at),
                    },
                    "team": {
                        "id": team.id,
                        "name": team.name,
                        "plan_code": team.plan_code or "starter",
                        "role": role,
                    },
                    "temporary_password": DEFAULT_ENTERPRISE_PASSWORD,
                }
            ), 201
    except Exception as e:
        log_error(e, "Admin - 기업 사용자 생성 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>/plan", methods=["PUT"])
@tier_required(["super"])
def update_team_plan_code(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        data = request.json or {}
        plan_code = (data.get("plan_code") or "").strip().lower()
        if plan_code not in ALLOWED_PLAN_CODES:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400

        with session_scope() as db_session:
            team = db_session.execute(select(Team).where(Team.id == team_id).limit(1)).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
            team.plan_code = plan_code

        return jsonify({"success": True, "team_id": team_id, "plan_code": plan_code}), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 plan 변경 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>/usage", methods=["GET"])
@tier_required(["super"])
def get_team_usage(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        start_at = _parse_iso_date(request.args.get("start_at"))
        end_at = _parse_iso_date(request.args.get("end_at"))
        if request.args.get("start_at") and start_at is None:
            return jsonify({"success": False, "error": "start_at은 ISO datetime 형식이어야 합니다."}), 400
        if request.args.get("end_at") and end_at is None:
            return jsonify({"success": False, "error": "end_at은 ISO datetime 형식이어야 합니다."}), 400

        with session_scope() as db_session:
            team = db_session.execute(select(Team).where(Team.id == team_id).limit(1)).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404

            base = select(
                func.sum(TeamUsageEvent.request_count),
                func.sum(TeamUsageEvent.prompt_tokens),
                func.sum(TeamUsageEvent.completion_tokens),
                func.sum(TeamUsageEvent.total_tokens),
            ).where(TeamUsageEvent.team_id == team_id)
            if start_at:
                base = base.where(TeamUsageEvent.occurred_at >= start_at)
            if end_at:
                base = base.where(TeamUsageEvent.occurred_at <= end_at)
            total_row = db_session.execute(base).one()

            by_feature_q = select(
                TeamUsageEvent.feature_key,
                func.sum(TeamUsageEvent.request_count).label("request_count"),
                func.sum(TeamUsageEvent.total_tokens).label("total_tokens"),
            ).where(TeamUsageEvent.team_id == team_id)
            if start_at:
                by_feature_q = by_feature_q.where(TeamUsageEvent.occurred_at >= start_at)
            if end_at:
                by_feature_q = by_feature_q.where(TeamUsageEvent.occurred_at <= end_at)
            by_feature_q = by_feature_q.group_by(TeamUsageEvent.feature_key).order_by(TeamUsageEvent.feature_key.asc())
            feature_rows = db_session.execute(by_feature_q).all()

            by_user_q = select(
                TeamUsageEvent.user_id,
                func.sum(TeamUsageEvent.request_count).label("request_count"),
                func.sum(TeamUsageEvent.total_tokens).label("total_tokens"),
            ).where(TeamUsageEvent.team_id == team_id)
            if start_at:
                by_user_q = by_user_q.where(TeamUsageEvent.occurred_at >= start_at)
            if end_at:
                by_user_q = by_user_q.where(TeamUsageEvent.occurred_at <= end_at)
            by_user_q = by_user_q.group_by(TeamUsageEvent.user_id).order_by(TeamUsageEvent.user_id.asc())
            user_rows = db_session.execute(by_user_q).all()

        return jsonify(
            {
                "success": True,
                "team": {
                    "id": team.id,
                    "name": team.name,
                    "plan_code": team.plan_code or "starter",
                },
                "window": {
                    "start_at": _serialize_dt(start_at),
                    "end_at": _serialize_dt(end_at),
                },
                "totals": {
                    "request_count": int(total_row[0] or 0),
                    "prompt_tokens": int(total_row[1] or 0),
                    "completion_tokens": int(total_row[2] or 0),
                    "total_tokens": int(total_row[3] or 0),
                },
                "by_feature": [
                    {
                        "feature_key": row.feature_key,
                        "request_count": int(row.request_count or 0),
                        "total_tokens": int(row.total_tokens or 0),
                    }
                    for row in feature_rows
                ],
                "by_user": [
                    {
                        "user_id": row.user_id,
                        "request_count": int(row.request_count or 0),
                        "total_tokens": int(row.total_tokens or 0),
                    }
                    for row in user_rows
                ],
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 사용량 조회 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/stats", methods=["GET"])
@tier_required(["super"])
def get_admin_stats():
    """관리자 대시보드 통계 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            user_rows = db_session.execute(select(User.id, User.tier)).all()
            total_users = len(user_rows)
            tier_counts = {}
            for _uid, tier in user_rows:
                t = tier or "free"
                tier_counts[t] = tier_counts.get(t, 0) + 1

            total_projects = db_session.execute(
                select(func.count()).select_from(Project)
            ).scalar_one() or 0
            total_studies = db_session.execute(
                select(func.count()).select_from(Study)
            ).scalar_one() or 0

        return jsonify(
            {
                "success": True,
                "stats": {
                    "total_users": int(total_users),
                    "tier_counts": tier_counts,
                    "total_projects": int(total_projects),
                    "total_studies": int(total_studies),
                },
            }
        )
    except Exception as e:
        log_error(e, "Admin - 통계 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/projects", methods=["GET"])
@tier_required(["super"])
def get_user_projects(user_id):
    """특정 사용자의 프로젝트 목록 조회 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            projects = db_session.execute(
                select(Project)
                .where(Project.owner_id == user_id_int)
                .order_by(Project.created_at.desc())
            ).scalars().all()

        payload = [
            {
                "id": p.id,
                "owner_id": p.owner_id,
                "name": p.name,
                "slug": p.slug,
                "product_url": p.product_url,
                "keywords": p.keywords,
                "created_at": _serialize_dt(p.created_at),
                "updated_at": _serialize_dt(p.updated_at),
            }
            for p in projects
        ]
        return jsonify({"success": True, "projects": payload, "count": len(payload)})
    except Exception as e:
        log_error(e, f"Admin - 사용자 프로젝트 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/studies", methods=["GET"])
@tier_required(["super"])
def get_user_studies(user_id):
    """특정 사용자의 스터디 목록 조회 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            project_ids = db_session.execute(
                select(Project.id).where(Project.owner_id == user_id_int)
            ).scalars().all()
            if not project_ids:
                return jsonify({"success": True, "studies": [], "count": 0})

            rows = db_session.execute(
                select(Study, Project.name)
                .join(Project, Project.id == Study.project_id)
                .where(Study.project_id.in_(project_ids))
                .order_by(Study.created_at.desc())
            ).all()

        studies = []
        for study, project_name in rows:
            studies.append(
                {
                    "id": study.id,
                    "project_id": study.project_id,
                    "name": study.name,
                    "slug": study.slug,
                    "initial_input": study.initial_input,
                    "keywords": study.keywords,
                    "methodologies": study.methodologies,
                    "participant_count": study.participant_count,
                    "start_date": study.start_date.isoformat() if study.start_date else None,
                    "end_date": study.end_date.isoformat() if study.end_date else None,
                    "timeline": study.timeline,
                    "budget": study.budget,
                    "target_audience": study.target_audience,
                    "additional_requirements": study.additional_requirements,
                    "created_at": _serialize_dt(study.created_at),
                    "updated_at": _serialize_dt(study.updated_at),
                    "projects": {"name": project_name},
                }
            )

        return jsonify({"success": True, "studies": studies, "count": len(studies)})
    except Exception as e:
        log_error(e, f"Admin - 사용자 스터디 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/studies/<int:study_id>", methods=["GET"])
@tier_required(["super"])
def admin_get_study(study_id):
    """Admin 전용 - Study 조회 (권한 검증 없이)"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            study = db_session.execute(
                select(Study).where(Study.id == study_id).limit(1)
            ).scalar_one_or_none()
            if not study:
                return jsonify({"success": False, "error": "연구를 찾을 수 없습니다."}), 404

            payload = {
                "id": study.id,
                "project_id": study.project_id,
                "name": study.name,
                "slug": study.slug,
                "initial_input": study.initial_input,
                "keywords": study.keywords,
                "methodologies": study.methodologies,
                "participant_count": study.participant_count,
                "start_date": study.start_date.isoformat() if study.start_date else None,
                "end_date": study.end_date.isoformat() if study.end_date else None,
                "timeline": study.timeline,
                "budget": study.budget,
                "target_audience": study.target_audience,
                "additional_requirements": study.additional_requirements,
                "created_at": _serialize_dt(study.created_at),
                "updated_at": _serialize_dt(study.updated_at),
            }
        return jsonify(payload)
    except Exception as e:
        log_error(e, f"Admin - Study 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/studies/<int:study_id>/artifacts", methods=["GET"])
@tier_required(["super"])
def admin_get_study_artifacts(study_id):
    """Admin 전용 - Study의 Artifacts 조회 (권한 검증 없이)"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            artifacts = db_session.execute(
                select(Artifact)
                .where(Artifact.study_id == study_id)
                .order_by(Artifact.created_at.desc())
            ).scalars().all()

        payload = [
            {
                "id": a.id,
                "study_id": a.study_id,
                "owner_id": a.owner_id,
                "artifact_type": a.artifact_type,
                "content": a.content,
                "status": a.status,
                "created_at": _serialize_dt(a.created_at),
                "updated_at": _serialize_dt(a.updated_at),
            }
            for a in artifacts
        ]
        return jsonify({"success": True, "artifacts": payload})
    except Exception as e:
        log_error(e, f"Admin - Study Artifacts 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/feedback", methods=["POST"])
@jwt_required()
def submit_feedback():
    """피드백 저장 - 로그인한 사용자만 사용 가능"""
    try:
        data = request.json or {}
        category = data.get("category")
        vote = data.get("vote")
        comment = data.get("comment", "")

        valid_categories = ["plan", "screener", "guide", "participants"]
        if not category or category not in valid_categories:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 category입니다. 가능한 값: {valid_categories}",
                }
            ), 400
        if vote is None:
            return jsonify({"success": False, "error": "vote 값이 필요합니다. (true 또는 false)"}), 400

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id = get_jwt_identity()
        user_id_int = _to_int_or_none(user_id)
        study_id = data.get("study_id")
        study_name = data.get("study_name", "")
        vote_str = "true" if bool(vote) else "false"

        with session_scope() as db_session:
            feedback = UserFeedback(
                category=category,
                vote=vote_str,
                comment=comment if comment else None,
                user_id=user_id_int,
                study_id=int(study_id) if study_id else None,
                study_name=study_name if study_name else None,
            )
            db_session.add(feedback)
            db_session.flush()
            db_session.refresh(feedback)

            feedback_payload = {
                "id": feedback.id,
                "category": feedback.category,
                "vote": feedback.vote,
                "comment": feedback.comment,
                "user_id": feedback.user_id,
                "study_id": feedback.study_id,
                "study_name": feedback.study_name,
                "created_at": _serialize_dt(feedback.created_at),
                "updated_at": _serialize_dt(feedback.updated_at),
            }

        return jsonify(
            {
                "success": True,
                "message": "피드백이 저장되었습니다.",
                "feedback": feedback_payload,
            }
        )
    except Exception as e:
        log_error(e, "피드백 저장")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/feedback/<int:feedback_id>", methods=["PATCH"])
@jwt_required()
def update_feedback_comment(feedback_id):
    """피드백 코멘트만 업데이트"""
    try:
        data = request.json or {}
        comment = data.get("comment", "")

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(get_jwt_identity())
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자입니다."}), 401

        with session_scope() as db_session:
            feedback = db_session.execute(
                select(UserFeedback)
                .where(
                    and_(
                        UserFeedback.id == feedback_id,
                        UserFeedback.user_id == user_id_int,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if not feedback:
                return jsonify({"success": False, "error": "피드백을 찾을 수 없거나 권한이 없습니다."}), 404

            feedback.comment = comment if comment else None
            feedback_payload = {
                "id": feedback.id,
                "category": feedback.category,
                "vote": feedback.vote,
                "comment": feedback.comment,
                "user_id": feedback.user_id,
                "study_id": feedback.study_id,
                "study_name": feedback.study_name,
                "created_at": _serialize_dt(feedback.created_at),
                "updated_at": _serialize_dt(feedback.updated_at),
            }

        return jsonify(
            {
                "success": True,
                "message": "코멘트가 업데이트되었습니다.",
                "feedback": feedback_payload,
            }
        )
    except Exception as e:
        log_error(e, f"피드백 {feedback_id} 코멘트 업데이트")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/feedback", methods=["GET"])
@tier_required(["super"])
def get_feedback():
    """피드백 조회 - admin 전용, category 필터링 지원"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        category = request.args.get("category", None)
        valid_categories = ["plan", "screener", "guide", "participants"]
        if category and category not in valid_categories:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 category입니다. 가능한 값: {valid_categories}",
                }
            ), 400

        with session_scope() as db_session:
            query = select(UserFeedback).order_by(UserFeedback.created_at.desc())
            if category:
                query = query.where(UserFeedback.category == category)
            rows = db_session.execute(query).scalars().all()

        feedback_payload = [
            {
                "id": row.id,
                "category": row.category,
                "vote": row.vote,
                "comment": row.comment,
                "user_id": row.user_id,
                "study_id": row.study_id,
                "study_name": row.study_name,
                "created_at": _serialize_dt(row.created_at),
                "updated_at": _serialize_dt(row.updated_at),
            }
            for row in rows
        ]

        return jsonify(
            {
                "success": True,
                "feedback": feedback_payload,
                "count": len(feedback_payload),
                "category": category if category else "all",
            }
        )
    except Exception as e:
        log_error(e, "Admin - 피드백 조회")
        return jsonify({"success": False, "error": str(e)}), 500
