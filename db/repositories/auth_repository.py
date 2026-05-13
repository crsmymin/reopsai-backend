"""Compatibility wrapper for the auth repository."""

from reopsai.infrastructure.persistence.repositories.auth_repository import (
    BUSINESS_ACCOUNT_TYPE,
    INDIVIDUAL_ACCOUNT_TYPE,
    AuthRepository,
)

__all__ = ["AuthRepository", "BUSINESS_ACCOUNT_TYPE", "INDIVIDUAL_ACCOUNT_TYPE"]
