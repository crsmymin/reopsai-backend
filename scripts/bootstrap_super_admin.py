#!/usr/bin/env python3
"""Bootstrap configured Google accounts as super admins.

This script is intended for first deployment and recovery operations. It is
idempotent: running it repeatedly keeps the configured accounts at tier=super.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: F401 - loads backend env files
from sqlalchemy import func, select

from reopsai.infrastructure.database import init_engine, session_scope
from reopsai.infrastructure.persistence.models.core import User


DEFAULT_SUPER_ADMIN_NAME = "Super Admin"
EMAIL_ENV_NAMES = ("SUPER_ADMIN_EMAILS", "INITIAL_SUPER_ADMIN_EMAIL")


def parse_super_admin_emails(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []

    seen = set()
    emails = []
    for item in raw_value.split(","):
        email = item.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        emails.append(email)
    return emails


def configured_super_admin_emails() -> list[str]:
    for env_name in EMAIL_ENV_NAMES:
        emails = parse_super_admin_emails(os.getenv(env_name))
        if emails:
            return emails
    return []


def ensure_super_admin(session, *, email: str, name: str = DEFAULT_SUPER_ADMIN_NAME):
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValueError("email is required")

    user = session.execute(
        select(User).where(func.lower(User.email) == normalized_email).limit(1)
    ).scalar_one_or_none()
    created = False

    if user is None:
        user = User(
            email=normalized_email,
            name=name,
            tier="super",
            account_type="individual",
            password_reset_required=False,
        )
        session.add(user)
        session.flush()
        created = True
    else:
        user.email = normalized_email
        user.tier = "super"
        user.account_type = "individual"
        user.password_reset_required = False
        if not user.name:
            user.name = name
        session.flush()

    session.refresh(user)
    return user, created


def bootstrap_super_admins(emails: list[str]) -> list[tuple[str, bool]]:
    results = []
    if not init_engine(validate_connection=True):
        raise RuntimeError("DATABASE_URL is required to bootstrap super admins.")

    with session_scope() as db_session:
        for email in emails:
            user, created = ensure_super_admin(db_session, email=email)
            results.append((user.email, created))
    return results


def main() -> int:
    emails = configured_super_admin_emails()
    if not emails:
        print("No super admin emails configured. Set SUPER_ADMIN_EMAILS or INITIAL_SUPER_ADMIN_EMAIL.")
        return 0

    results = bootstrap_super_admins(emails)
    for email, created in results:
        action = "created" if created else "updated"
        print(f"{action}: {email} -> tier=super")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
