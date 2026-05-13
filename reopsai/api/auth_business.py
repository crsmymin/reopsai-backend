"""Business account auth endpoints."""

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from reopsai.api import auth as auth_module


@auth_module.auth_bp.route("/api/auth/enterprise/login", methods=["POST"])
@auth_module.auth_bp.route("/api/auth/business/login", methods=["POST"])
def enterprise_login():
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        if not email or not password:
            return jsonify({"success": False, "error": "이메일과 비밀번호가 필요합니다."}), 400

        result = auth_module.auth_service.enterprise_login(email=email, password=password)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "individual_forbidden":
            return jsonify({"success": False, "error": "일반 계정은 Google OAuth로 로그인해야 합니다."}), 403
        if result.status == "invalid_password":
            return jsonify({"success": False, "error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401
        return auth_module._with_token(result.data, {"success": True, "message": "기업 계정 로그인 성공"})
    except Exception as exc:
        auth_module.log_error(exc, "기업 로그인 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/enterprise/change-password", methods=["POST"])
@auth_module.auth_bp.route("/api/auth/business/change-password", methods=["POST"])
@jwt_required()
def enterprise_change_password():
    try:
        claims = get_jwt() or {}
        if claims.get("account_type") != auth_module.BUSINESS_ACCOUNT_TYPE:
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

        result = auth_module.auth_service.change_business_password(
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
        return auth_module._with_token(result.data, {"success": True, "message": "비밀번호가 변경되었습니다."})
    except Exception as exc:
        auth_module.log_error(exc, "기업 계정 비밀번호 변경 실패")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/enterprise/profile", methods=["PUT"])
@auth_module.auth_bp.route("/api/auth/business/profile", methods=["PUT"])
@jwt_required()
def enterprise_update_profile():
    try:
        claims = get_jwt() or {}
        if claims.get("account_type") != auth_module.BUSINESS_ACCOUNT_TYPE:
            return jsonify({"success": False, "error": "기업 계정만 프로필 수정이 가능합니다."}), 403
        if claims.get("password_reset_required"):
            return jsonify({"success": False, "error": "비밀번호 변경 후 프로필을 수정할 수 있습니다."}), 403

        user_id = get_jwt_identity()
        user_id_int = int(user_id) if user_id is not None and str(user_id).isdigit() else None
        if user_id_int is None:
            return jsonify({"success": False, "error": "인증 정보가 없습니다."}), 401

        result = auth_module.auth_service.update_business_profile(user_id=user_id_int, data=request.get_json() or {})
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
        return auth_module._with_token(result.data, {"success": True, "message": "프로필이 수정되었습니다."})
    except Exception as exc:
        auth_module.log_error(exc, "기업 계정 프로필 수정 실패")
        return jsonify({"success": False, "error": str(exc)}), 500
