"""General auth endpoints for login, profile, users, and development login."""

import os

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required, unset_jwt_cookies

from reopsai.api import auth as auth_module
from reopsai.shared.auth import tier_required


@auth_module.auth_bp.route("/api/login", methods=["POST"])
def login_with_password():
    """이메일/비밀번호 기반 로그인 (레거시). 기본 비활성."""
    try:
        data = request.get_json() or {}
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        auth_module.log_api_call("/api/login", "POST", {"email": email})
        result = auth_module.auth_service.legacy_password_login(
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
        return auth_module._with_token(result.data, {})
    except Exception as exc:
        auth_module.log_error(exc, "레거시 로그인")
        return jsonify({"error": str(exc)}), 500


@auth_module.auth_bp.route("/api/profile", methods=["GET"])
@jwt_required()
def protected_profile():
    try:
        result = auth_module.auth_service.get_profile(user_id=get_jwt_identity(), jwt_claims=get_jwt() or {})
        return jsonify({"success": True, "user": result.data["user"]}), 200
    except Exception as exc:
        auth_module.log_error(exc, "프로필 사용자 조회 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    response = jsonify({"success": True, "message": "로그아웃되었습니다."})
    unset_jwt_cookies(response)
    return response, 200


@auth_module.auth_bp.route("/api/premium-feature", methods=["GET"])
@tier_required(["premium"])
def premium_feature():
    return jsonify({"message": "Welcome premium user!"}), 200


@auth_module.auth_bp.route("/api/auth/test", methods=["GET"])
def test_connection():
    """SQLAlchemy 연결 테스트"""
    try:
        result = auth_module.auth_service.test_connection()
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
        auth_module.log_error(exc, "DB 연결 테스트")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/check-user", methods=["POST"])
def check_user():
    try:
        data = request.json or {}
        email = data.get("email")
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        auth_module.log_api_call("/api/auth/check-user", "POST", data)
        result = auth_module.auth_service.check_user(email=email)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": True, "exists": False, "message": "사용자가 존재하지 않습니다."})
        return jsonify({"success": True, "exists": True, "user": result.data})
    except Exception as exc:
        auth_module.log_error(exc, "사용자 확인")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/register", methods=["POST"])
@tier_required(["super"])
def register_user():
    try:
        data = request.json or {}
        email = data.get("email")
        name = data.get("name")
        google_id = data.get("google_id")

        if not email or not name:
            return jsonify({"success": False, "error": "이메일과 이름이 필요합니다."}), 400

        auth_module.log_api_call("/api/auth/register", "POST", data)
        result = auth_module.auth_service.register_user(email=email, name=name, google_id=google_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "duplicate":
            return jsonify({"success": False, "error": "이미 존재하는 사용자입니다."}), 409
        return jsonify({"success": True, "message": "회원가입 성공!", "user": result.data})
    except Exception as exc:
        auth_module.log_error(exc, "사용자 회원가입")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/login", methods=["POST"])
def login_user():
    try:
        data = request.json or {}
        email = data.get("email")
        google_id = data.get("google_id")
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        auth_module.log_api_call("/api/auth/login", "POST", data)
        result = auth_module.auth_service.login_user(email=email, google_id=google_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "business_forbidden":
            return jsonify({"success": False, "error": "기업 계정은 business 로그인만 사용할 수 있습니다."}), 403
        return jsonify({"success": True, "message": "로그인 성공!", "user": result.data})
    except Exception as exc:
        auth_module.log_error(exc, "사용자 로그인")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/users", methods=["GET"])
def get_all_users():
    try:
        auth_module.log_api_call("/api/auth/users", "GET")
        result = auth_module.auth_service.list_users()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, "users": result.data["users"], "count": result.data["count"]})
    except Exception as exc:
        auth_module.log_error(exc, "사용자 목록 조회")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/dev-login", methods=["POST"])
def dev_login():
    """개발용 임시 로그인"""
    try:
        data = request.get_json() or {}
        result = auth_module.auth_service.dev_login(
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
        auth_module.log_error(exc, "개발용 로그인")
        return jsonify({"success": False, "error": str(exc)}), 500
