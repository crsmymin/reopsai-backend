from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db.repositories.admin_usage_repository import AdminUsageRepository


@dataclass(frozen=True)
class AdminUsageResult:
    status: str
    data: Any = None


def serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def serialize_decimal(value):
    return float(value or 0)


def row_value(row, name, index, default=0):
    if hasattr(row, name):
        return getattr(row, name)
    try:
        return row[index]
    except Exception:
        return default


def usage_totals_payload(row):
    return {
        "request_count": int(row_value(row, "request_count", 0) or 0),
        "prompt_tokens": int(row_value(row, "prompt_tokens", 1) or 0),
        "completion_tokens": int(row_value(row, "completion_tokens", 2) or 0),
        "cached_input_tokens": int(row_value(row, "cached_input_tokens", 3) or 0),
        "reasoning_tokens": int(row_value(row, "reasoning_tokens", 4) or 0),
        "total_tokens": int(row_value(row, "total_tokens", 5) or 0),
        "billable_weighted_tokens": int(row_value(row, "billable_weighted_tokens", 6) or 0),
        "estimated_cost_usd": serialize_decimal(row_value(row, "estimated_cost_usd", 7)),
    }


def legacy_totals_payload(row):
    return {
        "request_count": int(row[0] or 0),
        "prompt_tokens": int(row[1] or 0),
        "completion_tokens": int(row[2] or 0),
        "total_tokens": int(row[3] or 0),
    }


def llm_usage_payload(raw):
    return {
        "totals": usage_totals_payload(raw["totals"]),
        "by_period": [
            {"period": row.period, **usage_totals_payload(row)}
            for row in raw["by_period_rows"]
        ],
        "by_user": [
            {
                "user_id": row.user_id,
                "email": row.email,
                "name": row.name,
                "request_count": int(row.request_count or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": serialize_decimal(row.estimated_cost_usd),
                "last_used_at": serialize_dt(row.last_used_at),
            }
            for row in raw["by_user_rows"]
        ],
        "by_team": [
            {
                "team_id": row.team_id,
                "request_count": int(row.request_count or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": serialize_decimal(row.estimated_cost_usd),
            }
            for row in raw["by_team_rows"]
        ],
        "by_model": [
            {
                "provider": row.provider,
                "model": row.model,
                "request_count": int(row.request_count or 0),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "cached_input_tokens": int(row.cached_input_tokens or 0),
                "reasoning_tokens": int(row.reasoning_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "billable_weighted_tokens": int(row.billable_weighted_tokens or 0),
                "estimated_cost_usd": serialize_decimal(row.estimated_cost_usd),
            }
            for row in raw["by_model_rows"]
        ],
    }


class AdminUsageService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY):
        if repository is None:
            repository = AdminUsageRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory

    def db_ready(self):
        return self.session_factory is not None

    def get_team_usage(self, *, team_id, start_at=None, end_at=None):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            raw = self.repository.get_legacy_team_usage(
                db_session,
                team_id=team_id,
                start_at=start_at,
                end_at=end_at,
            )
            if not raw:
                return AdminUsageResult("not_found")
            team = raw["entity"]
            return AdminUsageResult(
                "ok",
                {
                    "team": {"id": team.id, "name": team.name, "plan_code": team.plan_code or "starter"},
                    "window": {"start_at": serialize_dt(start_at), "end_at": serialize_dt(end_at)},
                    "totals": legacy_totals_payload(raw["totals"]),
                    "by_feature": [
                        {
                            "feature_key": row.feature_key,
                            "request_count": int(row.request_count or 0),
                            "total_tokens": int(row.total_tokens or 0),
                        }
                        for row in raw["by_feature_rows"]
                    ],
                    "by_user": [
                        {
                            "user_id": row.user_id,
                            "request_count": int(row.request_count or 0),
                            "total_tokens": int(row.total_tokens or 0),
                        }
                        for row in raw["by_user_rows"]
                    ],
                },
            )

    def get_company_usage(self, *, company_id, start_at=None, end_at=None):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            raw = self.repository.get_legacy_company_usage(
                db_session,
                company_id=company_id,
                start_at=start_at,
                end_at=end_at,
            )
            if not raw:
                return AdminUsageResult("not_found")
            company = raw["entity"]
            return AdminUsageResult(
                "ok",
                {
                    "company": {"id": company.id, "name": company.name, "status": company.status},
                    "window": {"start_at": serialize_dt(start_at), "end_at": serialize_dt(end_at)},
                    "totals": legacy_totals_payload(raw["totals"]),
                    "by_feature": [
                        {
                            "feature_key": row.feature_key,
                            "request_count": int(row.request_count or 0),
                            "total_tokens": int(row.total_tokens or 0),
                        }
                        for row in raw["by_feature_rows"]
                    ],
                    "by_user": [
                        {
                            "user_id": row.user_id,
                            "request_count": int(row.request_count or 0),
                            "total_tokens": int(row.total_tokens or 0),
                        }
                        for row in raw["by_user_rows"]
                    ],
                },
            )

    def get_user_llm_usage(self, *, user_id, period, start_date=None, end_date=None):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user(db_session, user_id)
            if not user:
                return AdminUsageResult("not_found")
            usage = self.repository.get_llm_usage(
                db_session,
                scope="user",
                scope_id=user_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
            )
            return AdminUsageResult(
                "ok",
                {
                    "user": {"id": user.id, "email": user.email, "company_id": user.company_id},
                    "period": period,
                    "window": {
                        "start_date": start_date.isoformat() if start_date else None,
                        "end_date": end_date.isoformat() if end_date else None,
                    },
                    **llm_usage_payload(usage),
                },
            )

    def get_company_llm_usage(self, *, company_id, period, start_date=None, end_date=None):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            company = self.repository.get_company(db_session, company_id)
            if not company:
                return AdminUsageResult("not_found")
            self.repository.ensure_company_grant(db_session, company_id)
            balance = self.repository.company_token_balance(db_session, company_id)
            usage = self.repository.get_llm_usage(
                db_session,
                scope="company",
                scope_id=company_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
            )
            return AdminUsageResult(
                "ok",
                {
                    "company": {"id": company.id, "name": company.name, "status": company.status},
                    "period": period,
                    "window": {
                        "start_date": start_date.isoformat() if start_date else None,
                        "end_date": end_date.isoformat() if end_date else None,
                    },
                    "remaining_weighted_tokens": balance,
                    **llm_usage_payload(usage),
                },
            )

    def get_team_llm_usage(self, *, team_id, period, start_date=None, end_date=None):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            team = self.repository.get_team(db_session, team_id)
            if not team:
                return AdminUsageResult("not_found")
            usage = self.repository.get_llm_usage(
                db_session,
                scope="team",
                scope_id=team_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
            )
            return AdminUsageResult(
                "ok",
                {
                    "team": {
                        "id": team.id,
                        "name": team.name,
                        "status": team.status,
                        "plan_code": team.plan_code,
                        "owner_id": team.owner_id,
                    },
                    "period": period,
                    "window": {
                        "start_date": start_date.isoformat() if start_date else None,
                        "end_date": end_date.isoformat() if end_date else None,
                    },
                    **llm_usage_payload(usage),
                },
            )

    def get_company_token_balance(self, *, company_id):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            company = self.repository.get_company(db_session, company_id)
            if not company:
                return AdminUsageResult("not_found")
            self.repository.ensure_company_grant(db_session, company_id)
            balance = self.repository.company_token_balance(db_session, company_id)
            granted, used = self.repository.company_token_totals(db_session, company_id)
            return AdminUsageResult(
                "ok",
                {
                    "company": {"id": company.id, "name": company.name, "status": company.status},
                    "granted_weighted_tokens": granted,
                    "used_weighted_tokens": used,
                    "remaining_weighted_tokens": balance,
                },
            )

    def create_company_token_topup(self, *, company_id, weighted_tokens, created_by=None, note=None):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            company = self.repository.get_company(db_session, company_id)
            if not company:
                return AdminUsageResult("not_found")
            self.repository.ensure_company_grant(db_session, company_id, created_by=created_by)
            ledger = self.repository.create_token_topup(
                db_session,
                company_id=company_id,
                weighted_tokens=weighted_tokens,
                created_by=created_by,
                note=note,
            )
            balance = self.repository.company_token_balance(db_session, company_id)
            return AdminUsageResult(
                "ok",
                {
                    "company": {"id": company.id, "name": company.name, "status": company.status},
                    "ledger": {
                        "id": ledger.id,
                        "company_id": ledger.company_id,
                        "delta_weighted_tokens": ledger.delta_weighted_tokens,
                        "reason": ledger.reason,
                        "created_by": ledger.created_by,
                        "note": ledger.note,
                        "created_at": serialize_dt(ledger.created_at),
                    },
                    "remaining_weighted_tokens": balance,
                },
            )

    def list_model_prices(self, *, provider="", active_only=True):
        if not self.db_ready():
            return AdminUsageResult("db_unavailable")
        with self.session_factory() as db_session:
            prices = self.repository.list_model_prices(
                db_session,
                provider=provider,
                active_only=active_only,
            )
            return AdminUsageResult(
                "ok",
                {
                    "prices": [
                        {
                            "id": price.id,
                            "provider": price.provider,
                            "model": price.model,
                            "effective_from": serialize_dt(price.effective_from),
                            "effective_to": serialize_dt(price.effective_to),
                            "currency": price.currency,
                            "input_per_1m": serialize_decimal(price.input_per_1m),
                            "cached_input_per_1m": serialize_decimal(price.cached_input_per_1m),
                            "output_per_1m": serialize_decimal(price.output_per_1m),
                            "reasoning_policy": price.reasoning_policy,
                            "source_url": price.source_url,
                        }
                        for price in prices
                    ],
                },
            )

    def delete_expired_llm_usage_events(self, *, retention_days):
        deleted_count = self.repository.cleanup_expired_usage_events(retention_days=retention_days)
        return AdminUsageResult(
            "ok",
            {"retention_days": int(retention_days), "deleted_count": int(deleted_count)},
        )


admin_usage_service = AdminUsageService()
