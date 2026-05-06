"""
인증 관련 API 라우트 (SQLAlchemy 기반)
"""

from datetime import datetime
from functools import wraps
import json
import os
import traceback

from flask import Blueprint, jsonify, make_response, request
from flask_jwt_extended import (
    create_access_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    unset_jwt_cookies,
)
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy import and_, delete, func, select, update
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from db.engine import session_scope
from db.models.core import Artifact, Company, Project, Study, Team, TeamMember, User
from pii_utils import sanitize_for_log, sanitize_prompt_for_llm


auth_bp = Blueprint("auth", __name__)

BUSINESS_ACCOUNT_TYPE = "business"
INDIVIDUAL_ACCOUNT_TYPE = "individual"
PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/auth/enterprise/change-password",
    "/api/auth/business/change-password",
    "/api/profile",
}
BUSINESS_PROFILE_UPDATE_FIELDS = {"name", "department"}


def _allowed_cors_origin():
    origin = request.headers.get("Origin")
    allowed_origins = set(Config.ALLOWED_ORIGINS or [])
    if origin in allowed_origins:
        return origin
    return Config.FRONTEND_URL


def _apply_account_delete_cors(response, *, methods="DELETE, OPTIONS"):
    response.headers["Access-Control-Allow-Origin"] = _allowed_cors_origin()
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = methods
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-User-ID, x-user-id"
    response.headers.add("Vary", "Origin")
    return response


def log_api_call(endpoint, method, data=None):
    """API 호출 로깅 (개발 환경에서만 상세 출력)"""
    if os.getenv("FLASK_ENV") != "development":
        return
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] API 호출: {method} {endpoint}")
    if data:
        safe_data = sanitize_for_log(data)
        print(f"데이터: {json.dumps(safe_data, ensure_ascii=False, indent=2)}")


def log_error(error, context=""):
    """에러 로깅"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] ❌ 에러 발생: {context}")
    safe_err, _, _ = sanitize_prompt_for_llm(str(error))
    print(f"에러 내용: {safe_err}")
    traceback.print_exc()


def _serialize_dt(value):
    return value.isoformat() if hasattr(value, "isoformat") and value is not None else value


def _db_ready():
    return session_scope is not None


def _normalize_tier(raw_tier: str) -> str:
    tier = (raw_tier or "free").strip().lower()
    if tier == "admin":
        return "super"
    return tier


def _get_company_name(db_session, company_id):
    if not company_id:
        return None
    try:
        return db_session.execute(
            select(Company.name).where(Company.id == int(company_id)).limit(1)
        ).scalar_one_or_none()
    except Exception as exc:
        log_error(exc, "회사명 조회 실패")
        return None


def _build_auth_context(db_session, user: User):
    tier = _normalize_tier(user.tier or "free")
    account_type = user.account_type or (
        BUSINESS_ACCOUNT_TYPE if tier == "enterprise" else INDIVIDUAL_ACCOUNT_TYPE
    )
    claims = {
        "tier": tier,
        "account_type": account_type,
        "password_reset_required": bool(user.password_reset_required),
    }
    if user.company_id and account_type == BUSINESS_ACCOUNT_TYPE:
        claims["company_id"] = int(user.company_id)
    if getattr(user, "department", None):
        claims["department"] = user.department
    return claims


def _build_user_payload(user: User, name_override=None, company_id=None, company_name=None):
    payload = {
        "id": user.id,
        "email": user.email,
        "name": name_override if name_override is not None else user.name,
        "company_id": company_id if company_id is not None else user.company_id,
        "company_name": company_name,
        "department": user.department,
        "google_id": user.google_id,
        "tier": _normalize_tier(user.tier or "free"),
        "account_type": user.account_type or INDIVIDUAL_ACCOUNT_TYPE,
        "password_reset_required": bool(user.password_reset_required),
        "created_at": _serialize_dt(user.created_at),
    }
    return payload


def _auth_response(payload: dict, access_token: str, status_code: int = 200):
    response = jsonify(payload)
    set_access_cookies(response, access_token)
    return response, status_code


def tier_required(allowed_tiers):
    """
    사용자 등급 기반 접근 제어 데코레이터

    계층 구조: free < basic < premium == enterprise < super
    """
    if not isinstance(allowed_tiers, (list, tuple, set)):
        raise ValueError("allowed_tiers must be a list, tuple, or set")

    normalized_allowed_set = {_normalize_tier(t) for t in set(allowed_tiers)}
    tier_levels = {
        "free": 0,
        "basic": 1,
        "premium": 2,
        "enterprise": 2,
        "super": 3,
    }

    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            try:
                claims = get_jwt()
                tier = _normalize_tier(claims.get("tier"))

                if claims.get("password_reset_required") and request.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
                    return jsonify({"error": "Password change required"}), 403

                user_tier_level = tier_levels.get(tier)
                if user_tier_level is None:
                    return jsonify({"error": "Invalid tier", "your_tier": tier}), 403

                if "super" in normalized_allowed_set and tier != "super":
                    return jsonify(
                        {
                            "error": "Insufficient permissions",
                            "your_tier": tier,
                            "required": list(normalized_allowed_set),
                        }
                    ), 403

                if tier == "super":
                    return fn(*args, **kwargs)

                if tier in normalized_allowed_set:
                    return fn(*args, **kwargs)

                min_required_level = min(tier_levels.get(t, 999) for t in normalized_allowed_set)
                if user_tier_level >= min_required_level:
                    return fn(*args, **kwargs)

                return jsonify(
                    {
                        "error": "Insufficient permissions",
                        "your_tier": tier,
                        "required": list(normalized_allowed_set),
                    }
                ), 403
            except Exception as e:
                if isinstance(e, HTTPException):
                    raise
                traceback.print_exc()
                return jsonify({"error": str(e)}), 422

        return wrapper

    return decorator


def get_primary_team_id_for_user(db_session, user_id):
    """
    대표 팀 ID 조회 (SQLAlchemy Session 기반)
    """
    try:
        owner_team_id = db_session.execute(
            select(Team.id)
            .where(Team.owner_id == int(user_id), Team.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()
        if owner_team_id is not None:
            return int(owner_team_id)

        member_team_id = db_session.execute(
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == int(user_id), Team.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()
        return int(member_team_id) if member_team_id is not None else None
    except Exception as exc:
        log_error(exc, "대표 팀 ID 조회 실패")
        return None


@auth_bp.route("/api/login", methods=["POST"])
def login_with_password():
    """이메일/비밀번호 기반 로그인 (레거시). 기본 비활성."""
    data = request.get_json() or {}
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    if not _db_ready():
        return jsonify({"error": "데이터베이스 연결이 필요합니다."}), 500

    # 보안상 기본 비활성. 필요 시 명시적으로 활성화.
    if os.getenv("ENABLE_PASSWORD_LOGIN", "false").lower() != "true":
        return jsonify({"error": "Password login is disabled. Use Google login."}), 501

    shared_secret = os.getenv("PASSWORD_LOGIN_SHARED_SECRET", "")
    if not shared_secret or password != shared_secret:
        return jsonify({"error": "Invalid credentials"}), 401

    log_api_call("/api/login", "POST", {"email": email})
    with session_scope() as db_session:
        user = db_session.execute(select(User).where(User.email == email).limit(1)).scalar_one_or_none()
        if not user:
            return jsonify({"error": "User not found"}), 404

        claims = _build_auth_context(db_session, user)
        access_token = create_access_token(identity=str(user.id), additional_claims=claims)
        user_payload = _build_user_payload(
            user,
            company_name=_get_company_name(db_session, claims.get("company_id")),
            company_id=claims.get("company_id"),
        )

    return _auth_response(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "user": user_payload,
        },
        access_token,
    )


@auth_bp.route("/api/profile", methods=["GET"])
@jwt_required()
def protected_profile():
    user_id = get_jwt_identity()
    claims = get_jwt()
    user_payload = {
        "id": int(user_id) if str(user_id).isdigit() else user_id,
        "tier": _normalize_tier(claims.get("tier")),
        "account_type": claims.get("account_type", INDIVIDUAL_ACCOUNT_TYPE),
        "company_id": claims.get("company_id"),
        "department": claims.get("department"),
        "password_reset_required": bool(claims.get("password_reset_required")),
    }

    if _db_ready():
        try:
            with session_scope() as db_session:
                user = db_session.execute(
                    select(User).where(User.id == int(user_id)).limit(1)
                ).scalar_one_or_none()
                if user:
                    claims_context = _build_auth_context(db_session, user)
                    company_name = _get_company_name(db_session, claims_context.get("company_id"))
                    user_payload = _build_user_payload(
                        user,
                        company_id=claims_context.get("company_id"),
                        company_name=company_name,
                    )
                    user_payload["tier"] = _normalize_tier(claims_context.get("tier"))
        except Exception as exc:
            log_error(exc, "프로필 사용자 조회 실패")

    return jsonify(
        {
            "success": True,
            "user": user_payload,
        }
    ), 200


@auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    response = jsonify({"success": True, "message": "로그아웃되었습니다."})
    unset_jwt_cookies(response)
    return response, 200


@auth_bp.route("/api/premium-feature", methods=["GET"])
@tier_required(["premium"])
def premium_feature():
    return jsonify({"message": "Welcome premium user!"}), 200


@auth_bp.route("/api/auth/test", methods=["GET"])
def test_connection():
    """SQLAlchemy 연결 테스트"""
    try:
        if not _db_ready():
            return jsonify({"success": False, "error": "DB session is not initialized"}), 500

        with session_scope() as db_session:
            count = db_session.execute(select(func.count()).select_from(User)).scalar_one()
            sample = db_session.execute(select(User).limit(1)).scalar_one_or_none()

        return jsonify(
            {
                "success": True,
                "message": "SQLAlchemy DB 연결 성공!",
                "data_count": int(count or 0),
                "sample_data": [_build_user_payload(sample)] if sample else [],
            }
        )
    except Exception as e:
        log_error(e, "DB 연결 테스트")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/check-user", methods=["POST"])
def check_user():
    try:
        data = request.json or {}
        email = data.get("email")
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        log_api_call("/api/auth/check-user", "POST", data)
        with session_scope() as db_session:
            user = db_session.execute(select(User).where(User.email == email).limit(1)).scalar_one_or_none()

        if user:
            return jsonify(
                {
                    "success": True,
                    "exists": True,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.name,
                        "created_at": _serialize_dt(user.created_at),
                    },
                }
            )
        return jsonify({"success": True, "exists": False, "message": "사용자가 존재하지 않습니다."})
    except Exception as e:
        log_error(e, "사용자 확인")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/register", methods=["POST"])
@tier_required(["super"])
def register_user():
    try:
        data = request.json or {}
        email = data.get("email")
        name = data.get("name")
        google_id = data.get("google_id")

        if not email or not name:
            return jsonify({"success": False, "error": "이메일과 이름이 필요합니다."}), 400

        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        log_api_call("/api/auth/register", "POST", data)
        with session_scope() as db_session:
            exists = db_session.execute(select(User.id).where(User.email == email).limit(1)).scalar_one_or_none()
            if exists:
                return jsonify({"success": False, "error": "이미 존재하는 사용자입니다."}), 409

            user = User(
                email=email,
                name=name,
                google_id=google_id,
                tier="free",
                account_type=INDIVIDUAL_ACCOUNT_TYPE,
                password_reset_required=False,
            )
            db_session.add(user)
            db_session.flush()
            db_session.refresh(user)

        return jsonify(
            {
                "success": True,
                "message": "회원가입 성공!",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "created_at": _serialize_dt(user.created_at),
                },
            }
        )
    except Exception as e:
        log_error(e, "사용자 회원가입")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/login", methods=["POST"])
def login_user():
    try:
        data = request.json or {}
        email = data.get("email")
        google_id = data.get("google_id")
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        log_api_call("/api/auth/login", "POST", data)
        with session_scope() as db_session:
            query = select(User).where(User.email == email)
            if google_id:
                query = query.where(User.google_id == google_id)
            user = db_session.execute(query.limit(1)).scalar_one_or_none()

        if not user:
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) == BUSINESS_ACCOUNT_TYPE:
            return jsonify({"success": False, "error": "기업 계정은 business 로그인만 사용할 수 있습니다."}), 403

        return jsonify(
            {
                "success": True,
                "message": "로그인 성공!",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "created_at": _serialize_dt(user.created_at),
                },
            }
        )
    except Exception as e:
        log_error(e, "사용자 로그인")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/users", methods=["GET"])
def get_all_users():
    try:
        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        log_api_call("/api/auth/users", "GET")
        with session_scope() as db_session:
            users = db_session.execute(select(User).order_by(User.created_at.desc())).scalars().all()

        payload = [_build_user_payload(u) for u in users]
        return jsonify({"success": True, "users": payload, "count": len(payload)})
    except Exception as e:
        log_error(e, "사용자 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/google/verify", methods=["POST"])
def verify_google_token():
    """구글 OAuth 토큰 검증 및 사용자 정보 가져오기"""
    try:
        data = request.json or {}
        token = data.get("token")
        if not token:
            return jsonify({"success": False, "error": "구글 토큰이 필요합니다."}), 400
        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        log_api_call("/api/auth/google/verify", "POST", {"token": token[:20] + "..."})
        google_client_id = os.getenv("GOOGLE_CLIENT_ID")
        try:
            idinfo = id_token.verify_oauth2_token(
                token,
                requests.Request(),
                google_client_id,
                clock_skew_in_seconds=10,
            )
            if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
                raise ValueError("Wrong issuer.")

            google_id = idinfo["sub"]
            email = idinfo["email"]
            name = idinfo.get("name", email.split("@")[0])

            with session_scope() as db_session:
                user = db_session.execute(select(User).where(User.email == email).limit(1)).scalar_one_or_none()
                is_new_user = False
                if user:
                    if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) == BUSINESS_ACCOUNT_TYPE:
                        return jsonify(
                            {
                                "success": False,
                                "error": "기업 계정은 Google OAuth 로그인을 사용할 수 없습니다.",
                            }
                        ), 403
                    if not user.google_id:
                        db_session.execute(update(User).where(User.id == user.id).values(google_id=google_id))
                        user.google_id = google_id
                else:
                    user = User(
                        email=email,
                        name=name,
                        google_id=google_id,
                        tier="free",
                        account_type=INDIVIDUAL_ACCOUNT_TYPE,
                        password_reset_required=False,
                    )
                    db_session.add(user)
                    db_session.flush()
                    db_session.refresh(user)
                    is_new_user = True

                claims = _build_auth_context(db_session, user)
                access_token = create_access_token(identity=str(user.id), additional_claims=claims)

            return _auth_response(
                {
                    "success": True,
                    "message": "구글 계정으로 가입 완료!" if is_new_user else "로그인 성공!",
                    "user": _build_user_payload(user, name_override=name, company_name=_get_company_name(db_session, claims.get("company_id"))),
                    "access_token": access_token,
                    "token_type": "bearer",
                    "is_new_user": is_new_user,
                },
                access_token,
            )
        except ValueError as e:
            log_error(e, "구글 토큰 검증 실패")
            return jsonify({"success": False, "error": "유효하지 않은 구글 토큰입니다."}), 401
    except Exception as e:
        log_error(e, "구글 OAuth 토큰 검증")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/google/config", methods=["GET"])
def get_google_config():
    try:
        redirect_uri = f"{Config.FRONTEND_URL}/auth/callback"
        return jsonify(
            {
                "success": True,
                "google_client_id": Config.GOOGLE_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "frontend_url": Config.FRONTEND_URL,
            }
        )
    except Exception as e:
        log_error(e, "구글 설정 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/enterprise/login", methods=["POST"])
@auth_bp.route("/api/auth/business/login", methods=["POST"])
def enterprise_login():
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        if not email or not password:
            return jsonify({"success": False, "error": "이메일과 비밀번호가 필요합니다."}), 400

        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(func.lower(User.email) == email).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) != BUSINESS_ACCOUNT_TYPE:
                return jsonify({"success": False, "error": "일반 계정은 Google OAuth로 로그인해야 합니다."}), 403

            if not user.password_hash or not check_password_hash(user.password_hash, password):
                return jsonify({"success": False, "error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401

            claims = _build_auth_context(db_session, user)
            access_token = create_access_token(identity=str(user.id), additional_claims=claims)
            company_name = _get_company_name(db_session, claims.get("company_id"))

        return _auth_response(
            {
                "success": True,
                "message": "기업 계정 로그인 성공",
                "access_token": access_token,
                "token_type": "bearer",
                "user": _build_user_payload(
                    user,
                    company_id=claims.get("company_id"),
                    company_name=company_name,
                ),
            },
            access_token,
        )
    except Exception as e:
        log_error(e, "기업 로그인 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/enterprise/change-password", methods=["POST"])
@auth_bp.route("/api/auth/business/change-password", methods=["POST"])
@jwt_required()
def enterprise_change_password():
    try:
        claims = get_jwt() or {}
        if claims.get("account_type") != BUSINESS_ACCOUNT_TYPE:
            return jsonify({"success": False, "error": "기업 계정만 비밀번호 변경이 가능합니다."}), 403

        data = request.get_json() or {}
        current_password = data.get("current_password") or ""
        new_password = data.get("new_password") or ""
        if not current_password or not new_password:
            return jsonify({"success": False, "error": "현재 비밀번호와 새 비밀번호가 필요합니다."}), 400
        if len(new_password) < 8:
            return jsonify({"success": False, "error": "새 비밀번호는 8자 이상이어야 합니다."}), 400

        user_id = get_jwt_identity()
        if not user_id:
            return jsonify({"success": False, "error": "인증 정보가 없습니다."}), 401

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == int(user_id)).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) != BUSINESS_ACCOUNT_TYPE:
                return jsonify({"success": False, "error": "기업 계정만 비밀번호 변경이 가능합니다."}), 403
            if not user.password_hash or not check_password_hash(user.password_hash, current_password):
                return jsonify({"success": False, "error": "현재 비밀번호가 올바르지 않습니다."}), 401

            user.password_hash = generate_password_hash(new_password)
            user.password_reset_required = False

            claims = _build_auth_context(db_session, user)
            access_token = create_access_token(identity=str(user.id), additional_claims=claims)
            company_name = _get_company_name(db_session, claims.get("company_id"))

        return _auth_response(
            {
                "success": True,
                "message": "비밀번호가 변경되었습니다.",
                "access_token": access_token,
                "token_type": "bearer",
                "user": _build_user_payload(
                    user,
                    company_id=claims.get("company_id"),
                    company_name=company_name,
                ),
            },
            access_token,
        )
    except Exception as e:
        log_error(e, "기업 계정 비밀번호 변경 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/enterprise/profile", methods=["PUT"])
@auth_bp.route("/api/auth/business/profile", methods=["PUT"])
@jwt_required()
def enterprise_update_profile():
    try:
        claims = get_jwt() or {}
        if claims.get("account_type") != BUSINESS_ACCOUNT_TYPE:
            return jsonify({"success": False, "error": "기업 계정만 프로필 수정이 가능합니다."}), 403
        if claims.get("password_reset_required"):
            return jsonify({"success": False, "error": "비밀번호 변경 후 프로필을 수정할 수 있습니다."}), 403

        user_id = get_jwt_identity()
        user_id_int = int(user_id) if user_id is not None and str(user_id).isdigit() else None
        if user_id_int is None:
            return jsonify({"success": False, "error": "인증 정보가 없습니다."}), 401

        data = request.get_json() or {}
        unknown_fields = sorted(set(data.keys()) - BUSINESS_PROFILE_UPDATE_FIELDS)
        if unknown_fields:
            return jsonify({"success": False, "error": f"수정할 수 없는 필드입니다: {unknown_fields}"}), 400
        if not any(field in data for field in BUSINESS_PROFILE_UPDATE_FIELDS):
            return jsonify({"success": False, "error": "수정할 name 또는 department가 필요합니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) != BUSINESS_ACCOUNT_TYPE:
                return jsonify({"success": False, "error": "기업 계정만 프로필 수정이 가능합니다."}), 403

            if "name" in data:
                name = (data.get("name") or "").strip()
                if not name:
                    return jsonify({"success": False, "error": "name은 비워둘 수 없습니다."}), 400
                user.name = name

            if "department" in data:
                user.department = (data.get("department") or "").strip() or None

            db_session.flush()
            claims_context = _build_auth_context(db_session, user)
            access_token = create_access_token(identity=str(user.id), additional_claims=claims_context)
            company_name = _get_company_name(db_session, claims_context.get("company_id"))
            user_payload = _build_user_payload(
                user,
                company_id=claims_context.get("company_id"),
                company_name=company_name,
            )

        return _auth_response(
            {
                "success": True,
                "message": "프로필이 수정되었습니다.",
                "access_token": access_token,
                "token_type": "bearer",
                "user": user_payload,
            },
            access_token,
        )
    except Exception as e:
        log_error(e, "기업 계정 프로필 수정 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/dev-login", methods=["POST"])
def dev_login():
    """개발용 임시 로그인"""
    try:
        data = request.get_json() or {}
        email = data.get("email", "test@example.com")
        name = data.get("name", "테스트 사용자")
        if not _db_ready():
            return jsonify({"success": False, "error": "데이터베이스 연결 실패"}), 500

        with session_scope() as db_session:
            user = db_session.execute(select(User).where(User.email == email).limit(1)).scalar_one_or_none()
            is_new_user = False
            if not user:
                user = User(
                    email=email,
                    name=name,
                    google_id=f"dev_{email}",
                    tier="free",
                    account_type=INDIVIDUAL_ACCOUNT_TYPE,
                    password_reset_required=False,
                )
                db_session.add(user)
                db_session.flush()
                db_session.refresh(user)
                is_new_user = True

        return jsonify(
            {
                "success": True,
                "message": "개발용 계정 생성 완료!" if is_new_user else "개발용 로그인 성공!",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name or "테스트 사용자",
                    "created_at": _serialize_dt(user.created_at),
                },
                "is_new_user": is_new_user,
            }
        )
    except Exception as e:
        log_error(e, "개발용 로그인")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/account", methods=["DELETE", "OPTIONS"])
def delete_account():
    """
    계정 삭제 API
    - 사용자의 모든 프로젝트, 스터디, 아티팩트 삭제
    - 사용자 정보 삭제
    """
    try:
        if request.method == "OPTIONS":
            response = make_response("", 200)
            return _apply_account_delete_cors(response)
    except Exception as e:
        traceback.print_exc()
        response = make_response("", 500)
        return _apply_account_delete_cors(response)

    try:
        from flask_jwt_extended import verify_jwt_in_request

        verify_jwt_in_request()
    except Exception:
        response = make_response(jsonify({"success": False, "error": "인증이 필요합니다."}), 401)
        return _apply_account_delete_cors(response)

    try:
        if not _db_ready():
            response = make_response(
                jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
            )
            return _apply_account_delete_cors(response)

        user_id = get_jwt_identity()
        if not user_id:
            response = make_response(jsonify({"success": False, "error": "인증 정보가 없습니다."}), 401)
            return _apply_account_delete_cors(response)

        user_id_int = int(user_id)
        with session_scope() as db_session:
            project_ids = db_session.execute(
                select(Project.id).where(Project.owner_id == user_id_int)
            ).scalars().all()

            if project_ids:
                study_ids = db_session.execute(
                    select(Study.id).where(Study.project_id.in_(project_ids))
                ).scalars().all()
            else:
                study_ids = []

            if study_ids:
                total_artifacts = (
                    db_session.execute(
                        select(func.count()).select_from(Artifact).where(Artifact.study_id.in_(study_ids))
                    ).scalar_one()
                    or 0
                )
            else:
                total_artifacts = 0

            total_studies = len(study_ids)
            deleted_projects = len(project_ids)

            # FK cascade 기반으로 사용자 삭제
            db_session.execute(delete(User).where(User.id == user_id_int))

        response = make_response(
            jsonify(
                {
                    "success": True,
                    "message": "계정이 성공적으로 삭제되었습니다.",
                    "deleted_projects": deleted_projects,
                    "deleted_studies": total_studies,
                    "deleted_artifacts": int(total_artifacts),
                }
            ),
            200,
        )
        return _apply_account_delete_cors(response)
    except Exception as e:
        log_error(e, "계정 삭제")
        response = make_response(
            jsonify({"success": False, "error": f"계정 삭제 중 오류가 발생했습니다: {str(e)}"}), 500
        )
        return _apply_account_delete_cors(response)
