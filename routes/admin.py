"""
Admin 전용 API 라우트
"""

import traceback
from datetime import datetime, timezone
import math

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import and_, func, or_, select, update
from werkzeug.security import generate_password_hash

from db.engine import session_scope
from db.models.core import (
    Artifact,
    Company,
    CompanyMember,
    CompanyTokenLedger,
    CompanyUsageEvent,
    LlmModelPrice,
    LlmUsageDailyAggregate,
    LlmUsageEvent,
    Project,
    Study,
    Team,
    TeamMember,
    TeamUsageEvent,
    User,
    UserFeedback,
)
from reopsai_backend.shared.auth import tier_required
from routes.auth import get_primary_team_id_for_user
from utils.usage_metering import cleanup_old_llm_usage_events, ensure_company_initial_grant, get_company_token_balance


admin_bp = Blueprint("admin", __name__)
ALLOWED_PLAN_CODES = {"starter", "pro", "enterprise_plus"}
ALLOWED_USER_PLAN_CODES = {"free", "basic", "premium"}
USER_PLAN_CODE_ALIASES = {
    "starter": "free",
    "pro": "basic",
    "enterprise_plus": "premium",
}
DEFAULT_ENTERPRISE_PASSWORD = "0000"
DELETED_TEAM_STATUS = "deleted"
BUSINESS_ACCOUNT_TYPE = "business"


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


def _parse_usage_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None


def _usage_period():
    period = (request.args.get("period") or "daily").strip().lower()
    return period if period in {"daily", "monthly"} else None


def _serialize_decimal(value):
    return float(value or 0)


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
    if account_type == BUSINESS_ACCOUNT_TYPE:
        return BUSINESS_ACCOUNT_TYPE
    if user.google_id:
        return "google"
    return "individual"


def _get_company(db_session, company_id):
    if not company_id:
        return None
    return db_session.execute(
        select(Company).where(Company.id == int(company_id)).limit(1)
    ).scalar_one_or_none()


def _get_or_create_company(db_session, name):
    company_name = (name or "").strip()
    if not company_name:
        return None
    company = db_session.execute(
        select(Company).where(func.lower(Company.name) == company_name.lower()).limit(1)
    ).scalar_one_or_none()
    if company:
        return company
    company = Company(name=company_name, status="active")
    db_session.add(company)
    db_session.flush()
    db_session.refresh(company)
    ensure_company_initial_grant(db_session, company.id)
    return company


def _set_company_for_user(db_session, user: User, name):
    company_name = (name or "").strip()
    if not company_name:
        user.company_id = None
        return None

    existing = db_session.execute(
        select(Company).where(func.lower(Company.name) == company_name.lower()).limit(1)
    ).scalar_one_or_none()
    if existing and existing.id != user.company_id:
        company = existing
    elif user.company_id:
        company = _get_company(db_session, user.company_id)
        if company:
            company.name = company_name
        else:
            company = _get_or_create_company(db_session, company_name)
    else:
        company = _get_or_create_company(db_session, company_name)

    user.company_id = company.id if company else None
    return company


def _company_name_for(db_session, company_id, fallback=None):
    company = _get_company(db_session, company_id)
    return company.name if company else fallback


def _account_list_payload(db_session, user: User):
    member_row = db_session.execute(
        select(CompanyMember)
        .where(CompanyMember.user_id == user.id)
        .order_by(CompanyMember.joined_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    account_type = user.account_type or "individual"
    auth_type = _user_auth_type(user)
    company_id = user.company_id or (member_row.company_id if member_row else None)
    company_name = _company_name_for(db_session, company_id)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "company_id": company_id,
        "company_name": company_name,
        "department": user.department,
        "plan_code": None if account_type == BUSINESS_ACCOUNT_TYPE else (user.tier or "free"),
        "account_type": account_type,
        "auth_type": auth_type,
        "company_role": member_row.role if member_row else None,
        "is_owner": (member_row.role if member_row else None) == "owner",
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

    return {
        "id": team.id,
        "team_name": team.name,
        "description": team.description,
        "status": team.status,
        "plan_code": team.plan_code or "starter",
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
        company_role = (request.args.get("company_role") or request.args.get("team_role") or "").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": "유효하지 않은 plan_code입니다."}), 400
        if account_type and account_type not in {"google", "business", "individual"}:
            return jsonify({"success": False, "error": "유효하지 않은 account_type입니다."}), 400
        if company_role and company_role not in {"owner", "member"}:
            return jsonify({"success": False, "error": "유효하지 않은 company_role입니다."}), 400
        page, per_page = _pagination_params()

        with session_scope() as db_session:
            query = (
                select(User)
                .where(func.lower(User.tier).notin_(["super", "admin"]))
                .order_by(User.created_at.desc())
            )
            if search:
                pattern = f"%{search.lower()}%"
                query = query.where(
                    or_(
                        func.lower(User.email).like(pattern),
                        func.lower(User.name).like(pattern),
                        func.lower(User.department).like(pattern),
                        User.company_id.in_(
                            select(Company.id).where(func.lower(Company.name).like(pattern))
                        ),
                        User.id.in_(
                            select(CompanyMember.user_id)
                            .join(Company, Company.id == CompanyMember.company_id)
                            .where(func.lower(Company.name).like(pattern))
                        ),
                    )
                )
            if account_type == "business":
                query = query.where(User.account_type == BUSINESS_ACCOUNT_TYPE)
            elif account_type == "individual":
                query = query.where(User.account_type != BUSINESS_ACCOUNT_TYPE)
            elif account_type == "google":
                query = query.where(User.account_type != BUSINESS_ACCOUNT_TYPE, User.google_id.is_not(None))

            users = db_session.execute(query).scalars().all()
            accounts = []
            for user in users:
                payload = _account_list_payload(db_session, user)
                if plan_code and payload["plan_code"] != plan_code:
                    continue
                if company_role and payload["company_role"] != company_role:
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
        department = (data.get("department") or "").strip() or None

        if not all([email, name, company_name]):
            return jsonify({"success": False, "error": "email, name, company_name은 필수입니다."}), 400

        with session_scope() as db_session:
            exists = db_session.execute(
                select(User.id).where(func.lower(User.email) == email).limit(1)
            ).scalar_one_or_none()
            if exists:
                return jsonify({"success": False, "error": "이미 존재하는 이메일입니다."}), 409

            company = _get_or_create_company(db_session, company_name)

            user = User(
                email=email,
                name=name,
                company_id=company.id if company else None,
                department=department,
                tier="enterprise",
                account_type=BUSINESS_ACCOUNT_TYPE,
                password_hash=generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD),
                password_reset_required=True,
            )
            db_session.add(user)
            db_session.flush()
            db_session.refresh(user)
            if company:
                db_session.add(CompanyMember(company_id=company.id, user_id=user.id, role="owner"))

            account_payload = {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "company_id": user.company_id,
                "company_name": company.name if company else None,
                "department": user.department,
                "account_type": user.account_type,
                "tier": user.tier,
                "password_reset_required": bool(user.password_reset_required),
            }

        return jsonify({"success": True, "account": account_payload}), 201
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
        has_department = "department" in data
        has_plan_code = "plan_code" in data
        if not has_name and not has_company_name and not has_department and not has_plan_code:
            return jsonify({"success": False, "error": "수정할 name, company_name, department 또는 plan_code가 필요합니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == account_id).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "계정을 찾을 수 없습니다."}), 404

            auth_type = _user_auth_type(user)
            plan_code = None
            if has_plan_code:
                if auth_type == BUSINESS_ACCOUNT_TYPE:
                    return jsonify({"success": False, "error": "기업형 계정의 플랜은 이 API에서 변경할 수 없습니다."}), 403
                if (user.tier or "").strip().lower() not in ALLOWED_USER_PLAN_CODES:
                    return jsonify({"success": False, "error": "free/basic/premium 일반 계정만 플랜을 변경할 수 있습니다."}), 403
                plan_code = _validate_user_plan_code(data.get("plan_code"), required=True)
                if not plan_code:
                    allowed = sorted(ALLOWED_USER_PLAN_CODES | set(USER_PLAN_CODE_ALIASES.keys()))
                    return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {allowed}"}), 400

            if has_name:
                user.name = (data.get("name") or "").strip() or None
            if has_company_name:
                company = _set_company_for_user(db_session, user, data.get("company_name"))
                if company and (user.account_type or "") == BUSINESS_ACCOUNT_TYPE:
                    membership = db_session.execute(
                        select(CompanyMember).where(CompanyMember.user_id == user.id).limit(1)
                    ).scalar_one_or_none()
                    if membership:
                        membership.company_id = company.id
                    else:
                        db_session.add(CompanyMember(company_id=company.id, user_id=user.id, role="owner"))
            if has_department:
                user.department = (data.get("department") or "").strip() or None
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


@admin_bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_admin_user(user_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        requester_id = _to_int_or_none(get_jwt_identity())
        claims = get_jwt() or {}
        requester_tier = (claims.get("tier") or "").strip().lower()
        if requester_tier == "admin":
            requester_tier = "super"

        if claims.get("password_reset_required"):
            return jsonify({"success": False, "error": "비밀번호 변경 후 이용할 수 있습니다."}), 403
        if requester_id is None:
            return jsonify({"success": False, "error": "인증 정보를 확인할 수 없습니다."}), 401
        if requester_id == user_id:
            return jsonify({"success": False, "error": "현재 로그인한 계정은 이 API로 삭제할 수 없습니다."}), 400

        with session_scope() as db_session:
            target = db_session.execute(
                select(User).where(User.id == int(user_id)).limit(1)
            ).scalar_one_or_none()
            if not target:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            is_super = requester_tier == "super"
            requester_owner_company_ids = db_session.execute(
                select(CompanyMember.company_id).where(
                    CompanyMember.user_id == int(requester_id),
                    CompanyMember.role == "owner",
                )
            ).scalars().all()
            is_owned_company_member = False
            if requester_owner_company_ids:
                is_owned_company_member = (
                    db_session.execute(
                        select(CompanyMember.id)
                        .where(
                            and_(
                                CompanyMember.company_id.in_(requester_owner_company_ids),
                                CompanyMember.user_id == int(user_id),
                            )
                        )
                        .limit(1)
                    ).scalar_one_or_none()
                    is not None
                )

            if not (is_super or is_owned_company_member):
                return jsonify({"success": False, "error": "권한이 없습니다. super 또는 해당 회사 owner만 삭제할 수 있습니다."}), 403

            target_tier = (target.tier or "free").strip().lower()
            if not is_super and target_tier == "super":
                return jsonify({"success": False, "error": "팀 owner는 super 계정을 삭제할 수 없습니다."}), 403

            target_owner_company_count = db_session.execute(
                select(func.count())
                .select_from(CompanyMember)
                .where(CompanyMember.user_id == int(user_id), CompanyMember.role == "owner")
            ).scalar_one() or 0
            if not is_super and target_owner_company_count:
                return jsonify({"success": False, "error": "회사 owner 계정은 super만 삭제할 수 있습니다."}), 403

            membership_count = db_session.execute(
                select(func.count()).select_from(CompanyMember).where(CompanyMember.user_id == int(user_id))
            ).scalar_one() or 0
            project_count = db_session.execute(
                select(func.count()).select_from(Project).where(Project.owner_id == int(user_id))
            ).scalar_one() or 0
            usage_event_count = db_session.execute(
                select(func.count()).select_from(CompanyUsageEvent).where(CompanyUsageEvent.user_id == int(user_id))
            ).scalar_one() or 0

            deleted_user_payload = {
                "id": target.id,
                "email": target.email,
                "name": target.name,
                "tier": target.tier,
                "account_type": target.account_type,
                "company_id": target.company_id,
                "company_name": _company_name_for(db_session, target.company_id),
                "department": target.department,
            }

            db_session.delete(target)

        return jsonify(
            {
                "success": True,
                "message": "사용자가 삭제되었습니다.",
                "deleted_user": deleted_user_payload,
                "affected": {
                    "company_memberships": int(membership_count),
                    "owned_companies_released": int(target_owner_company_count),
                    "owned_projects": int(project_count),
                    "usage_events_anonymized": int(usage_event_count),
                },
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 사용자 삭제 실패 (user_id: {user_id})")
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
        status = (request.args.get("status") or "active").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400
        if request.args.get("enterprise_account_id") and enterprise_account_id is None:
            return jsonify({"success": False, "error": "enterprise_account_id가 올바르지 않습니다."}), 400
        if status not in {"active", "deleted", "all"}:
            return jsonify({"success": False, "error": "status는 active, deleted, all 중 하나여야 합니다."}), 400
        page, per_page = _pagination_params()

        with session_scope() as db_session:
            query = select(Team).order_by(Team.created_at.desc())
            if status != "all":
                query = query.where(Team.status == status)
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
            if owner.account_type == BUSINESS_ACCOUNT_TYPE:
                return jsonify({"success": False, "error": "business 계정은 개인용 team owner로 지정할 수 없습니다."}), 400

            inherited_plan = db_session.execute(
                select(Team.plan_code)
                .where(Team.owner_id == enterprise_account_id, Team.status != DELETED_TEAM_STATUS)
                .order_by(Team.created_at.asc())
                .limit(1)
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


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["DELETE"])
@tier_required(["super"])
def soft_delete_admin_team(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            team = db_session.execute(
                select(Team).where(Team.id == int(team_id)).limit(1)
            ).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404

            member_count = db_session.execute(
                select(func.count()).select_from(TeamMember).where(TeamMember.team_id == int(team_id))
            ).scalar_one() or 0
            usage_event_count = db_session.execute(
                select(func.count()).select_from(TeamUsageEvent).where(TeamUsageEvent.team_id == int(team_id))
            ).scalar_one() or 0

            was_deleted = team.status == DELETED_TEAM_STATUS
            if not was_deleted:
                team.status = DELETED_TEAM_STATUS
                team.updated_at = datetime.now()
                db_session.flush()

            team_payload = _team_payload(db_session, team)

        return jsonify(
            {
                "success": True,
                "message": "팀이 삭제 처리되었습니다." if not was_deleted else "이미 삭제 처리된 팀입니다.",
                "team": team_payload,
                "affected": {
                    "members_preserved": int(member_count),
                    "usage_events_preserved": int(usage_event_count),
                },
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 삭제 실패 (team_id: {team_id})")
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
                select(User)
                .where(func.lower(User.tier).notin_(["super", "admin"]))
                .order_by(User.created_at.desc())
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
                company_membership = db_session.execute(
                    select(CompanyMember).where(CompanyMember.user_id == user.id).limit(1)
                ).scalar_one_or_none()
                business_company_id = user.company_id or (company_membership.company_id if company_membership else None)

                payload_users.append(
                    {
                        "id": user.id,
                        "email": user.email,
                        "company_id": business_company_id,
                        "company_name": _company_name_for(db_session, business_company_id),
                        "department": user.department,
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
                        "business_company_id": business_company_id,
                        "business_company_role": company_membership.role if company_membership else None,
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
            membership = db_session.execute(
                select(CompanyMember).where(CompanyMember.user_id == user_id_int).limit(1)
            ).scalar_one_or_none()
            company_id = user.company_id or (membership.company_id if membership else None)

            user_payload = {
                "id": user.id,
                "email": user.email,
                "company_id": company_id,
                "company_name": _company_name_for(db_session, company_id),
                "department": user.department,
                "tier": tier,
                "account_type": user.account_type or "individual",
                "password_reset_required": bool(user.password_reset_required),
                "created_at": _serialize_dt(user.created_at),
            }
            company_payload = None
            if company_id:
                company = _get_company(db_session, company_id)
                company_payload = {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "role": membership.role if membership else None,
                    "joined_at": _serialize_dt(membership.joined_at) if membership else None,
                } if company else None

        return jsonify(
            {
                "success": True,
                "user": user_payload,
                "tier": tier,
                "company": company_payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 사용자 엔터프라이즈 정보 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise/init-team", methods=["POST"])
@tier_required(["super"])
def init_enterprise_team_for_user(user_id):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        data = request.json or {}
        company_name = (data.get("company_name") or "").strip()
        department = (data.get("department") or "").strip() or None

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            existing_owner = db_session.execute(
                select(CompanyMember)
                .where(CompanyMember.user_id == user_id_int, CompanyMember.role == "owner")
                .limit(1)
            ).scalar_one_or_none()
            if existing_owner:
                return jsonify(
                    {
                        "success": True,
                        "message": "이미 대표 회사 멤버십이 존재합니다.",
                        "user": {
                            "id": user.id,
                            "email": user.email,
                            "company_id": user.company_id or existing_owner.company_id,
                            "company_name": _company_name_for(db_session, user.company_id or existing_owner.company_id),
                            "department": user.department,
                            "tier": user.tier or "free",
                            "account_type": user.account_type or "individual",
                            "password_reset_required": bool(user.password_reset_required),
                            "created_at": _serialize_dt(user.created_at),
                        },
                        "company": {
                            "id": existing_owner.company_id,
                            "name": _company_name_for(db_session, existing_owner.company_id),
                            "role": existing_owner.role,
                        },
                    }
                )

            try:
                user.tier = "enterprise"
                user.account_type = BUSINESS_ACCOUNT_TYPE
                user.password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
                user.password_reset_required = True
                if department is not None:
                    user.department = department
                if not company_name:
                    email = user.email or ""
                    company_name = email.split("@", 1)[1].split(".")[0] if "@" in email else f"Business {user_id}"
                company = _get_or_create_company(db_session, company_name)
                if company:
                    user.company_id = company.id
            except Exception as exc:
                log_error(exc, "Admin - 사용자 business 업데이트 실패")

            member_exists = db_session.execute(
                select(CompanyMember)
                .where(CompanyMember.company_id == user.company_id, CompanyMember.user_id == user_id_int)
                .limit(1)
            ).scalar_one_or_none()
            if member_exists:
                member_exists.role = "owner"
            elif user.company_id:
                db_session.add(CompanyMember(company_id=user.company_id, user_id=user_id_int, role="owner"))

            user_payload = {
                "id": user.id,
                "email": user.email,
                "company_id": user.company_id,
                "company_name": _company_name_for(db_session, user.company_id),
                "department": user.department,
                "tier": user.tier or "enterprise",
                "account_type": user.account_type or BUSINESS_ACCOUNT_TYPE,
                "password_reset_required": bool(user.password_reset_required),
                "created_at": _serialize_dt(user.created_at),
            }
            company_payload = {
                "id": user.company_id,
                "name": _company_name_for(db_session, user.company_id),
            }

        return jsonify(
            {
                "success": True,
                "message": "business 회사가 설정되고 사용자가 owner로 등록되었습니다.",
                "user": user_payload,
                "company": company_payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 엔터프라이즈 팀 생성 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/users", methods=["POST"])
@jwt_required()
def create_enterprise_user():
    """super 또는 company owner: 기업 계정 생성 + 임시 비밀번호 발급 + 회사 소속 지정"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip() or None
        company_id = _to_int_or_none(data.get("company_id"))
        department = (data.get("department") or "").strip() or None
        role = (data.get("role") or "member").strip().lower()
        if role not in {"owner", "member"}:
            role = "member"
        if not email:
            return jsonify({"success": False, "error": "email이 필요합니다."}), 400
        if not company_id:
            return jsonify({"success": False, "error": "company_id가 필요합니다."}), 400

        password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)

        with session_scope() as db_session:
            company = db_session.execute(
                select(Company)
                .where(Company.id == int(company_id), Company.status != "deleted")
                .limit(1)
            ).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404

            requester_id = _to_int_or_none(get_jwt_identity())
            claims = get_jwt() or {}
            requester_tier = (claims.get("tier") or "").strip().lower()
            if requester_tier == "admin":
                requester_tier = "super"

            is_super = requester_tier == "super"
            is_company_owner = requester_id is not None and db_session.execute(
                select(CompanyMember.id)
                .where(
                    CompanyMember.company_id == int(company_id),
                    CompanyMember.user_id == int(requester_id),
                    CompanyMember.role == "owner",
                )
                .limit(1)
            ).scalar_one_or_none() is not None
            if not (is_super or is_company_owner):
                return jsonify({"success": False, "error": "권한이 없습니다. super 또는 회사 owner만 가능합니다."}), 403

            existing = db_session.execute(
                select(User).where(func.lower(User.email) == email).limit(1)
            ).scalar_one_or_none()
            if existing:
                user = existing
                user.name = name or user.name
                user.company_id = company.id
                if department is not None:
                    user.department = department
                user.tier = "enterprise"
                user.account_type = BUSINESS_ACCOUNT_TYPE
                user.password_hash = password_hash
                user.password_reset_required = True
            else:
                user = User(
                    email=email,
                    name=name,
                    company_id=company.id,
                    department=department,
                    tier="enterprise",
                    account_type=BUSINESS_ACCOUNT_TYPE,
                    password_hash=password_hash,
                    password_reset_required=True,
                )
                db_session.add(user)
                db_session.flush()

            existing_member = db_session.execute(
                select(CompanyMember)
                .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id == user.id))
                .limit(1)
            ).scalar_one_or_none()
            if existing_member:
                existing_member.role = role
            else:
                db_session.add(CompanyMember(company_id=int(company_id), user_id=user.id, role=role))

            if role == "owner":
                db_session.execute(
                    update(CompanyMember)
                    .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id != user.id))
                    .values(role="member")
                )

            db_session.flush()
            db_session.refresh(user)

            return jsonify(
                {
                    "success": True,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.name,
                        "company_id": user.company_id,
                        "company_name": _company_name_for(db_session, user.company_id),
                        "department": user.department,
                        "tier": user.tier,
                        "account_type": user.account_type,
                        "password_reset_required": bool(user.password_reset_required),
                        "created_at": _serialize_dt(user.created_at),
                    },
                    "company": {
                        "id": company.id,
                        "name": company.name,
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
            team = db_session.execute(
                select(Team)
                .where(Team.id == team_id, Team.status != DELETED_TEAM_STATUS)
                .limit(1)
            ).scalar_one_or_none()
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


@admin_bp.route("/api/admin/companies/<int:company_id>/usage", methods=["GET"])
@tier_required(["super"])
def get_company_usage(company_id: int):
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
            company = db_session.execute(
                select(Company).where(Company.id == company_id).limit(1)
            ).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404

            base = select(
                func.sum(CompanyUsageEvent.request_count),
                func.sum(CompanyUsageEvent.prompt_tokens),
                func.sum(CompanyUsageEvent.completion_tokens),
                func.sum(CompanyUsageEvent.total_tokens),
            ).where(CompanyUsageEvent.company_id == company_id)
            if start_at:
                base = base.where(CompanyUsageEvent.occurred_at >= start_at)
            if end_at:
                base = base.where(CompanyUsageEvent.occurred_at <= end_at)
            total_row = db_session.execute(base).one()

            by_feature_q = select(
                CompanyUsageEvent.feature_key,
                func.sum(CompanyUsageEvent.request_count).label("request_count"),
                func.sum(CompanyUsageEvent.total_tokens).label("total_tokens"),
            ).where(CompanyUsageEvent.company_id == company_id)
            if start_at:
                by_feature_q = by_feature_q.where(CompanyUsageEvent.occurred_at >= start_at)
            if end_at:
                by_feature_q = by_feature_q.where(CompanyUsageEvent.occurred_at <= end_at)
            by_feature_q = by_feature_q.group_by(CompanyUsageEvent.feature_key).order_by(CompanyUsageEvent.feature_key.asc())
            feature_rows = db_session.execute(by_feature_q).all()

            by_user_q = select(
                CompanyUsageEvent.user_id,
                func.sum(CompanyUsageEvent.request_count).label("request_count"),
                func.sum(CompanyUsageEvent.total_tokens).label("total_tokens"),
            ).where(CompanyUsageEvent.company_id == company_id)
            if start_at:
                by_user_q = by_user_q.where(CompanyUsageEvent.occurred_at >= start_at)
            if end_at:
                by_user_q = by_user_q.where(CompanyUsageEvent.occurred_at <= end_at)
            by_user_q = by_user_q.group_by(CompanyUsageEvent.user_id).order_by(CompanyUsageEvent.user_id.asc())
            user_rows = db_session.execute(by_user_q).all()

        return jsonify(
            {
                "success": True,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
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
        log_error(e, f"Admin - 회사 사용량 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


def _company_usage_summary(db_session, company_id: int):
    ensure_company_initial_grant(db_session, int(company_id))
    totals = db_session.execute(
        select(
            func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0),
            func.coalesce(func.sum(LlmUsageDailyAggregate.total_tokens), 0),
            func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0),
            func.coalesce(func.sum(LlmUsageDailyAggregate.estimated_cost_usd), 0),
        ).where(LlmUsageDailyAggregate.company_id == int(company_id))
    ).one()
    balance = get_company_token_balance(db_session, int(company_id))
    return {
        "request_count": int(totals[0] or 0),
        "total_tokens": int(totals[1] or 0),
        "billable_weighted_tokens": int(totals[2] or 0),
        "estimated_cost_usd": _serialize_decimal(totals[3]),
        "usage_limit": None,
        "remaining_weighted_tokens": balance,
    }


def _legacy_company_usage_summary(db_session, company_id: int):
    row = db_session.execute(
        select(
            func.sum(CompanyUsageEvent.request_count),
            func.sum(CompanyUsageEvent.total_tokens),
        ).where(CompanyUsageEvent.company_id == int(company_id))
    ).one()
    return {
        "request_count": int(row[0] or 0),
        "total_tokens": int(row[1] or 0),
        "usage_limit": 5000,
    }


def _usage_period_column(period: str):
    if period == "monthly":
        return func.to_char(func.date_trunc("month", LlmUsageDailyAggregate.usage_date), "YYYY-MM")
    return func.to_char(LlmUsageDailyAggregate.usage_date, "YYYY-MM-DD")


def _usage_totals_payload(row):
    return {
        "request_count": int(row.request_count or 0),
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "cached_input_tokens": int(row.cached_input_tokens or 0),
        "reasoning_tokens": int(row.reasoning_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
        "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
        "estimated_cost_usd": _serialize_decimal(row.estimated_cost_usd),
    }


def _empty_llm_usage_payload():
    return {
        "request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "billable_weighted_tokens": 0,
        "estimated_cost_usd": 0.0,
        "last_used_at": None,
    }


def _usage_base_query(filters):
    return select(
        func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0).label("request_count"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0).label("billable_weighted_tokens"),
        func.coalesce(func.sum(LlmUsageDailyAggregate.estimated_cost_usd), 0).label("estimated_cost_usd"),
    ).where(*filters)


def _usage_date_filters(filters):
    start_date = _parse_usage_date(request.args.get("start_date"))
    end_date = _parse_usage_date(request.args.get("end_date"))
    if request.args.get("start_date") and start_date is None:
        return None, None, jsonify({"success": False, "error": "start_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
    if request.args.get("end_date") and end_date is None:
        return None, None, jsonify({"success": False, "error": "end_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
    if start_date:
        filters.append(LlmUsageDailyAggregate.usage_date >= start_date)
    if end_date:
        filters.append(LlmUsageDailyAggregate.usage_date <= end_date)
    return start_date, end_date, None, None


def _usage_event_date_filters(event_filters, start_date, end_date):
    if start_date:
        event_filters.append(LlmUsageEvent.occurred_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        event_filters.append(LlmUsageEvent.occurred_at <= datetime.combine(end_date, datetime.max.time()))


def _usage_response(db_session, filters, period: str, event_filters=None):
    period_col = _usage_period_column(period).label("period")
    totals = db_session.execute(_usage_base_query(filters)).one()

    by_period_rows = db_session.execute(
        select(
            period_col,
            func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
            func.sum(LlmUsageDailyAggregate.prompt_tokens).label("prompt_tokens"),
            func.sum(LlmUsageDailyAggregate.completion_tokens).label("completion_tokens"),
            func.sum(LlmUsageDailyAggregate.cached_input_tokens).label("cached_input_tokens"),
            func.sum(LlmUsageDailyAggregate.reasoning_tokens).label("reasoning_tokens"),
            func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
            func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
            func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
        )
        .where(*filters)
        .group_by(period_col)
        .order_by(period_col.asc())
    ).all()

    by_user_aggregate = (
        select(
            LlmUsageDailyAggregate.user_id.label("user_id"),
            func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
            func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
            func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
            func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
        )
        .where(*filters)
        .group_by(LlmUsageDailyAggregate.user_id)
        .subquery()
    )
    last_user_event = (
        select(
            LlmUsageEvent.user_id.label("user_id"),
            func.max(LlmUsageEvent.occurred_at).label("last_used_at"),
        )
        .where(*(event_filters or []))
        .group_by(LlmUsageEvent.user_id)
        .subquery()
    )
    by_user_rows = db_session.execute(
        select(
            by_user_aggregate.c.user_id,
            User.email,
            User.name,
            by_user_aggregate.c.request_count,
            by_user_aggregate.c.total_tokens,
            by_user_aggregate.c.billable_weighted_tokens,
            by_user_aggregate.c.estimated_cost_usd,
            last_user_event.c.last_used_at,
        )
        .select_from(by_user_aggregate)
        .outerjoin(User, User.id == by_user_aggregate.c.user_id)
        .outerjoin(last_user_event, last_user_event.c.user_id == by_user_aggregate.c.user_id)
        .order_by(by_user_aggregate.c.user_id.asc())
    ).all()

    by_team_rows = db_session.execute(
        select(
            LlmUsageDailyAggregate.team_id,
            func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
            func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
            func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
            func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
        )
        .where(*filters)
        .group_by(LlmUsageDailyAggregate.team_id)
        .order_by(LlmUsageDailyAggregate.team_id.asc())
    ).all()

    by_model_rows = db_session.execute(
        select(
            LlmUsageDailyAggregate.provider,
            LlmUsageDailyAggregate.model,
            func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
            func.sum(LlmUsageDailyAggregate.prompt_tokens).label("prompt_tokens"),
            func.sum(LlmUsageDailyAggregate.completion_tokens).label("completion_tokens"),
            func.sum(LlmUsageDailyAggregate.cached_input_tokens).label("cached_input_tokens"),
            func.sum(LlmUsageDailyAggregate.reasoning_tokens).label("reasoning_tokens"),
            func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
            func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
            func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
        )
        .where(*filters)
        .group_by(LlmUsageDailyAggregate.provider, LlmUsageDailyAggregate.model)
        .order_by(LlmUsageDailyAggregate.provider.asc(), LlmUsageDailyAggregate.model.asc())
    ).all()

    return {
        "totals": _usage_totals_payload(totals),
        "by_period": [
            {"period": row.period, **_usage_totals_payload(row)}
            for row in by_period_rows
        ],
        "by_user": [
            {
                "user_id": row.user_id,
                "email": row.email,
                "name": row.name,
                "request_count": int(row.request_count or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": _serialize_decimal(row.estimated_cost_usd),
                "last_used_at": _serialize_dt(row.last_used_at),
            }
            for row in by_user_rows
        ],
        "by_team": [
            {
                "team_id": row.team_id,
                "request_count": int(row.request_count or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": _serialize_decimal(row.estimated_cost_usd),
            }
            for row in by_team_rows
        ],
        "by_model": [
            {
                "provider": row.provider,
                "model": row.model,
                "request_count": int(row.request_count or 0),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "cached_input_tokens": int(row.cached_input_tokens or 0),
                "reasoning_tokens": int(row.reasoning_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": _serialize_decimal(row.estimated_cost_usd),
            }
            for row in by_model_rows
        ],
    }


@admin_bp.route("/api/admin/users/<int:user_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_user_llm_usage(user_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        filters = [LlmUsageDailyAggregate.user_id == int(user_id)]
        start_date, end_date, error_response, error_status = _usage_date_filters(filters)
        if error_response is not None:
            return error_response, error_status

        with session_scope() as db_session:
            user = db_session.execute(select(User).where(User.id == int(user_id)).limit(1)).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
            event_filters = [LlmUsageEvent.user_id == int(user_id)]
            _usage_event_date_filters(event_filters, start_date, end_date)
            usage = _usage_response(db_session, filters, period, event_filters)

        return jsonify(
            {
                "success": True,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "company_id": user.company_id,
                },
                "period": period,
                "window": {
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                **usage,
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 사용자 LLM 사용량 조회 실패 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_company_llm_usage(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        filters = [LlmUsageDailyAggregate.company_id == int(company_id)]
        start_date, end_date, error_response, error_status = _usage_date_filters(filters)
        if error_response is not None:
            return error_response, error_status

        with session_scope() as db_session:
            company = db_session.execute(select(Company).where(Company.id == int(company_id)).limit(1)).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
            ensure_company_initial_grant(db_session, int(company_id))
            balance = get_company_token_balance(db_session, int(company_id))
            event_filters = [LlmUsageEvent.company_id == int(company_id)]
            _usage_event_date_filters(event_filters, start_date, end_date)
            usage = _usage_response(db_session, filters, period, event_filters)

        return jsonify(
            {
                "success": True,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                },
                "period": period,
                "window": {
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                "remaining_weighted_tokens": balance,
                **usage,
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 LLM 사용량 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_team_llm_usage(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        filters = [LlmUsageDailyAggregate.team_id == int(team_id)]
        start_date, end_date, error_response, error_status = _usage_date_filters(filters)
        if error_response is not None:
            return error_response, error_status

        with session_scope() as db_session:
            team = db_session.execute(select(Team).where(Team.id == int(team_id)).limit(1)).scalar_one_or_none()
            if not team:
                return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
            event_filters = [LlmUsageEvent.team_id == int(team_id)]
            _usage_event_date_filters(event_filters, start_date, end_date)
            usage = _usage_response(db_session, filters, period, event_filters)

        return jsonify(
            {
                "success": True,
                "team": {
                    "id": team.id,
                    "name": team.name,
                    "status": team.status,
                    "plan_code": team.plan_code,
                    "owner_id": team.owner_id,
                },
                "period": period,
                "window": {
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                **usage,
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 LLM 사용량 조회 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/token-balance", methods=["GET"])
@tier_required(["super"])
def get_company_token_balance_route(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        with session_scope() as db_session:
            company = db_session.execute(select(Company).where(Company.id == int(company_id)).limit(1)).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
            ensure_company_initial_grant(db_session, int(company_id))
            balance = get_company_token_balance(db_session, int(company_id))
            granted = db_session.execute(
                select(func.coalesce(func.sum(CompanyTokenLedger.delta_weighted_tokens), 0)).where(
                    CompanyTokenLedger.company_id == int(company_id),
                    CompanyTokenLedger.delta_weighted_tokens > 0,
                )
            ).scalar_one()
            used = db_session.execute(
                select(func.coalesce(func.sum(CompanyTokenLedger.delta_weighted_tokens), 0)).where(
                    CompanyTokenLedger.company_id == int(company_id),
                    CompanyTokenLedger.delta_weighted_tokens < 0,
                )
            ).scalar_one()

        return jsonify(
            {
                "success": True,
                "company": {"id": company.id, "name": company.name, "status": company.status},
                "granted_weighted_tokens": int(granted or 0),
                "used_weighted_tokens": abs(int(used or 0)),
                "remaining_weighted_tokens": balance,
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 토큰 잔액 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/token-topups", methods=["POST"])
@tier_required(["super"])
def create_company_token_topup(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        data = request.json or {}
        weighted_tokens = _to_int_or_none(data.get("weighted_tokens"))
        note = (data.get("note") or "").strip() or None
        if not weighted_tokens or weighted_tokens <= 0:
            return jsonify({"success": False, "error": "weighted_tokens는 1 이상의 정수여야 합니다."}), 400
        created_by = _to_int_or_none(get_jwt_identity())

        with session_scope() as db_session:
            company = db_session.execute(select(Company).where(Company.id == int(company_id)).limit(1)).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
            ensure_company_initial_grant(db_session, int(company_id), created_by=created_by)
            ledger = CompanyTokenLedger(
                company_id=int(company_id),
                delta_weighted_tokens=int(weighted_tokens),
                reason="top_up",
                created_by=created_by,
                note=note,
            )
            db_session.add(ledger)
            db_session.flush()
            balance = get_company_token_balance(db_session, int(company_id))
            ledger_payload = {
                "id": ledger.id,
                "company_id": ledger.company_id,
                "delta_weighted_tokens": ledger.delta_weighted_tokens,
                "reason": ledger.reason,
                "created_by": ledger.created_by,
                "note": ledger.note,
                "created_at": _serialize_dt(ledger.created_at),
            }

        return jsonify(
            {
                "success": True,
                "company": {"id": company.id, "name": company.name, "status": company.status},
                "ledger": ledger_payload,
                "remaining_weighted_tokens": balance,
            }
        ), 201
    except Exception as e:
        log_error(e, f"Admin - 회사 토큰 충전 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/llm-model-prices", methods=["GET"])
@tier_required(["super"])
def list_llm_model_prices():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        provider = (request.args.get("provider") or "").strip().lower()
        active_only = (request.args.get("active_only") or "1").strip() != "0"
        now = datetime.now(timezone.utc)

        with session_scope() as db_session:
            query = select(LlmModelPrice).order_by(
                LlmModelPrice.provider.asc(),
                LlmModelPrice.model.asc(),
                LlmModelPrice.effective_from.desc(),
            )
            if provider:
                query = query.where(LlmModelPrice.provider == provider)
            if active_only:
                query = query.where(
                    LlmModelPrice.effective_from <= now,
                    (LlmModelPrice.effective_to.is_(None) | (LlmModelPrice.effective_to > now)),
                )
            prices = db_session.execute(query).scalars().all()

        return jsonify(
            {
                "success": True,
                "prices": [
                    {
                        "id": price.id,
                        "provider": price.provider,
                        "model": price.model,
                        "effective_from": _serialize_dt(price.effective_from),
                        "effective_to": _serialize_dt(price.effective_to),
                        "currency": price.currency,
                        "input_per_1m": _serialize_decimal(price.input_per_1m),
                        "cached_input_per_1m": _serialize_decimal(price.cached_input_per_1m),
                        "output_per_1m": _serialize_decimal(price.output_per_1m),
                        "reasoning_policy": price.reasoning_policy,
                        "source_url": price.source_url,
                    }
                    for price in prices
                ],
            }
        ), 200
    except Exception as e:
        log_error(e, "Admin - LLM 모델 가격 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/llm-usage-events/expired", methods=["DELETE"])
@tier_required(["super"])
def delete_expired_llm_usage_events():
    try:
        retention_days = _to_int_or_none(request.args.get("retention_days")) or 90
        if retention_days < 1:
            return jsonify({"success": False, "error": "retention_days는 1 이상의 정수여야 합니다."}), 400
        deleted_count = cleanup_old_llm_usage_events(retention_days=retention_days)
        return jsonify(
            {
                "success": True,
                "retention_days": int(retention_days),
                "deleted_count": int(deleted_count),
            }
        ), 200
    except Exception as e:
        log_error(e, "Admin - 만료된 LLM 원본 이벤트 삭제 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies", methods=["GET"])
@tier_required(["super"])
def list_admin_companies():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        page, per_page = _pagination_params()
        search = (request.args.get("search") or "").strip()
        status = (request.args.get("status") or "").strip().lower()

        with session_scope() as db_session:
            query = select(Company).order_by(Company.created_at.desc())
            count_query = select(func.count()).select_from(Company)
            filters = []
            if search:
                filters.append(func.lower(Company.name).like(f"%{search.lower()}%"))
            if status:
                filters.append(Company.status == status)
            if filters:
                query = query.where(and_(*filters))
                count_query = count_query.where(and_(*filters))

            total_count = db_session.execute(count_query).scalar_one() or 0
            companies = db_session.execute(
                query.offset((page - 1) * per_page).limit(per_page)
            ).scalars().all()

            company_ids = [company.id for company in companies]
            member_counts = {}
            owner_counts = {}
            if company_ids:
                member_rows = db_session.execute(
                    select(CompanyMember.company_id, func.count(CompanyMember.id))
                    .where(CompanyMember.company_id.in_(company_ids))
                    .group_by(CompanyMember.company_id)
                ).all()
                member_counts = {row[0]: int(row[1] or 0) for row in member_rows}
                owner_rows = db_session.execute(
                    select(CompanyMember.company_id, func.count(CompanyMember.id))
                    .where(CompanyMember.company_id.in_(company_ids), CompanyMember.role == "owner")
                    .group_by(CompanyMember.company_id)
                ).all()
                owner_counts = {row[0]: int(row[1] or 0) for row in owner_rows}

            items = [
                {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "member_count": member_counts.get(company.id, 0),
                    "owner_count": owner_counts.get(company.id, 0),
                    "usage": _company_usage_summary(db_session, company.id),
                    "created_at": _serialize_dt(company.created_at),
                    "updated_at": _serialize_dt(company.updated_at),
                }
                for company in companies
            ]

        return jsonify(
            {
                "success": True,
                "companies": items,
                "total_count": int(total_count),
                "page": page,
                "per_page": per_page,
                "total_pages": (int(total_count) + per_page - 1) // per_page,
            }
        ), 200
    except Exception as e:
        log_error(e, "Admin - 회사 목록 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>", methods=["GET"])
@tier_required(["super"])
def get_admin_company_detail(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            company = db_session.execute(
                select(Company).where(Company.id == int(company_id)).limit(1)
            ).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404

            memberships = db_session.execute(
                select(CompanyMember)
                .where(CompanyMember.company_id == int(company_id))
                .order_by(CompanyMember.joined_at.asc())
            ).scalars().all()
            user_ids = [row.user_id for row in memberships if row.user_id is not None]
            users_by_id = {}
            usage_by_user_id = {}
            llm_usage_by_user_id = {}
            if user_ids:
                users = db_session.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
                users_by_id = {user.id: user for user in users}
                usage_rows = db_session.execute(
                    select(
                        CompanyUsageEvent.user_id,
                        func.sum(CompanyUsageEvent.total_tokens).label("total_tokens"),
                        func.max(CompanyUsageEvent.occurred_at).label("last_used_at"),
                    )
                    .where(
                        CompanyUsageEvent.company_id == int(company_id),
                        CompanyUsageEvent.user_id.in_(user_ids),
                    )
                    .group_by(CompanyUsageEvent.user_id)
                ).all()
                usage_by_user_id = {
                    row.user_id: {
                        "total_tokens": int(row.total_tokens or 0),
                        "last_used_at": _serialize_dt(row.last_used_at),
                    }
                    for row in usage_rows
                }
                llm_usage_rows = db_session.execute(
                    select(
                        LlmUsageDailyAggregate.user_id,
                        func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0).label("request_count"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.prompt_tokens), 0).label("prompt_tokens"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.completion_tokens), 0).label("completion_tokens"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.cached_input_tokens), 0).label("cached_input_tokens"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.reasoning_tokens), 0).label("reasoning_tokens"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.total_tokens), 0).label("total_tokens"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0).label("billable_weighted_tokens"),
                        func.coalesce(func.sum(LlmUsageDailyAggregate.estimated_cost_usd), 0).label("estimated_cost_usd"),
                    )
                    .where(
                        LlmUsageDailyAggregate.company_id == int(company_id),
                        LlmUsageDailyAggregate.user_id.in_(user_ids),
                    )
                    .group_by(LlmUsageDailyAggregate.user_id)
                ).all()
                last_llm_event_rows = db_session.execute(
                    select(
                        LlmUsageEvent.user_id,
                        func.max(LlmUsageEvent.occurred_at).label("last_used_at"),
                    )
                    .where(
                        LlmUsageEvent.company_id == int(company_id),
                        LlmUsageEvent.user_id.in_(user_ids),
                    )
                    .group_by(LlmUsageEvent.user_id)
                ).all()
                last_llm_used_by_user_id = {
                    row.user_id: _serialize_dt(row.last_used_at)
                    for row in last_llm_event_rows
                }
                llm_usage_by_user_id = {
                    row.user_id: {
                        "request_count": int(row.request_count or 0),
                        "prompt_tokens": int(row.prompt_tokens or 0),
                        "completion_tokens": int(row.completion_tokens or 0),
                        "cached_input_tokens": int(row.cached_input_tokens or 0),
                        "reasoning_tokens": int(row.reasoning_tokens or 0),
                        "total_tokens": int(row.total_tokens or 0),
                        "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                        "estimated_cost_usd": _serialize_decimal(row.estimated_cost_usd),
                        "last_used_at": last_llm_used_by_user_id.get(row.user_id),
                    }
                    for row in llm_usage_rows
                }

            members = []
            for membership in memberships:
                user = users_by_id.get(membership.user_id)
                if not user:
                    continue
                payload = _account_list_payload(db_session, user)
                payload["role"] = membership.role or "member"
                payload["company_role"] = membership.role or "member"
                payload["joined_at"] = _serialize_dt(membership.joined_at)
                payload["usage"] = usage_by_user_id.get(
                    user.id,
                    {"total_tokens": None, "last_used_at": None},
                )
                payload["llm_usage"] = llm_usage_by_user_id.get(user.id, _empty_llm_usage_payload())
                members.append(payload)

            usage = _company_usage_summary(db_session, company.id)

        return jsonify(
            {
                "success": True,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "member_count": len(members),
                    "usage": usage,
                    "created_at": _serialize_dt(company.created_at),
                    "updated_at": _serialize_dt(company.updated_at),
                },
                "members": members,
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 상세 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>", methods=["PUT"])
@tier_required(["super"])
def update_admin_company(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        status = (data.get("status") or "").strip().lower()
        if status not in {"active", "inactive"}:
            return jsonify({"success": False, "error": "status는 active 또는 inactive만 가능합니다."}), 400

        with session_scope() as db_session:
            company = db_session.execute(
                select(Company).where(Company.id == int(company_id)).limit(1)
            ).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
            company.status = status
            db_session.flush()
            payload = {
                "id": company.id,
                "name": company.name,
                "status": company.status,
                "usage": _company_usage_summary(db_session, company.id),
                "created_at": _serialize_dt(company.created_at),
                "updated_at": _serialize_dt(company.updated_at),
            }

        return jsonify({"success": True, "company": payload}), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 수정 실패 (company_id: {company_id})")
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
