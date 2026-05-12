from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from db.models.core import (
    Company,
    CompanyTokenLedger,
    CompanyUsageEvent,
    LlmModelPrice,
    LlmUsageDailyAggregate,
    LlmUsageEvent,
    Team,
    TeamUsageEvent,
    User,
)
from utils.usage_metering import (
    cleanup_old_llm_usage_events,
    ensure_company_initial_grant,
    get_company_token_balance,
)


class AdminUsageRepository:
    @staticmethod
    def get_user(session, user_id):
        return session.execute(select(User).where(User.id == int(user_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_company(session, company_id):
        return session.execute(select(Company).where(Company.id == int(company_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_team(session, team_id):
        return session.execute(select(Team).where(Team.id == int(team_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_legacy_team_usage(session, *, team_id, start_at=None, end_at=None):
        team = AdminUsageRepository.get_team(session, team_id)
        if not team:
            return None

        base = select(
            func.sum(TeamUsageEvent.request_count),
            func.sum(TeamUsageEvent.prompt_tokens),
            func.sum(TeamUsageEvent.completion_tokens),
            func.sum(TeamUsageEvent.total_tokens),
        ).where(TeamUsageEvent.team_id == int(team_id))
        by_feature_q = select(
            TeamUsageEvent.feature_key,
            func.sum(TeamUsageEvent.request_count).label("request_count"),
            func.sum(TeamUsageEvent.total_tokens).label("total_tokens"),
        ).where(TeamUsageEvent.team_id == int(team_id))
        by_user_q = select(
            TeamUsageEvent.user_id,
            func.sum(TeamUsageEvent.request_count).label("request_count"),
            func.sum(TeamUsageEvent.total_tokens).label("total_tokens"),
        ).where(TeamUsageEvent.team_id == int(team_id))

        if start_at:
            base = base.where(TeamUsageEvent.occurred_at >= start_at)
            by_feature_q = by_feature_q.where(TeamUsageEvent.occurred_at >= start_at)
            by_user_q = by_user_q.where(TeamUsageEvent.occurred_at >= start_at)
        if end_at:
            base = base.where(TeamUsageEvent.occurred_at <= end_at)
            by_feature_q = by_feature_q.where(TeamUsageEvent.occurred_at <= end_at)
            by_user_q = by_user_q.where(TeamUsageEvent.occurred_at <= end_at)

        return {
            "entity": team,
            "totals": session.execute(base).one(),
            "by_feature_rows": session.execute(
                by_feature_q.group_by(TeamUsageEvent.feature_key).order_by(TeamUsageEvent.feature_key.asc())
            ).all(),
            "by_user_rows": session.execute(
                by_user_q.group_by(TeamUsageEvent.user_id).order_by(TeamUsageEvent.user_id.asc())
            ).all(),
        }

    @staticmethod
    def get_legacy_company_usage(session, *, company_id, start_at=None, end_at=None):
        company = AdminUsageRepository.get_company(session, company_id)
        if not company:
            return None

        base = select(
            func.sum(CompanyUsageEvent.request_count),
            func.sum(CompanyUsageEvent.prompt_tokens),
            func.sum(CompanyUsageEvent.completion_tokens),
            func.sum(CompanyUsageEvent.total_tokens),
        ).where(CompanyUsageEvent.company_id == int(company_id))
        by_feature_q = select(
            CompanyUsageEvent.feature_key,
            func.sum(CompanyUsageEvent.request_count).label("request_count"),
            func.sum(CompanyUsageEvent.total_tokens).label("total_tokens"),
        ).where(CompanyUsageEvent.company_id == int(company_id))
        by_user_q = select(
            CompanyUsageEvent.user_id,
            func.sum(CompanyUsageEvent.request_count).label("request_count"),
            func.sum(CompanyUsageEvent.total_tokens).label("total_tokens"),
        ).where(CompanyUsageEvent.company_id == int(company_id))

        if start_at:
            base = base.where(CompanyUsageEvent.occurred_at >= start_at)
            by_feature_q = by_feature_q.where(CompanyUsageEvent.occurred_at >= start_at)
            by_user_q = by_user_q.where(CompanyUsageEvent.occurred_at >= start_at)
        if end_at:
            base = base.where(CompanyUsageEvent.occurred_at <= end_at)
            by_feature_q = by_feature_q.where(CompanyUsageEvent.occurred_at <= end_at)
            by_user_q = by_user_q.where(CompanyUsageEvent.occurred_at <= end_at)

        return {
            "entity": company,
            "totals": session.execute(base).one(),
            "by_feature_rows": session.execute(
                by_feature_q.group_by(CompanyUsageEvent.feature_key).order_by(CompanyUsageEvent.feature_key.asc())
            ).all(),
            "by_user_rows": session.execute(
                by_user_q.group_by(CompanyUsageEvent.user_id).order_by(CompanyUsageEvent.user_id.asc())
            ).all(),
        }

    @staticmethod
    def ensure_company_grant(session, company_id, created_by=None):
        ensure_company_initial_grant(session, int(company_id), created_by=created_by)

    @staticmethod
    def company_token_balance(session, company_id):
        return get_company_token_balance(session, int(company_id))

    @staticmethod
    def company_token_totals(session, company_id):
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
        return int(granted or 0), abs(int(used or 0))

    @staticmethod
    def create_token_topup(session, *, company_id, weighted_tokens, created_by=None, note=None):
        ledger = CompanyTokenLedger(
            company_id=int(company_id),
            delta_weighted_tokens=int(weighted_tokens),
            reason="top_up",
            created_by=created_by,
            note=note,
        )
        session.add(ledger)
        session.flush()
        return ledger

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
        return {
            "request_count": int(totals[0] or 0),
            "total_tokens": int(totals[1] or 0),
            "billable_weighted_tokens": int(totals[2] or 0),
            "estimated_cost_usd": float(totals[3] or 0),
            "usage_limit": None,
            "remaining_weighted_tokens": get_company_token_balance(session, int(company_id)),
        }

    @staticmethod
    def _usage_period_column(period: str):
        if period == "monthly":
            return func.to_char(func.date_trunc("month", LlmUsageDailyAggregate.usage_date), "YYYY-MM")
        return func.to_char(LlmUsageDailyAggregate.usage_date, "YYYY-MM-DD")

    @staticmethod
    def get_llm_usage(session, *, scope, scope_id, period, start_date=None, end_date=None):
        column_by_scope = {
            "user": LlmUsageDailyAggregate.user_id,
            "company": LlmUsageDailyAggregate.company_id,
            "team": LlmUsageDailyAggregate.team_id,
        }
        event_column_by_scope = {
            "user": LlmUsageEvent.user_id,
            "company": LlmUsageEvent.company_id,
            "team": LlmUsageEvent.team_id,
        }
        filters = [column_by_scope[scope] == int(scope_id)]
        event_filters = [event_column_by_scope[scope] == int(scope_id)]
        if start_date:
            filters.append(LlmUsageDailyAggregate.usage_date >= start_date)
            event_filters.append(LlmUsageEvent.occurred_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            filters.append(LlmUsageDailyAggregate.usage_date <= end_date)
            event_filters.append(LlmUsageEvent.occurred_at <= datetime.combine(end_date, datetime.max.time()))

        period_col = AdminUsageRepository._usage_period_column(period).label("period")
        totals = session.execute(
            select(
                func.coalesce(func.sum(LlmUsageDailyAggregate.request_count), 0).label("request_count"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.billable_weighted_tokens), 0).label("billable_weighted_tokens"),
                func.coalesce(func.sum(LlmUsageDailyAggregate.estimated_cost_usd), 0).label("estimated_cost_usd"),
            ).where(*filters)
        ).one()

        by_period_rows = session.execute(
            select(
                period_col,
                func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                func.sum(LlmUsageDailyAggregate.prompt_tokens).label("prompt_tokens"),
                func.sum(LlmUsageDailyAggregate.completion_tokens).label("completion_tokens"),
                func.sum(LlmUsageDailyAggregate.cached_input_tokens).label("cached_input_tokens"),
                func.sum(LlmUsageDailyAggregate.reasoning_tokens).label("reasoning_tokens"),
                func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
                func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
                func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
            )
            .where(*filters)
            .group_by(period_col)
            .order_by(period_col.asc())
        ).all()

        by_user_aggregate = (
            select(
                LlmUsageDailyAggregate.user_id.label("user_id"),
                func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
                func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
                func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
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
                by_user_aggregate.c.request_count,
                by_user_aggregate.c.total_tokens,
                by_user_aggregate.c.billable_weighted_tokens,
                by_user_aggregate.c.estimated_cost_usd,
                last_user_event.c.last_used_at,
            )
            .select_from(by_user_aggregate)
            .outerjoin(User, User.id == by_user_aggregate.c.user_id)
            .outerjoin(last_user_event, last_user_event.c.user_id == by_user_aggregate.c.user_id)
            .order_by(by_user_aggregate.c.user_id.asc())
        ).all()

        by_team_rows = session.execute(
            select(
                LlmUsageDailyAggregate.team_id,
                func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
                func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
                func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
            )
            .where(*filters)
            .group_by(LlmUsageDailyAggregate.team_id)
            .order_by(LlmUsageDailyAggregate.team_id.asc())
        ).all()

        by_model_rows = session.execute(
            select(
                LlmUsageDailyAggregate.provider,
                LlmUsageDailyAggregate.model,
                func.sum(LlmUsageDailyAggregate.request_count).label("request_count"),
                func.sum(LlmUsageDailyAggregate.prompt_tokens).label("prompt_tokens"),
                func.sum(LlmUsageDailyAggregate.completion_tokens).label("completion_tokens"),
                func.sum(LlmUsageDailyAggregate.cached_input_tokens).label("cached_input_tokens"),
                func.sum(LlmUsageDailyAggregate.reasoning_tokens).label("reasoning_tokens"),
                func.sum(LlmUsageDailyAggregate.total_tokens).label("total_tokens"),
                func.sum(LlmUsageDailyAggregate.billable_weighted_tokens).label("billable_weighted_tokens"),
                func.sum(LlmUsageDailyAggregate.estimated_cost_usd).label("estimated_cost_usd"),
            )
            .where(*filters)
            .group_by(LlmUsageDailyAggregate.provider, LlmUsageDailyAggregate.model)
            .order_by(LlmUsageDailyAggregate.provider.asc(), LlmUsageDailyAggregate.model.asc())
        ).all()

        return {
            "totals": totals,
            "by_period_rows": by_period_rows,
            "by_user_rows": by_user_rows,
            "by_team_rows": by_team_rows,
            "by_model_rows": by_model_rows,
        }

    @staticmethod
    def list_model_prices(session, *, provider="", active_only=True):
        query = select(LlmModelPrice).order_by(
            LlmModelPrice.provider.asc(),
            LlmModelPrice.model.asc(),
            LlmModelPrice.effective_from.desc(),
        )
        if provider:
            query = query.where(LlmModelPrice.provider == provider)
        if active_only:
            now = datetime.now(timezone.utc)
            query = query.where(
                LlmModelPrice.effective_from <= now,
                (LlmModelPrice.effective_to.is_(None) | (LlmModelPrice.effective_to > now)),
            )
        return session.execute(query).scalars().all()

    @staticmethod
    def cleanup_expired_usage_events(*, retention_days):
        return cleanup_old_llm_usage_events(retention_days=retention_days)
