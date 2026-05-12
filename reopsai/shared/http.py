"""HTTP response helpers shared by API controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from flask import jsonify
from flask_jwt_extended import set_access_cookies


@dataclass(frozen=True)
class ApiResult:
    """Small result envelope for application-to-API boundaries."""

    body: Mapping[str, Any]
    status_code: int = 200

    def to_response(self):
        return jsonify(dict(self.body)), self.status_code


def success(payload: Optional[Mapping[str, Any]] = None, status_code: int = 200) -> ApiResult:
    body = {"success": True}
    if payload:
        body.update(payload)
    return ApiResult(body=body, status_code=status_code)


def failure(error: str, status_code: int = 400, **extra: Any) -> ApiResult:
    body = {"success": False, "error": error}
    body.update(extra)
    return ApiResult(body=body, status_code=status_code)


def auth_response(payload: dict, access_token: str, status_code: int = 200):
    response = jsonify(payload)
    set_access_cookies(response, access_token)
    return response, status_code
