"""
데모 로그인 관련 API 라우트
"""

import os

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token

from reopsai_backend.application.demo_service import demo_service
from routes.auth import (
    _auth_response,
    log_api_call,
    log_error,
)


demo_bp = Blueprint("demo", __name__)

# 고정된 데모 URL 경로 (16진수 30자리)
DEMO_SECRET_PATH = os.getenv("DEMO_SECRET_PATH", "abc123def456789012345678901234")
DEMO_PASSWORD = "pxd1105"


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
    if not demo_service.db_ready():
        return jsonify({"error": "데이터베이스 연결이 필요합니다."}), 500

    log_api_call("/demo/login", "POST", {"tier_type": tier_type})

    try:
        result = demo_service.login(tier_type=tier_type)
        if result.status == "account_failed":
            return jsonify({"error": result.error}), 500
        if result.status == "db_unavailable":
            return jsonify({"error": "데이터베이스 연결이 필요합니다."}), 500

        access_token = create_access_token(
            identity=str(result.data["user_id"]),
            additional_claims=result.data["claims"],
        )

        return _auth_response(
            {
                "success": True,
                "access_token": access_token,
                "token_type": "bearer",
                "user": result.data["user"],
            },
            access_token,
        )
    except Exception as exc:
        log_error(exc, "데모 로그인 실패")
        return jsonify({"error": str(exc)}), 500
