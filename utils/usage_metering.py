"""Usage metering utilities for business company tracking."""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask import g, has_request_context
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import CompanyUsageEvent, Team, TeamUsageEvent


FEATURE_PREFIXES = {
    "plan_generation": ["/api/generator", "/api/studies/", "/api/plans", "/api/generator/create-plan"],
    "survey_generation": ["/api/surveys", "/api/survey"],
    "guideline_generation": ["/api/guidelines", "/api/guideline"],
    "artifact_ai": ["/api/artifacts/", "/api/artifact-ai"],
    "screener": ["/api/screener"],
}


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
