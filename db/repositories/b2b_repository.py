from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, select, update
from werkzeug.security import generate_password_hash

from db.models.core import (
    Company,
    CompanyMember,
    CompanyTokenLedger,
    LlmUsageDailyAggregate,
    LlmUsageEvent,
    User,
)
from reopsai.shared.usage_metering import ensure_company_initial_grant, get_company_token_balance


BUSINESS_ACCOUNT_TYPE = "business"
DEFAULT_BUSINESS_PASSWORD = "0000"


def _usage_period_column(period: str):
    if period == "monthly":
        return func.to_char(func.date_trunc("month", LlmUsageDailyAggregate.usage_date), "YYYY-MM")
    return func.to_char(LlmUsageDailyAggregate.usage_date, "YYYY-MM-DD")


class B2bRepository:
    @staticmethod
    def get_company_name(session, company_id):
        if not company_id:
            return None
        return session.execute(
            select(Company.name).where(Company.id == int(company_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_user_by_id(session, user_id):
        return session.execute(select(User).where(User.id == int(user_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_my_company_id(session, user_id_int, company_id_claim: Optional[int] = None):
        if company_id_claim:
            return int(company_id_claim)
        user = B2bRepository.get_user_by_id(session, user_id_int)
        return int(user.company_id) if user and user.company_id else None

    @staticmethod
    def get_membership(session, company_id, user_id):
        return session.execute(
            select(CompanyMember)
            .where(
                and_(
                    CompanyMember.company_id == int(company_id),
                    CompanyMember.user_id == int(user_id),
                )
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def require_owner(session, company_id, user_id):
        membership = B2bRepository.get_membership(session, company_id, user_id)
        return membership if membership and (membership.role or "member") == "owner" else None

    @staticmethod
    def get_active_company(session, company_id):
        return session.execute(
            select(Company)
            .where(Company.id == int(company_id), Company.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_company_members(session, company_id):
        member_rows = session.execute(
            select(CompanyMember)
            .where(CompanyMember.company_id == int(company_id))
            .order_by(CompanyMember.joined_at.asc())
        ).scalars().all()
        member_user_ids = [row.user_id for row in member_rows if row.user_id is not None]
        users_by_id = {}
        if member_user_ids:
            users = session.execute(select(User).where(User.id.in_(member_user_ids))).scalars().all()
            users_by_id = {user.id: user for user in users}
        return member_rows, users_by_id

    @staticmethod
    def get_usage_payload(session, *, company_id, owner_user_id, period, start_date, end_date):
        company = B2bRepository.get_active_company(session, company_id)
        if not company:
            return None

        owner = B2bRepository.get_user_by_id(session, owner_user_id)
        ensure_company_initial_grant(session, int(company_id), created_by=int(owner_user_id))

        granted = session.execute(
            select(func.coalesce(func.sum(CompanyTokenLedger.delta_weighted_tokens), 0)).where(
                CompanyTokenLedger.company_id == int(company_id),
                CompanyTokenLedger.delta_weighted_tokens > 0,
            )
        ).scalar_one()
        used = session.execute(
            select(func.coalesce(func.sum(CompanyTokenLedger.delta_weighted_tokens), 0)).where(
                CompanyTokenLedger.company_id == int(company_id),
                CompanyTokenLedger.delta_weighted_tokens < 0,
            )
        ).scalar_one()
        balance = get_company_token_balance(session, int(company_id))

        filters = [LlmUsageDailyAggregate.company_id == int(company_id)]
        if start_date:
            filters.append(LlmUsageDailyAggregate.usage_date >= start_date)
        if end_date:
            filters.append(LlmUsageDailyAggregate.usage_date <= end_date)

        totals = session.execute(
            select(
                func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0).label("request_count"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0).label("billable_weighted_tokens"),
            ).where(*filters)
        ).one()

        period_col = _usage_period_column(period).label("period")
        by_period_rows = session.execute(
            select(
                period_col,
                func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
            )
            .where(*filters)
            .group_by(period_col)
            .order_by(period_col.asc())
        ).all()

        event_filters = [LlmUsageEvent.company_id == int(company_id)]
        if start_date:
            event_filters.append(LlmUsageEvent.occurred_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            event_filters.append(LlmUsageEvent.occurred_at <= datetime.combine(end_date, datetime.max.time()))

        by_user_aggregate = (
            select(
                LlmUsageDailyAggregate.user_id.label("user_id"),
                func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
            )
            .where(*filters)
            .group_by(LlmUsageDailyAggregate.user_id)
            .subquery()
        )
        last_user_event = (
            select(
                LlmUsageEvent.user_id.label("user_id"),
                func.max(LlmUsageEvent.occurred_at).label("last_used_at"),
            )
            .where(*event_filters)
            .group_by(LlmUsageEvent.user_id)
            .subquery()
        )
        by_user_rows = session.execute(
            select(
                by_user_aggregate.c.user_id,
                User.email,
                User.name,
                User.department,
                by_user_aggregate.c.request_count,
                by_user_aggregate.c.billable_weighted_tokens,
                last_user_event.c.last_used_at,
            )
            .select_from(by_user_aggregate)
            .outerjoin(User, User.id == by_user_aggregate.c.user_id)
            .outerjoin(last_user_event, last_user_event.c.user_id == by_user_aggregate.c.user_id)
            .order_by(by_user_aggregate.c.billable_weighted_tokens.desc(), by_user_aggregate.c.user_id.asc())
        ).all()

        return {
            "company": company,
            "owner": owner,
            "granted": granted,
            "used": used,
            "balance": balance,
            "totals": totals,
            "by_period_rows": by_period_rows,
            "by_user_rows": by_user_rows,
        }

    @staticmethod
    def get_user_by_email(session, email: str):
        return session.execute(
            select(User).where(func.lower(User.email) == email.lower()).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def add_or_update_member(session, *, company_id, email, role, department):
        target_user = B2bRepository.get_user_by_email(session, email)
        if not target_user:
            return None, "not_found"

        target_user.tier = "enterprise"
        target_user.account_type = BUSINESS_ACCOUNT_TYPE
        target_user.company_id = int(company_id)
        if department is not None:
            target_user.department = department

        membership = B2bRepository.get_membership(session, company_id, target_user.id)
        if membership:
            membership.role = role
        else:
            membership = CompanyMember(company_id=int(company_id), user_id=target_user.id, role=role)
            session.add(membership)
        if role == "owner":
            session.execute(
                update(CompanyMember)
                .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id != target_user.id))
                .values(role="member")
            )
        session.flush()
        return (target_user, membership), "ok"

    @staticmethod
    def update_member_profile(
        session, *, member_user_id, name=None, department_value=None, update_department=False
    ):
        target_user = B2bRepository.get_user_by_id(session, member_user_id)
        if not target_user:
            return None
        if name is not None:
            target_user.name = name
        if update_department:
            target_user.department = department_value
        session.flush()
        return target_user

    @staticmethod
    def reset_member_password(session, *, member_user_id):
        target_user = B2bRepository.get_user_by_id(session, member_user_id)
        if not target_user:
            return None
        target_user.password_hash = generate_password_hash(DEFAULT_BUSINESS_PASSWORD)
        target_user.password_reset_required = True
        session.flush()
        return target_user

    @staticmethod
    def remove_membership(session, membership):
        session.delete(membership)

    @staticmethod
    def promote_member_to_owner(session, *, company_id, member_user_id):
        membership = B2bRepository.get_membership(session, company_id, member_user_id)
        if not membership:
            return None
        membership.role = "owner"
        session.execute(
            update(CompanyMember)
            .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id != int(member_user_id)))
            .values(role="member")
        )
        session.execute(
            update(User)
            .where(User.id == int(member_user_id))
            .values(tier="enterprise", account_type=BUSINESS_ACCOUNT_TYPE, company_id=int(company_id))
        )
        return membership
