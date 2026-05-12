"""Compatibility wrapper. Prefer reopsai_backend.api.auth."""

from reopsai_backend.api.auth import auth_bp

__all__ = ["auth_bp"]
