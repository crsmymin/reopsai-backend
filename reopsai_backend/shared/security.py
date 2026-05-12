"""Security and request lifecycle hooks for the Flask API."""

from __future__ import annotations

import uuid

from flask import g, jsonify, request
from flask_jwt_extended import get_jwt, verify_jwt_in_request

from reopsai_backend.shared.usage_metering import classify_feature_key, is_company_quota_exceeded


PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/auth/enterprise/change-password",
    "/api/auth/business/change-password",
    "/api/profile",
}


def register_jwt_error_handlers(jwt):
    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        print(f"Invalid JWT Token: {error}")
        return jsonify({"error": "Invalid token", "message": str(error)}), 422

    @jwt.unauthorized_loader
    def unauthorized_callback(error):
        print(f"Unauthorized (No JWT): {error}")
        try:
            print(
                "JWT request debug:",
                {
                    "path": request.path,
                    "host": request.host,
                    "origin": request.headers.get("Origin"),
                    "cookie_names": sorted(list(request.cookies.keys())),
                    "has_access_cookie": "access_token_cookie" in request.cookies,
                },
            )
        except Exception:
            pass
        return jsonify({"error": "Missing Authorization Header", "message": str(error)}), 401

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        print("Expired JWT Token")
        return jsonify({"error": "Token has expired", "message": "Please log in again"}), 401

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        print("Revoked JWT Token")
        return jsonify({"error": "Token has been revoked"}), 401


def register_request_guards(app):
    @app.before_request
    def enforce_enterprise_password_change():
        g.request_id = getattr(g, "request_id", None) or uuid.uuid4().hex
        try:
            verify_jwt_in_request(optional=True)
            claims = get_jwt() or {}
            if (
                request.method != "OPTIONS"
                and claims.get("password_reset_required")
                and (request.path or "") not in PASSWORD_CHANGE_ALLOWED_PATHS
            ):
                return jsonify({"error": "Password change required"}), 403
        except Exception:
            return None
        return None

    @app.before_request
    def enforce_business_llm_quota():
        try:
            if request.method == "OPTIONS":
                return None
            verify_jwt_in_request(optional=True)
            claims = get_jwt() or {}
            company_id = claims.get("company_id")
            if claims.get("account_type") != "business" or not company_id:
                return None
            feature_key = classify_feature_key(request.path or "")
            if not feature_key:
                return None
            try:
                company_id_int = int(company_id)
            except Exception:
                return None
            if is_company_quota_exceeded(company_id_int):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "quota_exceeded",
                            "message": "기업의 사용 가능한 weighted token 한도를 초과했습니다.",
                            "remaining_weighted_tokens": 0,
                        }
                    ),
                    402,
                )
        except Exception:
            return None
        return None


def register_security_headers(app):
    @app.after_request
    def set_security_headers(response):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
