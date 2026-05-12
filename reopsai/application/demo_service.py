from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api_logger import log_error
from reopsai.infrastructure.repositories import DemoRepository
from reopsai.application.auth_service import build_auth_context, build_user_payload
from reopsai.shared.usage_metering import ensure_company_initial_grant


@dataclass(frozen=True)
class DemoResult:
    status: str
    data: Any = None
    error: str | None = None


class DemoService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY, initial_grant_ensurer=None):
        if repository is None:
            repository = DemoRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai.infrastructure.database import session_scope

            session_factory = session_scope

        self.repository = repository
        self.session_factory = session_factory
        self.initial_grant_ensurer = initial_grant_ensurer or ensure_company_initial_grant

    def db_ready(self):
        return self.session_factory is not None

    def login(self, *, tier_type) -> DemoResult:
        if not self.db_ready():
            return DemoResult("db_unavailable")

        with self.session_factory() as db_session:
            if tier_type == "individual":
                user = self.get_or_create_individual_demo_account(db_session)
                if not user:
                    return DemoResult("account_failed", error="Failed to get or create individual demo account")
            else:
                user = self.get_or_create_enterprise_demo_account(db_session)
                if not user:
                    return DemoResult("account_failed", error="Failed to get or create enterprise demo account")

            claims = build_auth_context(user)
            user_payload = build_user_payload(user)
            return DemoResult(
                "ok",
                {
                    "user_id": user.id,
                    "claims": claims,
                    "user": user_payload,
                },
            )

    def get_or_create_individual_demo_account(self, db_session):
        try:
            demo_email = self.repository.INDIVIDUAL_DEMO_EMAIL
            user = self.repository.get_user_by_email_lower(db_session, demo_email)
            if user:
                return user
            return self.repository.create_demo_user(
                db_session,
                email=demo_email,
                tier="free",
                account_type="individual",
            )
        except Exception as exc:
            log_error(exc, "Individual 데모 계정 조회/생성 실패")
            return None

    def _ensure_business_company(self, db_session, user):
        company = self.repository.get_company_by_name_lower(db_session, "demo business")
        if not company:
            company = self.repository.create_company(db_session, name="Demo Business", status="active")

        self.initial_grant_ensurer(db_session, company.id)
        user.company_id = company.id

        membership = self.repository.get_company_member(
            db_session,
            company_id=company.id,
            user_id=user.id,
        )
        if membership:
            membership.role = "owner"
        else:
            self.repository.create_company_member(
                db_session,
                company_id=company.id,
                user_id=user.id,
                role="owner",
            )
        db_session.flush()
        return company.id

    def get_or_create_enterprise_demo_account(self, db_session):
        try:
            demo_email = self.repository.ENTERPRISE_DEMO_EMAIL
            user = self.repository.get_user_by_email_lower(db_session, demo_email)
            if user:
                if user.tier != "enterprise":
                    user.tier = "enterprise"
                user.account_type = "business"
                self._ensure_business_company(db_session, user)
                return user

            user = self.repository.create_demo_user(
                db_session,
                email=demo_email,
                tier="enterprise",
                account_type="business",
            )

            try:
                self._ensure_business_company(db_session, user)
            except Exception as exc:
                log_error(exc, "Enterprise 데모 계정 팀 생성 실패")
            return user
        except Exception as exc:
            log_error(exc, "Enterprise 데모 계정 조회/생성 실패")
            return None


demo_service = DemoService()
