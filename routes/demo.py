"""
데모 로그인 관련 API 라우트
"""

import os

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from sqlalchemy import func, select

from db.engine import session_scope
from db.models.core import Company, CompanyMember, User
from routes.auth import (
    _auth_response,
    _build_auth_context,
    _build_user_payload,
    log_api_call,
    log_error,
)
from utils.usage_metering import ensure_company_initial_grant


demo_bp = Blueprint("demo", __name__)

# 고정된 데모 URL 경로 (16진수 30자리)
DEMO_SECRET_PATH = os.getenv("DEMO_SECRET_PATH", "abc123def456789012345678901234")
DEMO_PASSWORD = "pxd1105"


def _serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def get_or_create_individual_demo_account(db_session):
    """
    개인 데모 계정을 조회하거나 생성합니다.
    한 번 생성하면 이후에는 재사용합니다.
    """
    try:
        demo_email = "test@example.com"
        user = db_session.execute(
            select(User).where(func.lower(User.email) == demo_email).limit(1)
        ).scalar_one_or_none()
        if user:
            return user

        user = User(
            email=demo_email,
            google_id=f"dev_{demo_email}",
            tier="free",
            account_type="individual",
        )
        db_session.add(user)
        db_session.flush()
        db_session.refresh(user)
        return user
    except Exception as exc:
        log_error(exc, "Individual 데모 계정 조회/생성 실패")
        return None


def _ensure_business_company(db_session, user):
    company = db_session.execute(
        select(Company).where(func.lower(Company.name) == "demo business").limit(1)
    ).scalar_one_or_none()
    if not company:
        company = Company(name="Demo Business", status="active")
        db_session.add(company)
        db_session.flush()
        db_session.refresh(company)
    ensure_company_initial_grant(db_session, company.id)
    user.company_id = company.id

    membership = db_session.execute(
        select(CompanyMember)
        .where(CompanyMember.company_id == company.id, CompanyMember.user_id == user.id)
        .limit(1)
    ).scalar_one_or_none()
    if membership:
        membership.role = "owner"
    else:
        db_session.add(CompanyMember(company_id=company.id, user_id=user.id, role="owner"))
    db_session.flush()
    return company.id


def get_or_create_enterprise_demo_account(db_session):
    """
    기업 데모 계정을 조회하거나 생성합니다.
    한 번 생성하면 이후에는 재사용합니다.
    """
    try:
        demo_email = "demo-enterprise@test.com"
        user = db_session.execute(
            select(User).where(func.lower(User.email) == demo_email).limit(1)
        ).scalar_one_or_none()

        if user:
            if user.tier != "enterprise":
                user.tier = "enterprise"
            user.account_type = "business"
            _ensure_business_company(db_session, user)
            return user

        user = User(
            email=demo_email,
            google_id=f"dev_{demo_email}",
            tier="enterprise",
            account_type="business",
        )
        db_session.add(user)
        db_session.flush()
        db_session.refresh(user)

        try:
            _ensure_business_company(db_session, user)
        except Exception as exc:
            log_error(exc, "Enterprise 데모 계정 팀 생성 실패")

        return user
    except Exception as exc:
        log_error(exc, "Enterprise 데모 계정 조회/생성 실패")
        return None


@demo_bp.route("/api/demo/verify", methods=["POST"])
def demo_verify_password():
    """데모 패스워드 검증"""
    data = request.get_json() or {}
    password = data.get("password")
    if password != DEMO_PASSWORD:
        return jsonify({"error": "Invalid password"}), 401
    return jsonify({"success": True, "message": "Password verified"}), 200


@demo_bp.route("/api/demo/login", methods=["POST"])
def demo_login():
    """데모 로그인 - 티어 선택 후 JWT 발급"""
    data = request.get_json() or {}
    password = data.get("password")
    tier_type = data.get("tier_type")

    if password != DEMO_PASSWORD:
        return jsonify({"error": "Invalid password"}), 401
    if tier_type not in ["individual", "enterprise"]:
        return jsonify({"error": "Invalid tier type"}), 400
    if session_scope is None:
        return jsonify({"error": "데이터베이스 연결이 필요합니다."}), 500

    log_api_call("/demo/login", "POST", {"tier_type": tier_type})

    try:
        user_id = None
        user_payload = {}

        with session_scope() as db_session:
            if tier_type == "individual":
                user = get_or_create_individual_demo_account(db_session)
                if not user:
                    return jsonify({"error": "Failed to get or create individual demo account"}), 500
                user_id = user.id
            else:
                user = get_or_create_enterprise_demo_account(db_session)
                if not user:
                    return jsonify({"error": "Failed to get or create enterprise demo account"}), 500
                user_id = user.id
            claims = _build_auth_context(db_session, user)
            user_payload = _build_user_payload(user)

        access_token = create_access_token(identity=str(user_id), additional_claims=claims)

        return _auth_response(
            {
                "success": True,
                "access_token": access_token,
                "token_type": "bearer",
                "user": user_payload,
            },
            access_token,
        )
    except Exception as exc:
        log_error(exc, "데모 로그인 실패")
        return jsonify({"error": str(exc)}), 500
