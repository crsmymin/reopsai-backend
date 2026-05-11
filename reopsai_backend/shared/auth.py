"""Authentication and authorization helpers shared across API controllers."""

from __future__ import annotations

from functools import wraps
import traceback

from flask import jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from werkzeug.exceptions import HTTPException

from reopsai_backend.shared.security import PASSWORD_CHANGE_ALLOWED_PATHS


def normalize_tier(raw_tier: str) -> str:
    tier = (raw_tier or "free").strip().lower()
    if tier == "admin":
        return "super"
    return tier


def tier_required(allowed_tiers):
    """JWT-backed tier guard used by Flask Blueprint endpoints."""

    if not isinstance(allowed_tiers, (list, tuple, set)):
        raise ValueError("allowed_tiers must be a list, tuple, or set")

    normalized_allowed_set = {normalize_tier(t) for t in set(allowed_tiers)}
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
                tier = normalize_tier(claims.get("tier"))

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

                if tier == "super" or tier in normalized_allowed_set:
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
            except Exception as exc:
                if isinstance(exc, HTTPException):
                    raise
                traceback.print_exc()
                return jsonify({"error": str(exc)}), 422

        return wrapper

    return decorator
