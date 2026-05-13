"""Compatibility wrapper for the admin repository."""

from reopsai.infrastructure.persistence.repositories.admin_repository import (
    DEFAULT_ENTERPRISE_PASSWORD,
    DELETED_TEAM_STATUS,
    BUSINESS_ACCOUNT_TYPE,
    AdminRepository,
)

__all__ = [
    "AdminRepository",
    "DEFAULT_ENTERPRISE_PASSWORD",
    "DELETED_TEAM_STATUS",
    "BUSINESS_ACCOUNT_TYPE",
]
