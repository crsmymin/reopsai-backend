from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from werkzeug.security import check_password_hash

from reopsai_backend.infrastructure.repositories import (
    AuthRepository,
    BUSINESS_ACCOUNT_TYPE,
    INDIVIDUAL_ACCOUNT_TYPE,
)
from reopsai_backend.shared.auth import normalize_tier


BUSINESS_PROFILE_UPDATE_FIELDS = {"name", "department"}


@dataclass(frozen=True)
class AuthResult:
    status: str
    data: Any = None


def serialize_dt(value):
    return value.isoformat() if hasattr(value, "isoformat") and value is not None else value


def build_auth_context(user):
    tier = normalize_tier(user.tier or "free")
    account_type = user.account_type or (
        BUSINESS_ACCOUNT_TYPE if tier == "enterprise" else INDIVIDUAL_ACCOUNT_TYPE
    )
    claims = {
        "tier": tier,
        "account_type": account_type,
        "password_reset_required": bool(user.password_reset_required),
    }
    if user.company_id and account_type == BUSINESS_ACCOUNT_TYPE:
        claims["company_id"] = int(user.company_id)
    if getattr(user, "department", None):
        claims["department"] = user.department
    return claims


def build_user_payload(user, name_override=None, company_id=None, company_name=None):
    return {
        "id": user.id,
        "email": user.email,
        "name": name_override if name_override is not None else user.name,
        "company_id": company_id if company_id is not None else user.company_id,
        "company_name": company_name,
        "department": user.department,
        "google_id": user.google_id,
        "tier": normalize_tier(user.tier or "free"),
        "account_type": user.account_type or INDIVIDUAL_ACCOUNT_TYPE,
        "password_reset_required": bool(user.password_reset_required),
        "created_at": serialize_dt(user.created_at),
    }


def compact_user_payload(user):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "created_at": serialize_dt(user.created_at),
    }


class AuthService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY):
        if repository is None:
            repository = AuthRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory

    def db_ready(self):
        return self.session_factory is not None

    def _company_name_for_claims(self, db_session, claims):
        return self.repository.get_company_name(db_session, claims.get("company_id"))

    def auth_payload_for_user(self, db_session, user, *, name_override=None):
        claims = build_auth_context(user)
        company_name = self._company_name_for_claims(db_session, claims)
        return {
            "user": build_user_payload(
                user,
                name_override=name_override,
                company_id=claims.get("company_id"),
                company_name=company_name,
            ),
            "claims": claims,
        }

    def legacy_password_login(self, *, email: str, password: str, enabled: bool, shared_secret: str) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        if not enabled:
            return AuthResult("disabled")
        if not shared_secret or password != shared_secret:
            return AuthResult("invalid_credentials")

        with self.session_factory() as db_session:
            user = self.repository.get_user_by_email(db_session, email)
            if not user:
                return AuthResult("not_found")
            return AuthResult("ok", self.auth_payload_for_user(db_session, user))

    def get_profile(self, *, user_id, jwt_claims: dict) -> AuthResult:
        user_payload = {
            "id": int(user_id) if str(user_id).isdigit() else user_id,
            "tier": normalize_tier(jwt_claims.get("tier")),
            "account_type": jwt_claims.get("account_type", INDIVIDUAL_ACCOUNT_TYPE),
            "company_id": jwt_claims.get("company_id"),
            "department": jwt_claims.get("department"),
            "password_reset_required": bool(jwt_claims.get("password_reset_required")),
        }

        if not self.db_ready():
            return AuthResult("ok", {"user": user_payload})

        try:
            with self.session_factory() as db_session:
                user = self.repository.get_user_by_id(db_session, user_id)
                if not user:
                    return AuthResult("ok", {"user": user_payload})
                auth_payload = self.auth_payload_for_user(db_session, user)
                auth_payload["user"]["tier"] = normalize_tier(auth_payload["claims"].get("tier"))
                return AuthResult("ok", {"user": auth_payload["user"]})
        except Exception:
            return AuthResult("ok", {"user": user_payload})

    def test_connection(self) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            count, sample = self.repository.get_database_test_payload(db_session)
            return AuthResult(
                "ok",
                {
                    "data_count": count,
                    "sample_data": [build_user_payload(sample)] if sample else [],
                },
            )

    def check_user(self, *, email: str) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_email(db_session, email)
            if not user:
                return AuthResult("not_found")
            return AuthResult("ok", compact_user_payload(user))

    def register_user(self, *, email: str, name: str, google_id=None) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            if self.repository.get_user_by_email(db_session, email):
                return AuthResult("duplicate")
            user = self.repository.create_user(db_session, email=email, name=name, google_id=google_id)
            return AuthResult("ok", compact_user_payload(user))

    def login_user(self, *, email: str, google_id=None) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_for_google_login(db_session, email=email, google_id=google_id)
            if not user:
                return AuthResult("not_found")
            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) == BUSINESS_ACCOUNT_TYPE:
                return AuthResult("business_forbidden")
            return AuthResult("ok", compact_user_payload(user))

    def list_users(self) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            users = self.repository.list_users(db_session)
            payload = [build_user_payload(user) for user in users]
            return AuthResult("ok", {"users": payload, "count": len(payload)})

    def upsert_google_user(self, *, email: str, name: str, google_id: str) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_email(db_session, email)
            is_new_user = False
            if user:
                if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) == BUSINESS_ACCOUNT_TYPE:
                    return AuthResult("business_forbidden")
                if not user.google_id:
                    user = self.repository.set_google_id(db_session, user_id=user.id, google_id=google_id)
            else:
                user = self.repository.create_user(db_session, email=email, name=name, google_id=google_id)
                is_new_user = True
            auth_payload = self.auth_payload_for_user(db_session, user, name_override=name)
            auth_payload["is_new_user"] = is_new_user
            return AuthResult("ok", auth_payload)

    def enterprise_login(self, *, email: str, password: str) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_email_lower(db_session, email)
            if not user:
                return AuthResult("not_found")
            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) != BUSINESS_ACCOUNT_TYPE:
                return AuthResult("individual_forbidden")
            if not user.password_hash or not check_password_hash(user.password_hash, password):
                return AuthResult("invalid_password")
            return AuthResult("ok", self.auth_payload_for_user(db_session, user))

    def change_business_password(self, *, user_id, current_password: str, new_password: str) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, user_id)
            if not user:
                return AuthResult("not_found")
            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) != BUSINESS_ACCOUNT_TYPE:
                return AuthResult("business_only")
            if not user.password_hash or not check_password_hash(user.password_hash, current_password):
                return AuthResult("invalid_current_password")
            user = self.repository.update_business_password(db_session, user_id=user_id, new_password=new_password)
            return AuthResult("ok", self.auth_payload_for_user(db_session, user))

    def update_business_profile(self, *, user_id, data: dict) -> AuthResult:
        unknown_fields = sorted(set(data.keys()) - BUSINESS_PROFILE_UPDATE_FIELDS)
        if unknown_fields:
            return AuthResult("unknown_fields", unknown_fields)
        if not any(field in data for field in BUSINESS_PROFILE_UPDATE_FIELDS):
            return AuthResult("empty_update")

        name = None
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return AuthResult("empty_name")
        department = None
        update_department = "department" in data
        if update_department:
            department = (data.get("department") or "").strip() or None

        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, user_id)
            if not user:
                return AuthResult("not_found")
            if (user.account_type or INDIVIDUAL_ACCOUNT_TYPE) != BUSINESS_ACCOUNT_TYPE:
                return AuthResult("business_only")
            user = self.repository.update_business_profile(
                db_session,
                user_id=user_id,
                name=name,
                department=department,
                update_department=update_department,
            )
            return AuthResult("ok", self.auth_payload_for_user(db_session, user))

    def dev_login(self, *, email: str, name: str) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            user, is_new_user = self.repository.get_or_create_dev_user(db_session, email=email, name=name)
            payload = compact_user_payload(user)
            payload["name"] = user.name or "테스트 사용자"
            return AuthResult("ok", {"user": payload, "is_new_user": is_new_user})

    def delete_account(self, *, user_id) -> AuthResult:
        if not self.db_ready():
            return AuthResult("db_unavailable")
        with self.session_factory() as db_session:
            return AuthResult("ok", self.repository.delete_account_payload(db_session, user_id=user_id))

    def get_primary_team_id_for_user(self, db_session, user_id):
        return self.repository.get_primary_team_id_for_user(db_session, user_id)


auth_service = AuthService()
