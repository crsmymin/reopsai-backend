from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from db.repositories.b2b_repository import B2bRepository, BUSINESS_ACCOUNT_TYPE, DEFAULT_BUSINESS_PASSWORD


@dataclass(frozen=True)
class B2bResult:
    status: str
    data: Any = None


def serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def serialize_decimal(value):
    return float(value or 0)


def plan_payload_for_user(user):
    code = (getattr(user, "tier", None) or "enterprise").strip().lower()
    if code == "admin":
        code = "super"
    plan_names = {
        "free": "Free Plan",
        "basic": "Basic Plan",
        "premium": "Premium Plan",
        "enterprise": "Enterprise Plan",
        "super": "Super Admin",
    }
    return {"code": code, "name": plan_names.get(code, f"{code.title()} Plan")}


def member_payload(user, membership):
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "department": user.department,
        "tier": user.tier or "free",
        "account_type": user.account_type or "individual",
        "role": membership.role or "member",
        "joined_at": serialize_dt(membership.joined_at),
        "password_reset_required": bool(user.password_reset_required),
    }


class B2bService:
    def __init__(self, repository=None, session_factory=None):
        if repository is None:
            repository = B2bRepository
        if session_factory is None:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory

    def db_ready(self):
        return self.session_factory is not None

    def _company_id_for(self, db_session, *, user_id: int, company_id_claim: Optional[int] = None):
        return self.repository.get_my_company_id(db_session, user_id, company_id_claim)

    def get_membership_usage(
        self,
        *,
        user_id: int,
        company_id_claim: Optional[int],
        period: str,
        start_date,
        end_date,
    ) -> B2bResult:
        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            if not self.repository.require_owner(db_session, company_id, user_id):
                return B2bResult("forbidden")
            usage = self.repository.get_usage_payload(
                db_session,
                company_id=company_id,
                owner_user_id=user_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
            )
            if not usage:
                return B2bResult("company_not_found")

            company = usage["company"]
            owner = usage["owner"]
            totals = usage["totals"]
            payload = {
                "company": {"id": company.id, "name": company.name, "status": company.status},
                "plan": plan_payload_for_user(owner) if owner else {"code": "enterprise", "name": "Enterprise Plan"},
                "token_balance": {
                    "granted_weighted_tokens": int(usage["granted"] or 0),
                    "used_weighted_tokens": abs(int(usage["used"] or 0)),
                    "remaining_weighted_tokens": int(usage["balance"] or 0),
                },
                "period": period,
                "window": {
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                "totals": {
                    "request_count": int(totals.request_count or 0),
                    "billable_weighted_tokens": int(totals.billable_weighted_tokens or 0),
                },
                "by_period": [
                    {
                        "period": row.period,
                        "request_count": int(row.request_count or 0),
                        "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                    }
                    for row in usage["by_period_rows"]
                ],
                "by_user": [
                    {
                        "user_id": row.user_id,
                        "email": row.email,
                        "name": row.name,
                        "department": row.department,
                        "request_count": int(row.request_count or 0),
                        "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                        "last_used_at": serialize_dt(row.last_used_at),
                    }
                    for row in usage["by_user_rows"]
                ],
            }
            return B2bResult("ok", payload)

    def get_my_team(self, *, user_id: int, company_id_claim: Optional[int]) -> B2bResult:
        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            company = self.repository.get_active_company(db_session, company_id)
            if not company:
                return B2bResult("company_not_found")
            member_rows, users_by_id = self.repository.get_company_members(db_session, company_id)
            members = [
                member_payload(users_by_id[row.user_id], row)
                for row in member_rows
                if row.user_id in users_by_id
            ]
            return B2bResult(
                "ok",
                {
                    "company": {
                        "id": company.id,
                        "name": company.name,
                        "status": company.status,
                        "created_at": serialize_dt(company.created_at),
                    },
                    "members": members,
                },
            )

    def add_team_member(self, *, user_id: int, company_id_claim: Optional[int], email: str, role: str, department) -> B2bResult:
        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            if not self.repository.require_owner(db_session, company_id, user_id):
                return B2bResult("forbidden")
            member_result, status = self.repository.add_or_update_member(
                db_session,
                company_id=company_id,
                email=email,
                role=role,
                department=department,
            )
            if status == "not_found":
                return B2bResult("user_not_found")
            return B2bResult("ok")

    def update_team_member(self, *, user_id: int, company_id_claim: Optional[int], member_user_id: int, data: dict) -> B2bResult:
        if int(member_user_id) == int(user_id):
            return B2bResult("self_update")
        allowed_fields = {"name", "department"}
        unknown_fields = sorted(set(data.keys()) - allowed_fields)
        if unknown_fields:
            return B2bResult("unknown_fields", unknown_fields)
        if not any(field in data for field in allowed_fields):
            return B2bResult("empty_update")

        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            if not self.repository.require_owner(db_session, company_id, user_id):
                return B2bResult("forbidden")
            membership = self.repository.get_membership(db_session, company_id, member_user_id)
            if not membership:
                return B2bResult("not_same_company")
            if (membership.role or "member") == "owner":
                return B2bResult("target_owner")
            target_user = self.repository.get_user_by_id(db_session, member_user_id)
            if not target_user:
                return B2bResult("user_not_found")
            if (target_user.account_type or "") != BUSINESS_ACCOUNT_TYPE:
                return B2bResult("not_business")

            name = None
            if "name" in data:
                name = (data.get("name") or "").strip()
                if not name:
                    return B2bResult("empty_name")
            department_marker = None
            if "department" in data:
                department_marker = (data.get("department") or "").strip() or None
            target_user = self.repository.update_member_profile(
                db_session,
                member_user_id=member_user_id,
                name=name,
                department_value=department_marker,
                update_department="department" in data,
            )
            return B2bResult("ok", member_payload(target_user, membership))

    def reset_team_member_password(self, *, user_id: int, company_id_claim: Optional[int], member_user_id: int) -> B2bResult:
        if int(member_user_id) == int(user_id):
            return B2bResult("self_reset")
        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            if not self.repository.require_owner(db_session, company_id, user_id):
                return B2bResult("forbidden")
            membership = self.repository.get_membership(db_session, company_id, member_user_id)
            if not membership:
                return B2bResult("not_same_company")
            if (membership.role or "member") == "owner":
                return B2bResult("target_owner")
            target_user = self.repository.get_user_by_id(db_session, member_user_id)
            if not target_user:
                return B2bResult("user_not_found")
            if (target_user.tier or "").strip().lower() == "super":
                return B2bResult("super_account")
            if (target_user.account_type or "") != BUSINESS_ACCOUNT_TYPE:
                return B2bResult("not_business")
            target_user = self.repository.reset_member_password(db_session, member_user_id=member_user_id)
            return B2bResult("ok", member_payload(target_user, membership))

    def remove_team_member(self, *, user_id: int, company_id_claim: Optional[int], member_user_id: int) -> B2bResult:
        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            if not self.repository.require_owner(db_session, company_id, user_id):
                return B2bResult("forbidden")
            if int(member_user_id) == int(user_id):
                return B2bResult("self_remove")
            membership = self.repository.get_membership(db_session, company_id, member_user_id)
            if not membership:
                return B2bResult("ok")
            if (membership.role or "member") == "owner":
                return B2bResult("target_owner")
            self.repository.remove_membership(db_session, membership)
            return B2bResult("ok")

    def change_team_member_role(self, *, user_id: int, company_id_claim: Optional[int], member_user_id: int, new_role: str) -> B2bResult:
        if new_role != "owner":
            return B2bResult("unsupported_role")
        with self.session_factory() as db_session:
            company_id = self._company_id_for(db_session, user_id=user_id, company_id_claim=company_id_claim)
            if not company_id:
                return B2bResult("no_company")
            if not self.repository.require_owner(db_session, company_id, user_id):
                return B2bResult("forbidden")
            if int(member_user_id) == int(user_id):
                return B2bResult("self_role_change")
            membership = self.repository.promote_member_to_owner(
                db_session,
                company_id=company_id,
                member_user_id=member_user_id,
            )
            if not membership:
                return B2bResult("not_same_company")
            return B2bResult("ok")


b2b_service = B2bService()
