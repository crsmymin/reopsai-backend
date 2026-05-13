"""Usage metering utilities for business company and LLM tracking."""

from __future__ import annotations

import math
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Tuple, TypeVar

from flask import g, has_request_context, request
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request
from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from reopsai.infrastructure.persistence.engine import session_scope
from reopsai.infrastructure.persistence.models.core import (
    CompanyTokenLedger,
    CompanyUsageEvent,
    LlmModelPrice,
    LlmUsageDailyAggregate,
    LlmUsageEvent,
    Team,
    TeamMember,
    TeamUsageEvent,
    User,
)


FEATURE_PREFIXES = {
    "plan_generation": [
        "/api/generator/create-plan",
        "/api/generator/create-plan-oneshot",
        "/api/generator/conversation-maker/finalize-oneshot",
        "/api/generator",
        "/api/conversation/message",
        "/api/study-helper/chat",
        "/api/studies/",
        "/api/plans",
    ],
    "survey_generation": ["/api/survey", "/api/surveys", "/api/survey-diagnoser"],
    "guideline_generation": ["/api/guideline", "/api/guidelines", "/api/extract-methodologies"],
    "artifact_ai": ["/api/artifacts/", "/api/artifact-ai"],
    "screener": ["/api/screener"],
    "workspace_ai": ["/api/workspace/generate-"],
}

INITIAL_COMPANY_WEIGHTED_TOKEN_GRANT = 100_000
BASE_WEIGHT_PRICE_PER_1M_USD = Decimal("0.15")
_LLM_USAGE_CONTEXT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("llm_usage_context", default=None)
_T = TypeVar("_T")


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _to_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _resolve_primary_team_id(db_session, user_id: Optional[int]) -> Optional[int]:
    if user_id is None:
        return None
    try:
        owner_team_id = db_session.execute(
            select(Team.id)
            .where(Team.owner_id == int(user_id), Team.status != "deleted")
            .order_by(Team.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if owner_team_id is not None:
            return int(owner_team_id)

        member_team_id = db_session.execute(
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == int(user_id), Team.status != "deleted")
            .order_by(TeamMember.joined_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        return int(member_team_id) if member_team_id is not None else None
    except Exception:
        return None


def set_llm_usage_context(context: Optional[Dict[str, Any]]) -> None:
    if context is None:
        _LLM_USAGE_CONTEXT.set(None)
        return
    _LLM_USAGE_CONTEXT.set(dict(context))


def get_llm_usage_context() -> Dict[str, Any]:
    return dict(_LLM_USAGE_CONTEXT.get() or {})


def run_with_llm_usage_context(context: Optional[Dict[str, Any]], func: Callable[..., _T], *args, **kwargs) -> _T:
    token = _LLM_USAGE_CONTEXT.set(dict(context) if context is not None else None)
    try:
        return func(*args, **kwargs)
    finally:
        _LLM_USAGE_CONTEXT.reset(token)


def stream_with_llm_usage_context(context: Optional[Dict[str, Any]], iterable: Iterable[_T]) -> Iterator[_T]:
    token = _LLM_USAGE_CONTEXT.set(dict(context) if context is not None else None)
    try:
        yield from iterable
    finally:
        _LLM_USAGE_CONTEXT.reset(token)


def _request_id_from_flask_context(fallback: Optional[str] = None) -> Optional[str]:
    if not has_request_context():
        return fallback
    request_id = getattr(g, "request_id", None) or fallback
    if not request_id:
        request_id = uuid.uuid4().hex
    g.request_id = request_id
    return request_id


def _resolve_company_id_for_user(user_id: Optional[int]) -> Optional[int]:
    if user_id is None or session_scope is None:
        return None
    try:
        with session_scope() as db_session:
            company_id = db_session.execute(
                select(User.company_id).where(User.id == int(user_id)).limit(1)
            ).scalar_one_or_none()
            return int(company_id) if company_id is not None else None
    except Exception:
        return None


def _resolve_team_id_for_user(user_id: Optional[int]) -> Optional[int]:
    if user_id is None or session_scope is None:
        return None
    try:
        with session_scope() as db_session:
            return _resolve_primary_team_id(db_session, int(user_id))
    except Exception:
        return None


def build_llm_usage_context(
    *,
    feature_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    user_id: Optional[Any] = None,
    company_id: Optional[Any] = None,
    team_id: Optional[Any] = None,
    request_id: Optional[str] = None,
    account_type: Optional[str] = None,
) -> Dict[str, Any]:
    usage_context = get_llm_usage_context()
    claims: Dict[str, Any] = {}
    identity = None

    if has_request_context():
        try:
            verify_jwt_in_request(optional=True)
            claims = get_jwt() or {}
            identity = get_jwt_identity()
        except Exception:
            claims = {}
        if user_id is None:
            user_id = request.headers.get("X-User-ID") or identity
        if endpoint is None:
            endpoint = request.path or usage_context.get("endpoint") or ""
        request_id = _request_id_from_flask_context(request_id or usage_context.get("request_id"))
    else:
        endpoint = endpoint if endpoint is not None else usage_context.get("endpoint") or ""
        request_id = request_id or usage_context.get("request_id")

    user_id_int = _to_optional_int(user_id)
    if user_id_int is None:
        user_id_int = _to_optional_int(identity)
    if user_id_int is None:
        user_id_int = _to_optional_int(usage_context.get("user_id"))

    company_id_int = _to_optional_int(company_id)
    if company_id_int is None:
        company_id_int = _to_optional_int(claims.get("company_id"))
    if company_id_int is None:
        company_id_int = _to_optional_int(usage_context.get("company_id"))
    if company_id_int is None:
        company_id_int = _resolve_company_id_for_user(user_id_int)

    team_id_int = _to_optional_int(team_id)
    if team_id_int is None:
        team_id_int = _to_optional_int(usage_context.get("team_id"))
    if team_id_int is None:
        team_id_int = _resolve_team_id_for_user(user_id_int)

    resolved_endpoint = endpoint or ""
    resolved_feature_key = (
        feature_key
        or classify_feature_key(resolved_endpoint)
        or usage_context.get("feature_key")
    )

    return {
        "company_id": company_id_int,
        "team_id": team_id_int,
        "user_id": user_id_int,
        "account_type": account_type or claims.get("account_type") or usage_context.get("account_type"),
        "endpoint": resolved_endpoint,
        "feature_key": resolved_feature_key,
        "request_id": request_id,
    }


def _request_context() -> Dict[str, Any]:
    usage_context = get_llm_usage_context()
    if not has_request_context():
        return usage_context
    return build_llm_usage_context()


def extract_openai_usage(usage_obj: Any) -> Dict[str, int]:
    if usage_obj is None:
        return {}
    prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
    completion_details = getattr(usage_obj, "completion_tokens_details", None)
    return {
        "prompt_tokens": _to_int(getattr(usage_obj, "prompt_tokens", None)),
        "completion_tokens": _to_int(getattr(usage_obj, "completion_tokens", None)),
        "total_tokens": _to_int(getattr(usage_obj, "total_tokens", None)),
        "cached_input_tokens": _to_int(getattr(prompt_details, "cached_tokens", None)),
        "reasoning_tokens": _to_int(getattr(completion_details, "reasoning_tokens", None)),
    }


def extract_gemini_usage(usage_obj: Any) -> Dict[str, int]:
    if usage_obj is None:
        return {}

    def pick(*names: str) -> int:
        for name in names:
            value = getattr(usage_obj, name, None)
            if value is not None:
                return _to_int(value)
        return 0

    return {
        "prompt_tokens": pick("prompt_token_count", "promptTokenCount"),
        "completion_tokens": pick("candidates_token_count", "candidatesTokenCount"),
        "total_tokens": pick("total_token_count", "totalTokenCount"),
        "cached_input_tokens": pick("cached_content_token_count", "cachedContentTokenCount"),
        "reasoning_tokens": pick("thoughts_token_count", "thoughtsTokenCount"),
    }


def _find_price(db_session, provider: str, model: str, occurred_at: datetime) -> Optional[LlmModelPrice]:
    return db_session.execute(
        select(LlmModelPrice)
        .where(
            LlmModelPrice.provider == provider,
            LlmModelPrice.model == model,
            LlmModelPrice.effective_from <= occurred_at,
            (LlmModelPrice.effective_to.is_(None) | (LlmModelPrice.effective_to > occurred_at)),
        )
        .order_by(LlmModelPrice.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()


def calculate_cost_and_weighted_tokens(
    *,
    price: Optional[LlmModelPrice],
    prompt_tokens: int,
    completion_tokens: int,
    cached_input_tokens: int,
) -> Tuple[Decimal, int]:
    if price is None:
        return Decimal("0"), 0

    cached_tokens = max(0, min(prompt_tokens, cached_input_tokens))
    non_cached_prompt_tokens = max(0, prompt_tokens - cached_tokens)
    input_rate = _to_decimal(price.input_per_1m)
    cached_rate = _to_decimal(price.cached_input_per_1m)
    if cached_rate <= 0:
        cached_rate = input_rate
    output_rate = _to_decimal(price.output_per_1m)

    weighted_decimal = (
        (Decimal(non_cached_prompt_tokens) * input_rate / BASE_WEIGHT_PRICE_PER_1M_USD)
        + (Decimal(cached_tokens) * cached_rate / BASE_WEIGHT_PRICE_PER_1M_USD)
        + (Decimal(completion_tokens) * output_rate / BASE_WEIGHT_PRICE_PER_1M_USD)
    )
    estimated_cost = (
        (Decimal(non_cached_prompt_tokens) * input_rate)
        + (Decimal(cached_tokens) * cached_rate)
        + (Decimal(completion_tokens) * output_rate)
    ) / Decimal(1_000_000)
    return estimated_cost, int(math.ceil(weighted_decimal))


def get_company_token_balance(db_session, company_id: int) -> int:
    total = db_session.execute(
        select(func.sum(CompanyTokenLedger.delta_weighted_tokens)).where(
            CompanyTokenLedger.company_id == int(company_id)
        )
    ).scalar_one()
    return int(total or 0)


def ensure_company_initial_grant(db_session, company_id: int, *, created_by: Optional[int] = None) -> bool:
    if not company_id:
        return False
    existing = db_session.execute(
        select(CompanyTokenLedger.id)
        .where(
            CompanyTokenLedger.company_id == int(company_id),
            CompanyTokenLedger.reason == "initial_grant",
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing:
        return False
    db_session.add(
        CompanyTokenLedger(
            company_id=int(company_id),
            delta_weighted_tokens=INITIAL_COMPANY_WEIGHTED_TOKEN_GRANT,
            reason="initial_grant",
            created_by=created_by,
            note="Initial 100k weighted token grant",
        )
    )
    return True


def is_company_quota_exceeded(company_id: Optional[int]) -> bool:
    if not company_id or session_scope is None:
        return False
    try:
        with session_scope() as db_session:
            ensure_company_initial_grant(db_session, int(company_id))
            return get_company_token_balance(db_session, int(company_id)) <= 0
    except Exception:
        return False


def cleanup_old_llm_usage_events(retention_days: int = 90) -> int:
    if session_scope is None:
        return 0
    retention_days = max(1, int(retention_days or 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    try:
        with session_scope() as db_session:
            result = db_session.execute(
                delete(LlmUsageEvent).where(LlmUsageEvent.occurred_at < cutoff)
            )
            return int(result.rowcount or 0)
    except Exception:
        return 0


def record_llm_call(
    *,
    provider: str,
    model: str,
    usage: Optional[Dict[str, Any]],
) -> Optional[int]:
    usage = usage or {}
    prompt_tokens = _to_int(usage.get("prompt_tokens"))
    completion_tokens = _to_int(usage.get("completion_tokens"))
    total_tokens = _to_int(usage.get("total_tokens"))
    cached_input_tokens = _to_int(usage.get("cached_input_tokens"))
    reasoning_tokens = _to_int(usage.get("reasoning_tokens"))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens

    context = _request_context()
    company_id = context.get("company_id")
    team_id = context.get("team_id")
    user_id = context.get("user_id")
    feature_key = context.get("feature_key") or "unknown"
    endpoint = context.get("endpoint")
    request_id = context.get("request_id")
    occurred_at = datetime.now(timezone.utc)

    if session_scope is None:
        track_llm_usage(usage)
        return None

    event_id = None
    try:
        with session_scope() as db_session:
            price = _find_price(db_session, provider, model, occurred_at)
            estimated_cost, weighted_tokens = calculate_cost_and_weighted_tokens(
                price=price,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_input_tokens=cached_input_tokens,
            )
            event = LlmUsageEvent(
                company_id=int(company_id) if company_id is not None else None,
                team_id=int(team_id) if team_id is not None else None,
                user_id=int(user_id) if user_id is not None else None,
                provider=provider,
                model=model,
                feature_key=feature_key,
                endpoint=(endpoint or "")[:255],
                request_id=request_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                billable_weighted_tokens=weighted_tokens,
                estimated_cost_usd=estimated_cost,
                price_catalog_id=price.id if price else None,
                occurred_at=occurred_at,
            )
            db_session.add(event)
            db_session.flush()
            event_id = event.id

            if company_id is not None and weighted_tokens > 0:
                ensure_company_initial_grant(db_session, int(company_id))
                db_session.add(
                    CompanyTokenLedger(
                        company_id=int(company_id),
                        delta_weighted_tokens=-weighted_tokens,
                        reason="usage",
                        reference_event_id=event.id,
                        note=f"{provider}/{model} usage",
                    )
                )

            aggregate_values = {
                "usage_date": occurred_at.date(),
                "company_id": int(company_id) if company_id is not None else None,
                "team_id": int(team_id) if team_id is not None else None,
                "user_id": int(user_id) if user_id is not None else None,
                "provider": provider,
                "model": model,
                "feature_key": feature_key,
                "request_count": 1,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_input_tokens": cached_input_tokens,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "billable_weighted_tokens": weighted_tokens,
                "estimated_cost_usd": estimated_cost,
                "updated_at": occurred_at,
            }
            insert_stmt = pg_insert(LlmUsageDailyAggregate.__table__).values(**aggregate_values)
            db_session.execute(
                insert_stmt.on_conflict_do_update(
                    index_elements=[
                        LlmUsageDailyAggregate.usage_date,
                        literal_column("COALESCE(company_id, -1)"),
                        literal_column("COALESCE(team_id, -1)"),
                        literal_column("COALESCE(user_id, -1)"),
                        LlmUsageDailyAggregate.provider,
                        LlmUsageDailyAggregate.model,
                        LlmUsageDailyAggregate.feature_key,
                    ],
                    set_={
                        "request_count": LlmUsageDailyAggregate.request_count + 1,
                        "prompt_tokens": LlmUsageDailyAggregate.prompt_tokens + prompt_tokens,
                        "completion_tokens": LlmUsageDailyAggregate.completion_tokens + completion_tokens,
                        "cached_input_tokens": LlmUsageDailyAggregate.cached_input_tokens + cached_input_tokens,
                        "reasoning_tokens": LlmUsageDailyAggregate.reasoning_tokens + reasoning_tokens,
                        "total_tokens": LlmUsageDailyAggregate.total_tokens + total_tokens,
                        "billable_weighted_tokens": LlmUsageDailyAggregate.billable_weighted_tokens + weighted_tokens,
                        "estimated_cost_usd": LlmUsageDailyAggregate.estimated_cost_usd + estimated_cost,
                        "updated_at": occurred_at,
                    },
                )
            )
    except Exception as exc:
        print(f"[WARN] record_llm_call failed: provider={provider}, model={model}, error={exc}")
        traceback.print_exc()
        event_id = None

    track_llm_usage(usage)
    return event_id


def classify_feature_key(endpoint: str) -> Optional[str]:
    endpoint = (endpoint or "").strip().lower()
    if not endpoint:
        return None
    for feature, prefixes in FEATURE_PREFIXES.items():
        if any(endpoint.startswith(prefix.lower()) for prefix in prefixes):
            return feature
    return None


def track_llm_usage(usage: Optional[Dict[str, Any]]) -> None:
    if not has_request_context() or not usage:
        return

    current = getattr(g, "llm_usage", None) or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((usage or {}).get("completion_tokens") or 0)
    total_tokens = int((usage or {}).get("total_tokens") or 0)
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens

    current["prompt_tokens"] += prompt_tokens
    current["completion_tokens"] += completion_tokens
    current["total_tokens"] += total_tokens
    g.llm_usage = current


def record_team_usage_event(
    *,
    endpoint: str,
    team_id: Optional[int],
    user_id: Optional[int],
    feature_key: Optional[str],
) -> None:
    if not team_id or not feature_key or session_scope is None:
        return

    usage = getattr(g, "llm_usage", None) or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens

    try:
        with session_scope() as db_session:
            team = db_session.execute(
                select(Team)
                .where(Team.id == int(team_id), Team.status != "deleted")
                .limit(1)
            ).scalar_one_or_none()
            if not team:
                return
            db_session.add(
                TeamUsageEvent(
                    team_id=int(team_id),
                    user_id=int(user_id) if user_id is not None else None,
                    feature_key=feature_key,
                    endpoint=(endpoint or "")[:255],
                    request_count=1,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            )
    except Exception:
        return


def record_company_usage_event(
    *,
    endpoint: str,
    company_id: Optional[int],
    user_id: Optional[int],
    feature_key: Optional[str],
) -> None:
    if not company_id or not feature_key or session_scope is None:
        return

    usage = getattr(g, "llm_usage", None) or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens

    try:
        with session_scope() as db_session:
            db_session.add(
                CompanyUsageEvent(
                    company_id=int(company_id),
                    user_id=int(user_id) if user_id is not None else None,
                    feature_key=feature_key,
                    endpoint=(endpoint or "")[:255],
                    request_count=1,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            )
    except Exception:
        return
