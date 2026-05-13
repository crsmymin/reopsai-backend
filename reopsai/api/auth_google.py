"""Google OAuth auth endpoints."""

import os

from flask import jsonify, request

from reopsai.api import auth as auth_module


@auth_module.auth_bp.route("/api/auth/google/verify", methods=["POST"])
def verify_google_token():
    """구글 OAuth 토큰 검증 및 사용자 정보 가져오기"""
    try:
        data = request.json or {}
        token = data.get("token")
        if not token:
            return jsonify({"success": False, "error": "구글 토큰이 필요합니다."}), 400

        auth_module.log_api_call("/api/auth/google/verify", "POST", {"token": token[:20] + "..."})
        google_client_id = os.getenv("GOOGLE_CLIENT_ID")
        try:
            if auth_module.id_token is None or auth_module.requests is None:
                raise RuntimeError("Google auth dependency is not installed.")
            idinfo = auth_module.id_token.verify_oauth2_token(
                token,
                auth_module.requests.Request(),
                google_client_id,
                clock_skew_in_seconds=10,
            )
            if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
                raise ValueError("Wrong issuer.")

            google_id = idinfo["sub"]
            email = idinfo["email"]
            name = idinfo.get("name", email.split("@")[0])
            result = auth_module.auth_service.upsert_google_user(email=email, name=name, google_id=google_id)
            if result.status == "db_unavailable":
                return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
            if result.status == "business_forbidden":
                return jsonify(
                    {"success": False, "error": "기업 계정은 Google OAuth 로그인을 사용할 수 없습니다."}
                ), 403

            return auth_module._with_token(
                result.data,
                {
                    "success": True,
                    "message": "구글 계정으로 가입 완료!" if result.data["is_new_user"] else "로그인 성공!",
                    "is_new_user": result.data["is_new_user"],
                },
            )
        except ValueError as exc:
            auth_module.log_error(exc, "구글 토큰 검증 실패")
            return jsonify({"success": False, "error": "유효하지 않은 구글 토큰입니다."}), 401
    except Exception as exc:
        auth_module.log_error(exc, "구글 OAuth 토큰 검증")
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_module.auth_bp.route("/api/auth/google/config", methods=["GET"])
def get_google_config():
    try:
        redirect_uri = f"{auth_module.Config.FRONTEND_URL}/auth/callback"
        return jsonify(
            {
                "success": True,
                "google_client_id": auth_module.Config.GOOGLE_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "frontend_url": auth_module.Config.FRONTEND_URL,
            }
        )
    except Exception as exc:
        auth_module.log_error(exc, "구글 설정 조회")
        return jsonify({"success": False, "error": str(exc)}), 500
