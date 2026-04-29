"""
B2B(Business) company management routes.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity
from sqlalchemy import and_, func, select, update
from werkzeug.security import generate_password_hash

from api_logger import log_error
from db.engine import session_scope
from db.models.core import Company, CompanyMember, CompanyTokenLedger, LlmUsageDailyAggregate, LlmUsageEvent, User
from routes.auth import tier_required
from utils.usage_metering import ensure_company_initial_grant, get_company_token_balance


b2b_bp = Blueprint("b2b", __name__, url_prefix="/api/b2b")
DEFAULT_BUSINESS_PASSWORD = "0000"
BUSINESS_ACCOUNT_TYPE = "business"


def _serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _serialize_decimal(value):
    return float(value or 0)


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


def _usage_period_column(period: str):
    if period == "monthly":
        return func.to_char(func.date_trunc("month", LlmUsageDailyAggregate.usage_date), "YYYY-MM")
    return func.to_char(LlmUsageDailyAggregate.usage_date, "YYYY-MM-DD")


def _plan_payload_for_user(user: User):
    code = (user.tier or "enterprise").strip().lower()
    if code == "admin":
        code = "super"
    plan_names = {
        "free": "Free Plan",
        "basic": "Basic Plan",
        "premium": "Premium Plan",
        "enterprise": "Enterprise Plan",
        "super": "Super Admin",
    }
    return {"code": code, "name": plan_names.get(code, f"{code.title()} Plan")}


def _get_identity_int():
    identity = get_jwt_identity()
    try:
        return int(identity) if identity is not None else None
    except Exception:
        return identity


def _company_name_for(db_session, company_id):
    if not company_id:
        return None
    return db_session.execute(
        select(Company.name).where(Company.id == int(company_id)).limit(1)
    ).scalar_one_or_none()


def _get_my_company_id(db_session, user_id_int):
    claims = get_jwt() or {}
    company_id = claims.get("company_id")
    if company_id:
        return int(company_id)
    user = db_session.execute(select(User).where(User.id == int(user_id_int)).limit(1)).scalar_one_or_none()
    return int(user.company_id) if user and user.company_id else None


def _get_membership(db_session, company_id, user_id):
    return db_session.execute(
        select(CompanyMember)
        .where(
            and_(
                CompanyMember.company_id == int(company_id),
                CompanyMember.user_id == int(user_id),
            )
        )
        .limit(1)
    ).scalar_one_or_none()


def _require_owner(db_session, company_id, user_id):
    membership = _get_membership(db_session, company_id, user_id)
    return membership if membership and (membership.role or "member") == "owner" else None


def _member_payload(user: User, membership: CompanyMember):
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "department": user.department,
        "tier": user.tier or "free",
        "account_type": user.account_type or "individual",
        "role": membership.role or "member",
        "joined_at": _serialize_dt(membership.joined_at),
        "password_reset_required": bool(user.password_reset_required),
    }


@b2b_bp.route("/membership/usage", methods=["GET"])
@tier_required(["enterprise"])
def b2b_get_membership_usage():
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date = _parse_usage_date(request.args.get("start_date"))
        end_date = _parse_usage_date(request.args.get("end_date"))
        if request.args.get("start_date") and start_date is None:
            return jsonify({"success": False, "error": "start_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
        if request.args.get("end_date") and end_date is None:
            return jsonify({"success": False, "error": "end_date는 YYYY-MM-DD 형식이어야 합니다."}), 400

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
            owner_membership = _require_owner(db_session, company_id, user_id_int)
            if not owner_membership:
                return jsonify({"success": False, "error": "멤버십 사용량은 회사 owner만 조회할 수 있습니다."}), 403

            company = db_session.execute(
                select(Company).where(Company.id == int(company_id), Company.status != "deleted").limit(1)
            ).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사 정보를 찾을 수 없습니다."}), 404

            owner = db_session.execute(select(User).where(User.id == int(user_id_int)).limit(1)).scalar_one_or_none()
            ensure_company_initial_grant(db_session, int(company_id), created_by=int(user_id_int))

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
            balance = get_company_token_balance(db_session, int(company_id))

            filters = [LlmUsageDailyAggregate.company_id == int(company_id)]
            if start_date:
                filters.append(LlmUsageDailyAggregate.usage_date >= start_date)
            if end_date:
                filters.append(LlmUsageDailyAggregate.usage_date <= end_date)

            totals = db_session.execute(
                select(
                    func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0).label("request_count"),
                    func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0).label("billable_weighted_tokens"),
                ).where(*filters)
            ).one()

            period_col = _usage_period_column(period).label("period")
            by_period_rows = db_session.execute(
                select(
                    period_col,
                    func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                    func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
                )
                .where(*filters)
                .group_by(period_col)
                .order_by(period_col.asc())
            ).all()

            by_user_aggregate = (
                select(
                    LlmUsageDailyAggregate.user_id.label("user_id"),
                    func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                    func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
                )
                .where(*filters)
                .group_by(LlmUsageDailyAggregate.user_id)
                .subquery()
            )

            event_filters = [LlmUsageEvent.company_id == int(company_id)]
            if start_date:
                event_filters.append(LlmUsageEvent.occurred_at >= datetime.combine(start_date, datetime.min.time()))
            if end_date:
                event_filters.append(LlmUsageEvent.occurred_at <= datetime.combine(end_date, datetime.max.time()))
            last_user_event = (
                select(
                    LlmUsageEvent.user_id.label("user_id"),
                    func.max(LlmUsageEvent.occurred_at).label("last_used_at"),
                )
                .where(*event_filters)
                .group_by(LlmUsageEvent.user_id)
                .subquery()
            )

            by_user_rows = db_session.execute(
                select(
                    by_user_aggregate.c.user_id,
                    User.email,
                    User.name,
                    User.department,
                    by_user_aggregate.c.request_count,
                    by_user_aggregate.c.billable_weighted_tokens,
                    last_user_event.c.last_used_at,
                )
                .select_from(by_user_aggregate)
                .outerjoin(User, User.id == by_user_aggregate.c.user_id)
                .outerjoin(last_user_event, last_user_event.c.user_id == by_user_aggregate.c.user_id)
                .order_by(by_user_aggregate.c.billable_weighted_tokens.desc(), by_user_aggregate.c.user_id.asc())
            ).all()

        return jsonify(
            {
                "success": True,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                },
                "plan": _plan_payload_for_user(owner) if owner else {"code": "enterprise", "name": "Enterprise Plan"},
                "token_balance": {
                    "granted_weighted_tokens": int(granted or 0),
                    "used_weighted_tokens": abs(int(used or 0)),
                    "remaining_weighted_tokens": int(balance or 0),
                },
                "period": period,
                "window": {
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                "totals": {
                    "request_count": int(totals.request_count or 0),
                    "billable_weighted_tokens": int(totals.billable_weighted_tokens or 0),
                },
                "by_period": [
                    {
                        "period": row.period,
                        "request_count": int(row.request_count or 0),
                        "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                    }
                    for row in by_period_rows
                ],
                "by_user": [
                    {
                        "user_id": row.user_id,
                        "email": row.email,
                        "name": row.name,
                        "department": row.department,
                        "request_count": int(row.request_count or 0),
                        "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                        "last_used_at": _serialize_dt(row.last_used_at),
                    }
                    for row in by_user_rows
                ],
            }
        ), 200
    except Exception as e:
        log_error(e, "B2B - 멤버십 사용량 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team", methods=["GET"])
@tier_required(["enterprise"])
def b2b_get_my_team():
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404

            company = db_session.execute(
                select(Company).where(Company.id == int(company_id), Company.status != "deleted").limit(1)
            ).scalar_one_or_none()
            if not company:
                return jsonify({"success": False, "error": "회사 정보를 찾을 수 없습니다."}), 404

            member_rows = db_session.execute(
                select(CompanyMember)
                .where(CompanyMember.company_id == int(company_id))
                .order_by(CompanyMember.joined_at.asc())
            ).scalars().all()
            users_by_id = {}
            member_user_ids = [row.user_id for row in member_rows if row.user_id is not None]
            if member_user_ids:
                users = db_session.execute(select(User).where(User.id.in_(member_user_ids))).scalars().all()
                users_by_id = {u.id: u for u in users}

            members = [
                _member_payload(users_by_id[row.user_id], row)
                for row in member_rows
                if row.user_id in users_by_id
            ]

        return jsonify(
            {
                "success": True,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "created_at": _serialize_dt(company.created_at),
                },
                "members": members,
            }
        )
    except Exception as e:
        log_error(e, "B2B - 회사 정보 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members", methods=["POST"])
@tier_required(["enterprise"])
def b2b_add_team_member():
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        role = (data.get("role") or "member").strip().lower()
        department = (data.get("department") or "").strip() or None
        if role not in {"owner", "member"}:
            role = "member"
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
            if not _require_owner(db_session, company_id, user_id_int):
                return jsonify({"success": False, "error": "회사 멤버 추가는 owner만 가능합니다."}), 403

            target_user = db_session.execute(
                select(User).where(func.lower(User.email) == email).limit(1)
            ).scalar_one_or_none()
            if not target_user:
                return jsonify({"success": False, "error": "해당 이메일로 가입된 사용자가 없습니다."}), 404

            target_user.tier = "enterprise"
            target_user.account_type = BUSINESS_ACCOUNT_TYPE
            target_user.company_id = company_id
            if department is not None:
                target_user.department = department

            existing = _get_membership(db_session, company_id, target_user.id)
            if existing:
                existing.role = role
            else:
                db_session.add(CompanyMember(company_id=int(company_id), user_id=target_user.id, role=role))
            if role == "owner":
                db_session.execute(
                    update(CompanyMember)
                    .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id != target_user.id))
                    .values(role="member")
                )
            db_session.flush()

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 회사 멤버 추가 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>", methods=["PUT"])
@tier_required(["enterprise"])
def b2b_update_team_member(member_user_id: int):
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401
        if int(member_user_id) == int(user_id_int):
            return jsonify({"success": False, "error": "본인 정보는 /api/auth/business/profile에서 수정해주세요."}), 400

        data = request.get_json() or {}
        allowed_fields = {"name", "department"}
        unknown_fields = sorted(set(data.keys()) - allowed_fields)
        if unknown_fields:
            return jsonify({"success": False, "error": f"수정할 수 없는 필드입니다: {unknown_fields}"}), 400
        if not any(field in data for field in allowed_fields):
            return jsonify({"success": False, "error": "수정할 name 또는 department가 필요합니다."}), 400

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
            if not _require_owner(db_session, company_id, user_id_int):
                return jsonify({"success": False, "error": "멤버 정보 수정은 owner만 가능합니다."}), 403

            membership = _get_membership(db_session, company_id, member_user_id)
            if not membership:
                return jsonify({"success": False, "error": "같은 회사 소속 멤버만 수정할 수 있습니다."}), 403
            if (membership.role or "member") == "owner":
                return jsonify({"success": False, "error": "owner 계정은 이 API로 수정할 수 없습니다."}), 400

            target_user = db_session.execute(select(User).where(User.id == int(member_user_id)).limit(1)).scalar_one_or_none()
            if not target_user:
                return jsonify({"success": False, "error": "수정할 멤버를 찾을 수 없습니다."}), 404
            if (target_user.account_type or "") != BUSINESS_ACCOUNT_TYPE:
                return jsonify({"success": False, "error": "기업 계정 멤버만 수정할 수 있습니다."}), 400

            if "name" in data:
                name = (data.get("name") or "").strip()
                if not name:
                    return jsonify({"success": False, "error": "name은 비워둘 수 없습니다."}), 400
                target_user.name = name
            if "department" in data:
                target_user.department = (data.get("department") or "").strip() or None
            db_session.flush()
            payload = _member_payload(target_user, membership)

        return jsonify({"success": True, "message": "멤버 정보가 수정되었습니다.", "user": payload}), 200
    except Exception as e:
        log_error(e, "B2B - 멤버 정보 수정 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>/reset-password", methods=["POST"])
@tier_required(["enterprise"])
def b2b_reset_team_member_password(member_user_id: int):
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401
        if int(member_user_id) == int(user_id_int):
            return jsonify({"success": False, "error": "본인 비밀번호는 이 API로 초기화할 수 없습니다."}), 400

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
            if not _require_owner(db_session, company_id, user_id_int):
                return jsonify({"success": False, "error": "비밀번호 초기화는 owner만 가능합니다."}), 403

            membership = _get_membership(db_session, company_id, member_user_id)
            if not membership:
                return jsonify({"success": False, "error": "같은 회사 소속 멤버만 초기화할 수 있습니다."}), 403
            if (membership.role or "member") == "owner":
                return jsonify({"success": False, "error": "owner 계정은 이 API로 초기화할 수 없습니다."}), 403

            target_user = db_session.execute(select(User).where(User.id == int(member_user_id)).limit(1)).scalar_one_or_none()
            if not target_user:
                return jsonify({"success": False, "error": "대상 사용자를 찾을 수 없습니다."}), 404
            if (target_user.tier or "").strip().lower() == "super":
                return jsonify({"success": False, "error": "super 계정은 초기화할 수 없습니다."}), 403
            if (target_user.account_type or "") != BUSINESS_ACCOUNT_TYPE:
                return jsonify({"success": False, "error": "기업 계정 멤버만 초기화할 수 있습니다."}), 400

            target_user.password_hash = generate_password_hash(DEFAULT_BUSINESS_PASSWORD)
            target_user.password_reset_required = True
            db_session.flush()
            payload = _member_payload(target_user, membership)

        return jsonify(
            {
                "success": True,
                "message": "비밀번호가 초기화되었습니다.",
                "temporary_password": DEFAULT_BUSINESS_PASSWORD,
                "user": payload,
            }
        ), 200
    except Exception as e:
        log_error(e, "B2B - 멤버 비밀번호 초기화 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def b2b_remove_team_member(member_user_id: int):
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
            if not _require_owner(db_session, company_id, user_id_int):
                return jsonify({"success": False, "error": "멤버 삭제는 owner만 가능합니다."}), 403
            if int(member_user_id) == int(user_id_int):
                return jsonify({"success": False, "error": "owner 본인은 삭제할 수 없습니다."}), 400

            membership = _get_membership(db_session, company_id, member_user_id)
            if not membership:
                return jsonify({"success": True})
            if (membership.role or "member") == "owner":
                return jsonify({"success": False, "error": "owner 계정은 삭제할 수 없습니다."}), 400
            db_session.delete(membership)

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 멤버 삭제 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>/role", methods=["POST"])
@tier_required(["enterprise"])
def b2b_change_team_member_role(member_user_id: int):
    try:
        if session_scope is None:
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _get_identity_int()
        if not user_id_int:
            return jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401

        data = request.get_json() or {}
        new_role = (data.get("role") or "member").strip().lower()
        if new_role != "owner":
            return jsonify({"success": False, "error": "현재는 owner로 변경만 지원합니다."}), 400

        with session_scope() as db_session:
            company_id = _get_my_company_id(db_session, user_id_int)
            if not company_id:
                return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
            if not _require_owner(db_session, company_id, user_id_int):
                return jsonify({"success": False, "error": "권한 변경은 owner만 가능합니다."}), 403
            if int(member_user_id) == int(user_id_int):
                return jsonify({"success": False, "error": "본인을 대상으로 권한을 변경할 수 없습니다."}), 400

            membership = _get_membership(db_session, company_id, member_user_id)
            if not membership:
                return jsonify({"success": False, "error": "같은 회사 소속 멤버만 owner로 변경할 수 있습니다."}), 403
            membership.role = "owner"
            db_session.execute(
                update(CompanyMember)
                .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id != int(member_user_id)))
                .values(role="member")
            )
            db_session.execute(
                update(User)
                .where(User.id == int(member_user_id))
                .values(tier="enterprise", account_type=BUSINESS_ACCOUNT_TYPE, company_id=int(company_id))
            )

        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 멤버 권한 변경 실패")
        return jsonify({"success": False, "error": str(e)}), 500
