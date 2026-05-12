"""
인증 관련 API 라우트 (SQLAlchemy 기반)
"""

from datetime import datetime
import json
import os
import traceback

from flask import Blueprint, jsonify, make_response, request
from flask_jwt_extended import (
    create_access_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    unset_jwt_cookies,
    verify_jwt_in_request,
)
try:
    from google.auth.transport import requests
    from google.oauth2 import id_token
except ModuleNotFoundError:
    requests = None
    id_token = None

from config import Config
from reopsai_backend.infrastructure.repositories import BUSINESS_ACCOUNT_TYPE, INDIVIDUAL_ACCOUNT_TYPE
from pii_utils import sanitize_for_log, sanitize_prompt_for_llm
from reopsai_backend.application.auth_service import (
    auth_service,
    build_auth_context,
    build_user_payload,
    serialize_dt,
)
from reopsai_backend.shared.auth import normalize_tier, tier_required
from reopsai_backend.shared.http import auth_response


auth_bp = Blueprint("auth", __name__)


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
    return serialize_dt(value)


def _normalize_tier(raw_tier: str) -> str:
    return normalize_tier(raw_tier)


def _build_auth_context(db_session, user):
    return build_auth_context(user)


def _build_user_payload(user, name_override=None, company_id=None, company_name=None):
    return build_user_payload(
        user,
        name_override=name_override,
        company_id=company_id,
        company_name=company_name,
    )


def get_primary_team_id_for_user(db_session, user_id):
    """
    대표 팀 ID 조회 (SQLAlchemy Session 기반)
    """
    try:
        return auth_service.get_primary_team_id_for_user(db_session, user_id)
    except Exception as exc:
        log_error(exc, "대표 팀 ID 조회 실패")
        return None


def _with_token(auth_payload, response_payload, status_code=200):
    claims = auth_payload["claims"]
    user = auth_payload["user"]
    access_token = create_access_token(identity=str(user["id"]), additional_claims=claims)
    response_payload.update(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "user": user,
        }
    )
    return auth_response(response_payload, access_token, status_code)


@auth_bp.route("/api/login", methods=["POST"])
def login_with_password():
    """이메일/비밀번호 기반 로그인 (레거시). 기본 비활성."""
    try:
        data = request.get_json() or {}
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        log_api_call("/api/login", "POST", {"email": email})
        result = auth_service.legacy_password_login(
            email=email,
            password=password,
            enabled=os.getenv("ENABLE_PASSWORD_LOGIN", "false").lower() == "true",
            shared_secret=os.getenv("PASSWORD_LOGIN_SHARED_SECRET", ""),
        )
        if result.status == "db_unavailable":
            return jsonify({"error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "disabled":
            return jsonify({"error": "Password login is disabled. Use Google login."}), 501
        if result.status == "invalid_credentials":
            return jsonify({"error": "Invalid credentials"}), 401
        if result.status == "not_found":
            return jsonify({"error": "User not found"}), 404
        return _with_token(result.data, {})
    except Exception as exc:
        log_error(exc, "레거시 로그인")
        return jsonify({"error": str(exc)}), 500


@auth_bp.route("/api/profile", methods=["GET"])
@jwt_required()
def protected_profile():
    try:
        result = auth_service.get_profile(user_id=get_jwt_identity(), jwt_claims=get_jwt() or {})
        return jsonify({"success": True, "user": result.data["user"]}), 200
    except Exception as exc:
        log_error(exc, "프로필 사용자 조회 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


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
        result = auth_service.test_connection()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "DB session is not initialized"}), 500
        return jsonify(
            {
                "success": True,
                "message": "SQLAlchemy DB 연결 성공!",
                "data_count": result.data["data_count"],
                "sample_data": result.data["sample_data"],
            }
        )
    except Exception as exc:
        log_error(exc, "DB 연결 테스트")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route("/api/auth/check-user", methods=["POST"])
def check_user():
    try:
        data = request.json or {}
        email = data.get("email")
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        log_api_call("/api/auth/check-user", "POST", data)
        result = auth_service.check_user(email=email)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": True, "exists": False, "message": "사용자가 존재하지 않습니다."})
        return jsonify({"success": True, "exists": True, "user": result.data})
    except Exception as exc:
        log_error(exc, "사용자 확인")
        return jsonify({"success": False, "error": str(exc)}), 500


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

        log_api_call("/api/auth/register", "POST", data)
        result = auth_service.register_user(email=email, name=name, google_id=google_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "duplicate":
            return jsonify({"success": False, "error": "이미 존재하는 사용자입니다."}), 409
        return jsonify({"success": True, "message": "회원가입 성공!", "user": result.data})
    except Exception as exc:
        log_error(exc, "사용자 회원가입")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route("/api/auth/login", methods=["POST"])
def login_user():
    try:
        data = request.json or {}
        email = data.get("email")
        google_id = data.get("google_id")
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        log_api_call("/api/auth/login", "POST", data)
        result = auth_service.login_user(email=email, google_id=google_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "business_forbidden":
            return jsonify({"success": False, "error": "기업 계정은 business 로그인만 사용할 수 있습니다."}), 403
        return jsonify({"success": True, "message": "로그인 성공!", "user": result.data})
    except Exception as exc:
        log_error(exc, "사용자 로그인")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route("/api/auth/users", methods=["GET"])
def get_all_users():
    try:
        log_api_call("/api/auth/users", "GET")
        result = auth_service.list_users()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, "users": result.data["users"], "count": result.data["count"]})
    except Exception as exc:
        log_error(exc, "사용자 목록 조회")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route("/api/auth/google/verify", methods=["POST"])
def verify_google_token():
    """구글 OAuth 토큰 검증 및 사용자 정보 가져오기"""
    try:
        data = request.json or {}
        token = data.get("token")
        if not token:
            return jsonify({"success": False, "error": "구글 토큰이 필요합니다."}), 400

        log_api_call("/api/auth/google/verify", "POST", {"token": token[:20] + "..."})
        google_client_id = os.getenv("GOOGLE_CLIENT_ID")
        try:
            if id_token is None or requests is None:
                raise RuntimeError("Google auth dependency is not installed.")
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
            result = auth_service.upsert_google_user(email=email, name=name, google_id=google_id)
            if result.status == "db_unavailable":
                return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
            if result.status == "business_forbidden":
                return jsonify(
                    {"success": False, "error": "기업 계정은 Google OAuth 로그인을 사용할 수 없습니다."}
                ), 403

            return _with_token(
                result.data,
                {
                    "success": True,
                    "message": "구글 계정으로 가입 완료!" if result.data["is_new_user"] else "로그인 성공!",
                    "is_new_user": result.data["is_new_user"],
                },
            )
        except ValueError as exc:
            log_error(exc, "구글 토큰 검증 실패")
            return jsonify({"success": False, "error": "유효하지 않은 구글 토큰입니다."}), 401
    except Exception as exc:
        log_error(exc, "구글 OAuth 토큰 검증")
        return jsonify({"success": False, "error": str(exc)}), 500


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
    except Exception as exc:
        log_error(exc, "구글 설정 조회")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route("/api/auth/enterprise/login", methods=["POST"])
@auth_bp.route("/api/auth/business/login", methods=["POST"])
def enterprise_login():
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        if not email or not password:
            return jsonify({"success": False, "error": "이메일과 비밀번호가 필요합니다."}), 400

        result = auth_service.enterprise_login(email=email, password=password)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "individual_forbidden":
            return jsonify({"success": False, "error": "일반 계정은 Google OAuth로 로그인해야 합니다."}), 403
        if result.status == "invalid_password":
            return jsonify({"success": False, "error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401
        return _with_token(result.data, {"success": True, "message": "기업 계정 로그인 성공"})
    except Exception as exc:
        log_error(exc, "기업 로그인 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


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

        result = auth_service.change_business_password(
            user_id=user_id,
            current_password=current_password,
            new_password=new_password,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "business_only":
            return jsonify({"success": False, "error": "기업 계정만 비밀번호 변경이 가능합니다."}), 403
        if result.status == "invalid_current_password":
            return jsonify({"success": False, "error": "현재 비밀번호가 올바르지 않습니다."}), 401
        return _with_token(result.data, {"success": True, "message": "비밀번호가 변경되었습니다."})
    except Exception as exc:
        log_error(exc, "기업 계정 비밀번호 변경 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


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

        result = auth_service.update_business_profile(user_id=user_id_int, data=request.get_json() or {})
        if result.status == "unknown_fields":
            return jsonify({"success": False, "error": f"수정할 수 없는 필드입니다: {result.data}"}), 400
        if result.status == "empty_update":
            return jsonify({"success": False, "error": "수정할 name 또는 department가 필요합니다."}), 400
        if result.status == "empty_name":
            return jsonify({"success": False, "error": "name은 비워둘 수 없습니다."}), 400
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "business_only":
            return jsonify({"success": False, "error": "기업 계정만 프로필 수정이 가능합니다."}), 403
        return _with_token(result.data, {"success": True, "message": "프로필이 수정되었습니다."})
    except Exception as exc:
        log_error(exc, "기업 계정 프로필 수정 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route("/api/auth/dev-login", methods=["POST"])
def dev_login():
    """개발용 임시 로그인"""
    try:
        data = request.get_json() or {}
        result = auth_service.dev_login(
            email=data.get("email", "test@example.com"),
            name=data.get("name", "테스트 사용자"),
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결 실패"}), 500
        return jsonify(
            {
                "success": True,
                "message": "개발용 계정 생성 완료!" if result.data["is_new_user"] else "개발용 로그인 성공!",
                "user": result.data["user"],
                "is_new_user": result.data["is_new_user"],
            }
        )
    except Exception as exc:
        log_error(exc, "개발용 로그인")
        return jsonify({"success": False, "error": str(exc)}), 500


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
    except Exception:
        traceback.print_exc()
        response = make_response("", 500)
        return _apply_account_delete_cors(response)

    try:
        verify_jwt_in_request()
    except Exception:
        response = make_response(jsonify({"success": False, "error": "인증이 필요합니다."}), 401)
        return _apply_account_delete_cors(response)

    try:
        user_id = get_jwt_identity()
        if not user_id:
            response = make_response(jsonify({"success": False, "error": "인증 정보가 없습니다."}), 401)
            return _apply_account_delete_cors(response)

        result = auth_service.delete_account(user_id=user_id)
        if result.status == "db_unavailable":
            response = make_response(
                jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
            )
            return _apply_account_delete_cors(response)

        response = make_response(
            jsonify(
                {
                    "success": True,
                    "message": "계정이 성공적으로 삭제되었습니다.",
                    "deleted_projects": result.data["deleted_projects"],
                    "deleted_studies": result.data["deleted_studies"],
                    "deleted_artifacts": result.data["deleted_artifacts"],
                }
            ),
            200,
        )
        return _apply_account_delete_cors(response)
    except Exception as exc:
        log_error(exc, "계정 삭제")
        response = make_response(
            jsonify({"success": False, "error": f"계정 삭제 중 오류가 발생했습니다: {str(exc)}"}), 500
        )
        return _apply_account_delete_cors(response)
