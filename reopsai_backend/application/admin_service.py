from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from reopsai_backend.infrastructure.repositories import (
    AdminRepository,
    BUSINESS_ACCOUNT_TYPE,
    DEFAULT_ENTERPRISE_PASSWORD,
)


ALLOWED_PLAN_CODES = {"starter", "pro", "enterprise_plus"}
ALLOWED_USER_PLAN_CODES = {"free", "basic", "premium"}
USER_PLAN_CODE_ALIASES = {
    "starter": "free",
    "pro": "basic",
    "enterprise_plus": "premium",
}
DELETED_TEAM_STATUS = "deleted"


@dataclass(frozen=True)
class AdminResult:
    status: str
    data: Any = None


def serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def serialize_decimal(value):
    return float(value or 0)


def empty_llm_usage_payload():
    return {
        "request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "billable_weighted_tokens": 0,
        "estimated_cost_usd": 0.0,
        "last_used_at": None,
    }


def user_auth_type(user):
    account_type = user.account_type or "individual"
    if account_type == BUSINESS_ACCOUNT_TYPE:
        return BUSINESS_ACCOUNT_TYPE
    if user.google_id:
        return "google"
    return "individual"


def normalize_requester_tier(tier):
    value = (tier or "").strip().lower()
    return "super" if value == "admin" else value


class AdminService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY):
        if repository is None:
            repository = AdminRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory

    def db_ready(self):
        return self.session_factory is not None

    def _company_name_for(self, db_session, company_id, fallback=None):
        company = self.repository.get_company(db_session, company_id)
        return company.name if company else fallback

    def account_list_payload(self, db_session, user):
        member_row = self.repository.get_first_company_membership(db_session, user.id)
        account_type = user.account_type or "individual"
        company_id = user.company_id or (member_row.company_id if member_row else None)
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "company_id": company_id,
            "company_name": self._company_name_for(db_session, company_id),
            "department": user.department,
            "plan_code": None if account_type == BUSINESS_ACCOUNT_TYPE else (user.tier or "free"),
            "account_type": account_type,
            "auth_type": user_auth_type(user),
            "company_role": member_row.role if member_row else None,
            "is_owner": (member_row.role if member_row else None) == "owner",
            "created_at": serialize_dt(user.created_at),
        }

    def team_payload(self, db_session, team, owner=None):
        if owner is None and team.owner_id is not None:
            owner = self.repository.get_user_by_id(db_session, team.owner_id)
        member_count = self.repository.count_team_members(db_session, team.id)
        if team.owner_id and not self.repository.team_owner_member_exists(db_session, team.id, team.owner_id):
            member_count += 1
        return {
            "id": team.id,
            "team_name": team.name,
            "description": team.description,
            "status": team.status,
            "plan_code": team.plan_code or "starter",
            "owner_email": owner.email if owner else None,
            "enterprise_account_id": team.owner_id,
            "owner_id": team.owner_id,
            "member_count": int(member_count),
            "created_at": serialize_dt(team.created_at),
        }

    def list_enterprise_accounts(
        self,
        *,
        search="",
        plan_code=None,
        account_type="",
        company_role="",
        page=1,
        per_page=20,
    ) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            users = self.repository.list_account_users(
                db_session,
                search=search,
                account_type=account_type,
            )
            accounts = []
            for user in users:
                payload = self.account_list_payload(db_session, user)
                if plan_code and payload["plan_code"] != plan_code:
                    continue
                if company_role and payload["company_role"] != company_role:
                    continue
                accounts.append(payload)
            total_count = len(accounts)
            start = (page - 1) * per_page
            end = start + per_page
            return AdminResult(
                "ok",
                {
                    "accounts": accounts[start:end],
                    "total_count": total_count,
                    "total_pages": math.ceil(total_count / per_page) if total_count else 0,
                    "current_page": page,
                },
            )

    def create_enterprise_account(self, *, email, name, company_name, department) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            created, status = self.repository.create_enterprise_account(
                db_session,
                email=email,
                name=name,
                company_name=company_name,
                department=department,
            )
            if status == "duplicate":
                return AdminResult("duplicate")
            user, company = created
            return AdminResult(
                "ok",
                {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "company_id": user.company_id,
                    "company_name": company.name if company else None,
                    "department": user.department,
                    "account_type": user.account_type,
                    "tier": user.tier,
                    "password_reset_required": bool(user.password_reset_required),
                },
            )

    def update_enterprise_account(self, *, account_id, data) -> AdminResult:
        has_name = "name" in data
        has_company_name = "company_name" in data
        has_department = "department" in data
        has_plan_code = "plan_code" in data
        if not has_name and not has_company_name and not has_department and not has_plan_code:
            return AdminResult("empty_update")
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, account_id)
            if not user:
                return AdminResult("not_found")

            plan_code = None
            if has_plan_code:
                if user_auth_type(user) == BUSINESS_ACCOUNT_TYPE:
                    return AdminResult("business_plan_forbidden")
                if (user.tier or "").strip().lower() not in ALLOWED_USER_PLAN_CODES:
                    return AdminResult("non_user_plan_forbidden")
                value = (data.get("plan_code") or "").strip().lower()
                value = USER_PLAN_CODE_ALIASES.get(value, value)
                if value not in ALLOWED_USER_PLAN_CODES:
                    return AdminResult("invalid_user_plan")
                plan_code = value

            if has_name:
                user.name = (data.get("name") or "").strip() or None
            if has_company_name:
                company = self.repository.set_company_for_user(db_session, user, data.get("company_name"))
                if company and (user.account_type or "") == BUSINESS_ACCOUNT_TYPE:
                    membership = self.repository.get_first_company_membership(db_session, user.id)
                    if membership:
                        membership.company_id = company.id
                    else:
                        self.repository.ensure_company_membership(
                            db_session,
                            company_id=company.id,
                            user_id=user.id,
                            role="owner",
                        )
            if has_department:
                user.department = (data.get("department") or "").strip() or None
            if plan_code:
                user.tier = plan_code
            db_session.flush()
            return AdminResult("ok", self.account_list_payload(db_session, user))

    def reset_enterprise_account_password(self, *, account_id) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, account_id)
            if not user:
                return AdminResult("not_found")
            self.repository.reset_enterprise_password(db_session, user)
            return AdminResult("ok")

    def list_admin_teams(
        self,
        *,
        search="",
        plan_code=None,
        enterprise_account_id=None,
        status="active",
        page=1,
        per_page=20,
    ) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            teams = self.repository.list_teams(
                db_session,
                search=search,
                plan_code=plan_code,
                enterprise_account_id=enterprise_account_id,
                status=status,
            )
            total_count = len(teams)
            start = (page - 1) * per_page
            end = start + per_page
            return AdminResult(
                "ok",
                {
                    "teams": [self.team_payload(db_session, team) for team in teams[start:end]],
                    "total_count": total_count,
                    "total_pages": math.ceil(total_count / per_page) if total_count else 0,
                    "current_page": page,
                },
            )

    def create_admin_team(self, *, enterprise_account_id, team_name, description, requested_plan) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            owner = self.repository.get_user_by_id(db_session, enterprise_account_id)
            if not owner:
                return AdminResult("owner_not_found")
            if owner.account_type == BUSINESS_ACCOUNT_TYPE:
                return AdminResult("business_owner_forbidden")
            inherited_plan = self.repository.get_inherited_team_plan(db_session, enterprise_account_id)
            plan_code = requested_plan or inherited_plan or "starter"
            team = self.repository.create_team(
                db_session,
                owner_id=enterprise_account_id,
                team_name=team_name,
                description=description,
                plan_code=plan_code,
            )
            return AdminResult("ok", self.team_payload(db_session, team, owner))

    def soft_delete_admin_team(self, *, team_id) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            team = self.repository.get_team(db_session, team_id)
            if not team:
                return AdminResult("not_found")
            member_count = self.repository.count_team_members(db_session, team_id)
            usage_event_count = self.repository.count_team_usage_events(db_session, team_id)
            was_deleted = self.repository.soft_delete_team(db_session, team)
            return AdminResult(
                "ok",
                {
                    "was_deleted": was_deleted,
                    "team": self.team_payload(db_session, team),
                    "affected": {
                        "members_preserved": int(member_count),
                        "usage_events_preserved": int(usage_event_count),
                    },
                },
            )

    def update_team_plan_code(self, *, team_id, plan_code) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            team = self.repository.get_active_team(db_session, team_id)
            if not team:
                return AdminResult("not_found")
            team.plan_code = plan_code
            return AdminResult("ok")

    def create_enterprise_user(
        self,
        *,
        email,
        name,
        company_id,
        department,
        role,
        requester_id,
        requester_tier,
    ) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            company = self.repository.get_active_company(db_session, company_id)
            if not company:
                return AdminResult("company_not_found")
            is_super = normalize_requester_tier(requester_tier) == "super"
            is_company_owner = self.repository.is_company_owner(
                db_session,
                company_id=company_id,
                user_id=requester_id,
            )
            if not (is_super or is_company_owner):
                return AdminResult("forbidden")
            user = self.repository.upsert_enterprise_user(
                db_session,
                company=company,
                email=email,
                name=name,
                department=department,
                role=role,
            )
            return AdminResult(
                "ok",
                {
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.name,
                        "company_id": user.company_id,
                        "company_name": self._company_name_for(db_session, user.company_id),
                        "department": user.department,
                        "tier": user.tier,
                        "account_type": user.account_type,
                        "password_reset_required": bool(user.password_reset_required),
                        "created_at": serialize_dt(user.created_at),
                    },
                    "company": {"id": company.id, "name": company.name, "role": role},
                    "temporary_password": DEFAULT_ENTERPRISE_PASSWORD,
                },
            )

    def list_admin_companies(self, *, page=1, per_page=20, search="", status="") -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            companies, total_count = self.repository.list_companies(
                db_session,
                search=search,
                status=status,
                offset=(page - 1) * per_page,
                limit=per_page,
            )
            company_ids = [company.id for company in companies]
            member_counts, owner_counts = self.repository.company_member_counts(db_session, company_ids)
            return AdminResult(
                "ok",
                {
                    "companies": [
                        {
                            "id": company.id,
                            "name": company.name,
                            "status": company.status,
                            "member_count": member_counts.get(company.id, 0),
                            "owner_count": owner_counts.get(company.id, 0),
                            "usage": self.repository.company_usage_summary(db_session, company.id),
                            "created_at": serialize_dt(company.created_at),
                            "updated_at": serialize_dt(company.updated_at),
                        }
                        for company in companies
                    ],
                    "total_count": int(total_count),
                    "page": page,
                    "per_page": per_page,
                    "total_pages": (int(total_count) + per_page - 1) // per_page,
                },
            )

    def get_admin_company_detail(self, *, company_id) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            company = self.repository.get_company(db_session, company_id)
            if not company:
                return AdminResult("not_found")
            memberships, users_by_id = self.repository.get_company_members(db_session, company_id)
            user_ids = list(users_by_id.keys())
            usage_by_user_id = self.repository.company_member_legacy_usage(db_session, company_id, user_ids)
            llm_usage_by_user_id = self.repository.company_member_llm_usage(db_session, company_id, user_ids)
            members = []
            for membership in memberships:
                user = users_by_id.get(membership.user_id)
                if not user:
                    continue
                payload = self.account_list_payload(db_session, user)
                payload["role"] = membership.role or "member"
                payload["company_role"] = membership.role or "member"
                payload["joined_at"] = serialize_dt(membership.joined_at)
                legacy_usage = usage_by_user_id.get(user.id)
                payload["usage"] = {
                    "total_tokens": legacy_usage["total_tokens"] if legacy_usage else None,
                    "last_used_at": serialize_dt(legacy_usage["last_used_at"]) if legacy_usage else None,
                }
                llm_usage = llm_usage_by_user_id.get(user.id, empty_llm_usage_payload())
                payload["llm_usage"] = {
                    **llm_usage,
                    "last_used_at": serialize_dt(llm_usage.get("last_used_at")),
                }
                members.append(payload)
            usage = self.repository.company_usage_summary(db_session, company.id)
            return AdminResult(
                "ok",
                {
                    "company": {
                        "id": company.id,
                        "name": company.name,
                        "status": company.status,
                        "member_count": len(members),
                        "usage": usage,
                        "created_at": serialize_dt(company.created_at),
                        "updated_at": serialize_dt(company.updated_at),
                    },
                    "members": members,
                },
            )

    def update_admin_company(self, *, company_id, status) -> AdminResult:
        if not self.db_ready():
            return AdminResult("db_unavailable")
        with self.session_factory() as db_session:
            company = self.repository.get_company(db_session, company_id)
            if not company:
                return AdminResult("not_found")
            company.status = status
            db_session.flush()
            return AdminResult(
                "ok",
                {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "usage": self.repository.company_usage_summary(db_session, company.id),
                    "created_at": serialize_dt(company.created_at),
                    "updated_at": serialize_dt(company.updated_at),
                },
            )


admin_service = AdminService()
