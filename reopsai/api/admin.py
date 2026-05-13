"""
Admin API public entrypoint.

The concrete route groups live in sibling modules. This module intentionally
keeps the blueprint, common helpers, and service attributes as the public
surface so existing app factory imports and controller tests can keep using
``reopsai.api.admin.admin_bp`` and monkeypatching the service objects here.
"""

import traceback
from datetime import datetime

from flask import Blueprint, request

from reopsai.application.admin_backoffice_service import admin_backoffice_service
from reopsai.application.admin_service import admin_service
from reopsai.application.admin_usage_service import admin_usage_service


admin_bp = Blueprint("admin", __name__)
ALLOWED_PLAN_CODES = {"starter", "pro", "enterprise_plus"}
ALLOWED_USER_PLAN_CODES = {"free", "basic", "premium"}
USER_PLAN_CODE_ALIASES = {
    "starter": "free",
    "pro": "basic",
    "enterprise_plus": "premium",
}


def log_error(error, context=""):
    """에러 로깅"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] ❌ 에러 발생: {context}")
    print(f"에러 내용: {str(error)}")
    traceback.print_exc()


def _to_int_or_none(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _service_ready(service):
    return getattr(service, "db_ready", lambda: True)()


def _ensure_db():
    return (
        _service_ready(admin_service)
        and _service_ready(admin_usage_service)
        and _service_ready(admin_backoffice_service)
    )


def _parse_iso_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


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


def _pagination_params():
    page = _to_int_or_none(request.args.get("page")) or 1
    per_page = _to_int_or_none(request.args.get("per_page")) or 20
    page = max(1, page)
    per_page = max(1, min(100, per_page))
    return page, per_page


def _validate_plan_code(plan_code, *, required=False):
    value = (plan_code or "").strip().lower()
    if not value:
        if required:
            return None
        return None
    return value if value in ALLOWED_PLAN_CODES else None


# Import route groups after the public blueprint/helpers are defined.
import reopsai.api.admin_accounts  # noqa: E402,F401
import reopsai.api.admin_backoffice  # noqa: E402,F401
import reopsai.api.admin_usage  # noqa: E402,F401


__all__ = [
    "admin_bp",
    "admin_service",
    "admin_usage_service",
    "admin_backoffice_service",
]
