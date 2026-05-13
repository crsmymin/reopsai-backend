"""
Auth API public entrypoint.

Concrete auth endpoints live in sibling modules. This module keeps the
blueprint, helper functions, service object, and external auth dependencies as
the stable public surface for app registration and tests.
"""

from datetime import datetime
import json
import os
import traceback

from flask import Blueprint, request
from flask_jwt_extended import create_access_token

try:
    from google.auth.transport import requests
    from google.oauth2 import id_token
except ModuleNotFoundError:
    requests = None
    id_token = None

from config import Config
from reopsai.application.auth_service import (
    auth_service,
    build_auth_context,
    build_user_payload,
    serialize_dt,
)
from reopsai.infrastructure.repositories import BUSINESS_ACCOUNT_TYPE, INDIVIDUAL_ACCOUNT_TYPE
from pii_utils import sanitize_for_log, sanitize_prompt_for_llm
from reopsai.shared.auth import normalize_tier
from reopsai.shared.http import auth_response


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


# Import endpoint groups after the public blueprint/helpers are defined.
import reopsai.api.auth_account  # noqa: E402,F401
import reopsai.api.auth_business  # noqa: E402,F401
import reopsai.api.auth_google  # noqa: E402,F401
import reopsai.api.auth_public  # noqa: E402,F401


__all__ = [
    "auth_bp",
    "auth_service",
    "Config",
    "requests",
    "id_token",
    "BUSINESS_ACCOUNT_TYPE",
    "INDIVIDUAL_ACCOUNT_TYPE",
]
