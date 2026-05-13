"""Compatibility wrapper for the B2B repository."""

from reopsai.infrastructure.persistence.repositories.b2b_repository import (
    BUSINESS_ACCOUNT_TYPE,
    DEFAULT_BUSINESS_PASSWORD,
    B2bRepository,
)

__all__ = ["B2bRepository", "BUSINESS_ACCOUNT_TYPE", "DEFAULT_BUSINESS_PASSWORD"]
