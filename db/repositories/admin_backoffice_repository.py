"""Compatibility wrapper for the admin backoffice repository."""

from reopsai.infrastructure.persistence.repositories.admin_backoffice_repository import (
    DEFAULT_ENTERPRISE_PASSWORD,
    BUSINESS_ACCOUNT_TYPE,
    AdminBackofficeRepository,
)

__all__ = [
    "AdminBackofficeRepository",
    "DEFAULT_ENTERPRISE_PASSWORD",
    "BUSINESS_ACCOUNT_TYPE",
]
