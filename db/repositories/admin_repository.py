from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from werkzeug.security import generate_password_hash

from db.models.core import (
    Company,
    CompanyMember,
    CompanyUsageEvent,
    LlmUsageDailyAggregate,
    LlmUsageEvent,
    Team,
    TeamMember,
    TeamUsageEvent,
    User,
)
from reopsai.shared.usage_metering import ensure_company_initial_grant, get_company_token_balance


DEFAULT_ENTERPRISE_PASSWORD = "0000"
DELETED_TEAM_STATUS = "deleted"
BUSINESS_ACCOUNT_TYPE = "business"


class AdminRepository:
    @staticmethod
    def get_user_by_id(session, user_id):
        return session.execute(select(User).where(User.id == int(user_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_user_by_email_lower(session, email):
        return session.execute(
            select(User).where(func.lower(User.email) == email.lower()).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_company(session, company_id):
        if not company_id:
            return None
        return session.execute(
            select(Company).where(Company.id == int(company_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_active_company(session, company_id):
        return session.execute(
            select(Company)
            .where(Company.id == int(company_id), Company.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_or_create_company(session, name):
        company_name = (name or "").strip()
        if not company_name:
            return None
        company = session.execute(
            select(Company).where(func.lower(Company.name) == company_name.lower()).limit(1)
        ).scalar_one_or_none()
        if company:
            return company
        company = Company(name=company_name, status="active")
        session.add(company)
        session.flush()
        session.refresh(company)
        ensure_company_initial_grant(session, company.id)
        return company

    @staticmethod
    def set_company_for_user(session, user, name):
        company_name = (name or "").strip()
        if not company_name:
            user.company_id = None
            return None

        existing = session.execute(
            select(Company).where(func.lower(Company.name) == company_name.lower()).limit(1)
        ).scalar_one_or_none()
        if existing and existing.id != user.company_id:
            company = existing
        elif user.company_id:
            company = AdminRepository.get_company(session, user.company_id)
            if company:
                company.name = company_name
            else:
                company = AdminRepository.get_or_create_company(session, company_name)
        else:
            company = AdminRepository.get_or_create_company(session, company_name)

        user.company_id = company.id if company else None
        return company

    @staticmethod
    def get_first_company_membership(session, user_id):
        return session.execute(
            select(CompanyMember)
            .where(CompanyMember.user_id == int(user_id))
            .order_by(CompanyMember.joined_at.asc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_company_membership(session, company_id, user_id):
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
    def ensure_company_membership(session, *, company_id, user_id, role):
        membership = AdminRepository.get_company_membership(session, company_id, user_id)
        if membership:
            membership.role = role
        else:
            membership = CompanyMember(company_id=int(company_id), user_id=int(user_id), role=role)
            session.add(membership)
        if role == "owner":
            session.execute(
                update(CompanyMember)
                .where(and_(CompanyMember.company_id == int(company_id), CompanyMember.user_id != int(user_id)))
                .values(role="member")
            )
        return membership

    @staticmethod
    def list_account_users(session, *, search="", account_type=""):
        query = (
            select(User)
            .where(func.lower(User.tier).notin_(["super", "admin"]))
            .order_by(User.created_at.desc())
        )
        if search:
            pattern = f"%{search.lower()}%"
            query = query.where(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(User.name).like(pattern),
                    func.lower(User.department).like(pattern),
                    User.company_id.in_(select(Company.id).where(func.lower(Company.name).like(pattern))),
                    User.id.in_(
                        select(CompanyMember.user_id)
                        .join(Company, Company.id == CompanyMember.company_id)
                        .where(func.lower(Company.name).like(pattern))
                    ),
                )
            )
        if account_type == "business":
            query = query.where(User.account_type == BUSINESS_ACCOUNT_TYPE)
        elif account_type == "individual":
            query = query.where(User.account_type != BUSINESS_ACCOUNT_TYPE)
        elif account_type == "google":
            query = query.where(User.account_type != BUSINESS_ACCOUNT_TYPE, User.google_id.is_not(None))
        return session.execute(query).scalars().all()

    @staticmethod
    def create_enterprise_account(session, *, email, name, company_name, department):
        if AdminRepository.get_user_by_email_lower(session, email):
            return None, "duplicate"
        company = AdminRepository.get_or_create_company(session, company_name)
        user = User(
            email=email,
            name=name,
            company_id=company.id if company else None,
            department=department,
            tier="enterprise",
            account_type=BUSINESS_ACCOUNT_TYPE,
            password_hash=generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD),
            password_reset_required=True,
        )
        session.add(user)
        session.flush()
        session.refresh(user)
        if company:
            session.add(CompanyMember(company_id=company.id, user_id=user.id, role="owner"))
        return (user, company), "ok"

    @staticmethod
    def reset_enterprise_password(session, user):
        user.password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
        user.password_reset_required = True

    @staticmethod
    def list_teams(session, *, search="", plan_code=None, enterprise_account_id=None, status="active"):
        query = select(Team).order_by(Team.created_at.desc())
        if status != "all":
            query = query.where(Team.status == status)
        if search:
            query = query.where(func.lower(Team.name).like(f"%{search.lower()}%"))
        if plan_code:
            query = query.where(Team.plan_code == plan_code)
        if enterprise_account_id is not None:
            query = query.where(Team.owner_id == int(enterprise_account_id))
        return session.execute(query).scalars().all()

    @staticmethod
    def get_team(session, team_id):
        return session.execute(select(Team).where(Team.id == int(team_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_active_team(session, team_id):
        return session.execute(
            select(Team)
            .where(Team.id == int(team_id), Team.status != DELETED_TEAM_STATUS)
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_inherited_team_plan(session, owner_id):
        return session.execute(
            select(Team.plan_code)
            .where(Team.owner_id == int(owner_id), Team.status != DELETED_TEAM_STATUS)
            .order_by(Team.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_team(session, *, owner_id, team_name, description, plan_code):
        team = Team(
            owner_id=int(owner_id),
            name=team_name,
            description=description or None,
            status="active",
            plan_code=plan_code,
        )
        session.add(team)
        session.flush()
        session.refresh(team)
        session.add(TeamMember(team_id=team.id, user_id=int(owner_id), role="owner"))
        session.flush()
        return team

    @staticmethod
    def count_team_members(session, team_id):
        return (
            session.execute(
                select(func.count()).select_from(TeamMember).where(TeamMember.team_id == int(team_id))
            ).scalar_one()
            or 0
        )

    @staticmethod
    def team_owner_member_exists(session, team_id, owner_id):
        if not owner_id:
            return False
        return (
            session.execute(
                select(TeamMember.id)
                .where(and_(TeamMember.team_id == int(team_id), TeamMember.user_id == int(owner_id)))
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    @staticmethod
    def count_team_usage_events(session, team_id):
        return (
            session.execute(
                select(func.count()).select_from(TeamUsageEvent).where(TeamUsageEvent.team_id == int(team_id))
            ).scalar_one()
            or 0
        )

    @staticmethod
    def soft_delete_team(session, team):
        was_deleted = team.status == DELETED_TEAM_STATUS
        if not was_deleted:
            team.status = DELETED_TEAM_STATUS
            team.updated_at = datetime.now()
            session.flush()
        return was_deleted

    @staticmethod
    def list_companies(session, *, search="", status="", offset=0, limit=20):
        query = select(Company).order_by(Company.created_at.desc())
        count_query = select(func.count()).select_from(Company)
        filters = []
        if search:
            filters.append(func.lower(Company.name).like(f"%{search.lower()}%"))
        if status:
            filters.append(Company.status == status)
        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))
        total_count = session.execute(count_query).scalar_one() or 0
        companies = session.execute(query.offset(offset).limit(limit)).scalars().all()
        return companies, int(total_count)

    @staticmethod
    def company_member_counts(session, company_ids):
        if not company_ids:
            return {}, {}
        member_rows = session.execute(
            select(CompanyMember.company_id, func.count(CompanyMember.id))
            .where(CompanyMember.company_id.in_(company_ids))
            .group_by(CompanyMember.company_id)
        ).all()
        owner_rows = session.execute(
            select(CompanyMember.company_id, func.count(CompanyMember.id))
            .where(CompanyMember.company_id.in_(company_ids), CompanyMember.role == "owner")
            .group_by(CompanyMember.company_id)
        ).all()
        return (
            {row[0]: int(row[1] or 0) for row in member_rows},
            {row[0]: int(row[1] or 0) for row in owner_rows},
        )

    @staticmethod
    def company_usage_summary(session, company_id):
        ensure_company_initial_grant(session, int(company_id))
        totals = session.execute(
            select(
                func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0),
                func.coalesce(func.sum(LlmUsageDailyAggregate.total_tokens), 0),
                func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0),
                func.coalesce(func.sum(LlmUsageDailyAggregate.estimated_cost_usd), 0),
            ).where(LlmUsageDailyAggregate.company_id == int(company_id))
        ).one()
        balance = get_company_token_balance(session, int(company_id))
        return {
            "request_count": int(totals[0] or 0),
            "total_tokens": int(totals[1] or 0),
            "billable_weighted_tokens": int(totals[2] or 0),
            "estimated_cost_usd": float(totals[3] or 0),
            "usage_limit": None,
            "remaining_weighted_tokens": balance,
        }

    @staticmethod
    def get_company_members(session, company_id):
        memberships = session.execute(
            select(CompanyMember)
            .where(CompanyMember.company_id == int(company_id))
            .order_by(CompanyMember.joined_at.asc())
        ).scalars().all()
        user_ids = [row.user_id for row in memberships if row.user_id is not None]
        users_by_id = {}
        if user_ids:
            users = session.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
            users_by_id = {user.id: user for user in users}
        return memberships, users_by_id

    @staticmethod
    def company_member_legacy_usage(session, company_id, user_ids):
        if not user_ids:
            return {}
        usage_rows = session.execute(
            select(
                CompanyUsageEvent.user_id,
                func.sum(CompanyUsageEvent.total_tokens).label("total_tokens"),
                func.max(CompanyUsageEvent.occurred_at).label("last_used_at"),
            )
            .where(
                CompanyUsageEvent.company_id == int(company_id),
                CompanyUsageEvent.user_id.in_(user_ids),
            )
            .group_by(CompanyUsageEvent.user_id)
        ).all()
        return {
            row.user_id: {"total_tokens": int(row.total_tokens or 0), "last_used_at": row.last_used_at}
            for row in usage_rows
        }

    @staticmethod
    def company_member_llm_usage(session, company_id, user_ids):
        if not user_ids:
            return {}
        usage_rows = session.execute(
            select(
                LlmUsageDailyAggregate.user_id,
                func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0).label("request_count"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0).label("billable_weighted_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.estimated_cost_usd), 0).label("estimated_cost_usd"),
            )
            .where(
                LlmUsageDailyAggregate.company_id == int(company_id),
                LlmUsageDailyAggregate.user_id.in_(user_ids),
            )
            .group_by(LlmUsageDailyAggregate.user_id)
        ).all()
        last_rows = session.execute(
            select(LlmUsageEvent.user_id, func.max(LlmUsageEvent.occurred_at).label("last_used_at"))
            .where(LlmUsageEvent.company_id == int(company_id), LlmUsageEvent.user_id.in_(user_ids))
            .group_by(LlmUsageEvent.user_id)
        ).all()
        last_by_user_id = {row.user_id: row.last_used_at for row in last_rows}
        return {
            row.user_id: {
                "request_count": int(row.request_count or 0),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "cached_input_tokens": int(row.cached_input_tokens or 0),
                "reasoning_tokens": int(row.reasoning_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": float(row.estimated_cost_usd or 0),
                "last_used_at": last_by_user_id.get(row.user_id),
            }
            for row in usage_rows
        }

    @staticmethod
    def is_company_owner(session, *, company_id, user_id):
        if user_id is None:
            return False
        return (
            session.execute(
                select(CompanyMember.id)
                .where(
                    CompanyMember.company_id == int(company_id),
                    CompanyMember.user_id == int(user_id),
                    CompanyMember.role == "owner",
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    @staticmethod
    def upsert_enterprise_user(session, *, company, email, name, department, role):
        password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
        user = AdminRepository.get_user_by_email_lower(session, email)
        if user:
            user.name = name or user.name
            user.company_id = company.id
            if department is not None:
                user.department = department
            user.tier = "enterprise"
            user.account_type = BUSINESS_ACCOUNT_TYPE
            user.password_hash = password_hash
            user.password_reset_required = True
        else:
            user = User(
                email=email,
                name=name,
                company_id=company.id,
                department=department,
                tier="enterprise",
                account_type=BUSINESS_ACCOUNT_TYPE,
                password_hash=password_hash,
                password_reset_required=True,
            )
            session.add(user)
            session.flush()

        AdminRepository.ensure_company_membership(
            session,
            company_id=company.id,
            user_id=user.id,
            role=role,
        )
        session.flush()
        session.refresh(user)
        return user
