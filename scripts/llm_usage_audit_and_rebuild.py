#!/usr/bin/env python3
"""Audit and optionally rebuild LLM daily usage aggregates.

Default mode is read-only. Use --apply to backfill recoverable event dimensions
and rebuild llm_usage_daily_aggregates from llm_usage_events.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import config  # noqa: F401 - loads environment files
from db.engine import get_engine, init_engine
from sqlalchemy import text


FEATURE_CASE_SQL = """
CASE
    WHEN endpoint LIKE '/api/generator%' OR endpoint LIKE '/api/studies/%' OR endpoint LIKE '/api/plans%'
      OR endpoint = '/api/conversation/message' OR endpoint = '/api/study-helper/chat'
        THEN 'plan_generation'
    WHEN endpoint LIKE '/api/survey%' OR endpoint LIKE '/api/surveys%' OR endpoint LIKE '/api/survey-diagnoser%'
        THEN 'survey_generation'
    WHEN endpoint LIKE '/api/guideline%' OR endpoint LIKE '/api/guidelines%' OR endpoint LIKE '/api/extract-methodologies%'
        THEN 'guideline_generation'
    WHEN endpoint LIKE '/api/artifacts/%' OR endpoint LIKE '/api/artifact-ai%'
        THEN 'artifact_ai'
    WHEN endpoint LIKE '/api/screener%'
        THEN 'screener'
    WHEN endpoint LIKE '/api/workspace/generate-%'
        THEN 'workspace_ai'
    ELSE feature_key
END
"""


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(result) -> list[Dict[str, Any]]:
    return [dict(row._mapping) for row in result]


def _date_params(args: argparse.Namespace) -> Dict[str, Optional[str]]:
    return {"start_date": args.start_date, "end_date": args.end_date}


def print_json(label: str, payload: Any) -> None:
    print(f"\n## {label}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))


def audit(conn, args: argparse.Namespace) -> None:
    params = _date_params(args)
    date_filter_agg = """
        (:start_date IS NULL OR usage_date >= CAST(:start_date AS date))
        AND (:end_date IS NULL OR usage_date <= CAST(:end_date AS date))
    """
    date_filter_event = """
        (:start_date IS NULL OR occurred_at >= CAST(:start_date AS date))
        AND (:end_date IS NULL OR occurred_at < CAST(:end_date AS date) + INTERVAL '1 day')
    """

    print_json(
        "aggregate_null_unknown_summary",
        _rows(
            conn.execute(
                text(
                    f"""
                    SELECT
                        usage_date,
                        feature_key,
                        provider,
                        COUNT(*) AS rows,
                        SUM(request_count) AS request_count,
                        SUM(total_tokens) AS total_tokens,
                        SUM(billable_weighted_tokens) AS billable_weighted_tokens,
                        COUNT(*) FILTER (WHERE company_id IS NULL) AS company_id_null_rows,
                        COUNT(*) FILTER (WHERE user_id IS NULL) AS user_id_null_rows,
                        COUNT(*) FILTER (WHERE feature_key = 'unknown') AS unknown_feature_rows
                    FROM llm_usage_daily_aggregates
                    WHERE ({date_filter_agg})
                      AND (company_id IS NULL OR user_id IS NULL OR feature_key = 'unknown')
                    GROUP BY usage_date, feature_key, provider
                    ORDER BY usage_date DESC, rows DESC
                    LIMIT :limit
                    """
                ),
                {**params, "limit": args.limit},
            )
        ),
    )

    print_json(
        "unknown_events_by_endpoint",
        _rows(
            conn.execute(
                text(
                    f"""
                    SELECT
                        COALESCE(endpoint, '') AS endpoint,
                        provider,
                        model,
                        COUNT(*) AS events,
                        SUM(total_tokens) AS total_tokens,
                        COUNT(*) FILTER (WHERE company_id IS NULL) AS company_id_null_events,
                        COUNT(*) FILTER (WHERE user_id IS NULL) AS user_id_null_events,
                        MIN(occurred_at) AS first_seen_at,
                        MAX(occurred_at) AS last_seen_at
                    FROM llm_usage_events
                    WHERE ({date_filter_event})
                      AND (feature_key IS NULL OR feature_key = 'unknown' OR company_id IS NULL OR user_id IS NULL)
                    GROUP BY COALESCE(endpoint, ''), provider, model
                    ORDER BY events DESC
                    LIMIT :limit
                    """
                ),
                {**params, "limit": args.limit},
            )
        ),
    )

    print_json(
        "event_vs_aggregate_daily_mismatch",
        _rows(
            conn.execute(
                text(
                    f"""
                    WITH event_daily AS (
                        SELECT
                            occurred_at::date AS usage_date,
                            COALESCE(company_id, -1) AS company_key,
                            COALESCE(team_id, -1) AS team_key,
                            COALESCE(user_id, -1) AS user_key,
                            provider,
                            model,
                            COALESCE(feature_key, 'unknown') AS feature_key,
                            COUNT(*) AS request_count,
                            SUM(total_tokens) AS total_tokens,
                            SUM(billable_weighted_tokens) AS billable_weighted_tokens
                        FROM llm_usage_events
                        WHERE ({date_filter_event})
                        GROUP BY occurred_at::date, COALESCE(company_id, -1), COALESCE(team_id, -1),
                                 COALESCE(user_id, -1), provider, model, COALESCE(feature_key, 'unknown')
                    ),
                    aggregate_daily AS (
                        SELECT
                            usage_date,
                            COALESCE(company_id, -1) AS company_key,
                            COALESCE(team_id, -1) AS team_key,
                            COALESCE(user_id, -1) AS user_key,
                            provider,
                            model,
                            feature_key,
                            SUM(request_count) AS request_count,
                            SUM(total_tokens) AS total_tokens,
                            SUM(billable_weighted_tokens) AS billable_weighted_tokens
                        FROM llm_usage_daily_aggregates
                        WHERE ({date_filter_agg})
                        GROUP BY usage_date, COALESCE(company_id, -1), COALESCE(team_id, -1),
                                 COALESCE(user_id, -1), provider, model, feature_key
                    )
                    SELECT
                        COALESCE(e.usage_date, a.usage_date) AS usage_date,
                        COALESCE(e.provider, a.provider) AS provider,
                        COALESCE(e.model, a.model) AS model,
                        COALESCE(e.feature_key, a.feature_key) AS feature_key,
                        COALESCE(e.request_count, 0) AS event_request_count,
                        COALESCE(a.request_count, 0) AS aggregate_request_count,
                        COALESCE(e.total_tokens, 0) AS event_total_tokens,
                        COALESCE(a.total_tokens, 0) AS aggregate_total_tokens,
                        COALESCE(e.billable_weighted_tokens, 0) AS event_billable_weighted_tokens,
                        COALESCE(a.billable_weighted_tokens, 0) AS aggregate_billable_weighted_tokens
                    FROM event_daily e
                    FULL OUTER JOIN aggregate_daily a
                      ON e.usage_date = a.usage_date
                     AND e.company_key = a.company_key
                     AND e.team_key = a.team_key
                     AND e.user_key = a.user_key
                     AND e.provider = a.provider
                     AND e.model = a.model
                     AND e.feature_key = a.feature_key
                    WHERE COALESCE(e.request_count, 0) <> COALESCE(a.request_count, 0)
                       OR COALESCE(e.total_tokens, 0) <> COALESCE(a.total_tokens, 0)
                       OR COALESCE(e.billable_weighted_tokens, 0) <> COALESCE(a.billable_weighted_tokens, 0)
                    ORDER BY usage_date DESC
                    LIMIT :limit
                    """
                ),
                {**params, "limit": args.limit},
            )
        ),
    )

    print_json(
        "request_id_dimension_drift",
        _rows(
            conn.execute(
                text(
                    f"""
                    SELECT
                        request_id,
                        COUNT(*) AS events,
                        COUNT(DISTINCT COALESCE(company_id::text, 'NULL')) AS company_variants,
                        COUNT(DISTINCT COALESCE(user_id::text, 'NULL')) AS user_variants,
                        COUNT(DISTINCT COALESCE(feature_key, 'unknown')) AS feature_variants,
                        MIN(occurred_at) AS first_seen_at,
                        MAX(occurred_at) AS last_seen_at
                    FROM llm_usage_events
                    WHERE ({date_filter_event})
                      AND request_id IS NOT NULL
                    GROUP BY request_id
                    HAVING COUNT(DISTINCT COALESCE(company_id::text, 'NULL')) > 1
                        OR COUNT(DISTINCT COALESCE(user_id::text, 'NULL')) > 1
                        OR COUNT(DISTINCT COALESCE(feature_key, 'unknown')) > 1
                    ORDER BY events DESC
                    LIMIT :limit
                    """
                ),
                {**params, "limit": args.limit},
            )
        ),
    )


def apply_backfill(conn, args: argparse.Namespace) -> None:
    params = _date_params(args)
    event_filter = """
        (:start_date IS NULL OR occurred_at >= CAST(:start_date AS date))
        AND (:end_date IS NULL OR occurred_at < CAST(:end_date AS date) + INTERVAL '1 day')
    """
    aggregate_filter = """
        (:start_date IS NULL OR usage_date >= CAST(:start_date AS date))
        AND (:end_date IS NULL OR usage_date <= CAST(:end_date AS date))
    """

    result = conn.execute(
        text(
            f"""
            UPDATE llm_usage_events e
            SET company_id = u.company_id
            FROM users u
            WHERE ({event_filter})
              AND e.user_id = u.id
              AND e.company_id IS NULL
              AND u.company_id IS NOT NULL
            """
        ),
        params,
    )
    print_json("events_company_from_user_updated", {"rows": result.rowcount})

    result = conn.execute(
        text(
            f"""
            WITH owner_teams AS (
                SELECT DISTINCT ON (owner_id) owner_id AS user_id, id AS team_id
                FROM teams
                WHERE owner_id IS NOT NULL AND status != 'deleted'
                ORDER BY owner_id, created_at ASC, id ASC
            ),
            member_teams AS (
                SELECT DISTINCT ON (tm.user_id) tm.user_id, tm.team_id
                FROM team_members tm
                JOIN teams t ON t.id = tm.team_id
                WHERE t.status != 'deleted'
                ORDER BY tm.user_id, tm.joined_at ASC, tm.id ASC
            ),
            primary_teams AS (
                SELECT user_id, team_id FROM owner_teams
                UNION ALL
                SELECT mt.user_id, mt.team_id
                FROM member_teams mt
                WHERE NOT EXISTS (
                    SELECT 1 FROM owner_teams ot WHERE ot.user_id = mt.user_id
                )
            )
            UPDATE llm_usage_events e
            SET team_id = primary_teams.team_id
            FROM primary_teams
            WHERE ({event_filter})
              AND e.team_id IS NULL
              AND e.user_id = primary_teams.user_id
            """
        ),
        params,
    )
    print_json("events_team_from_user_updated", {"rows": result.rowcount})

    result = conn.execute(
        text(
            f"""
            UPDATE llm_usage_events e
            SET
                user_id = COALESCE(e.user_id, a.owner_id),
                company_id = COALESCE(e.company_id, u.company_id),
                feature_key = 'artifact_ai'
            FROM artifacts a
            LEFT JOIN users u ON u.id = a.owner_id
            WHERE ({event_filter})
              AND e.endpoint LIKE '/api/artifacts/%/modify'
              AND split_part(e.endpoint, '/', 4) ~ '^[0-9]+$'
              AND a.id = split_part(e.endpoint, '/', 4)::bigint
              AND (e.user_id IS NULL OR e.company_id IS NULL OR e.feature_key IS NULL OR e.feature_key = 'unknown')
            """
        ),
        params,
    )
    print_json("artifact_events_backfilled", {"rows": result.rowcount})

    result = conn.execute(
        text(
            f"""
            UPDATE llm_usage_events
            SET feature_key = {FEATURE_CASE_SQL}
            WHERE ({event_filter})
              AND endpoint IS NOT NULL
              AND (feature_key IS NULL OR feature_key = 'unknown')
            """
        ),
        params,
    )
    print_json("events_feature_from_endpoint_updated", {"rows": result.rowcount})

    result = conn.execute(
        text(f"DELETE FROM llm_usage_daily_aggregates WHERE ({aggregate_filter})"),
        params,
    )
    print_json("aggregates_deleted", {"rows": result.rowcount})

    result = conn.execute(
        text(
            f"""
            INSERT INTO llm_usage_daily_aggregates (
                usage_date,
                company_id,
                team_id,
                user_id,
                provider,
                model,
                feature_key,
                request_count,
                prompt_tokens,
                completion_tokens,
                cached_input_tokens,
                reasoning_tokens,
                total_tokens,
                billable_weighted_tokens,
                estimated_cost_usd,
                updated_at
            )
            SELECT
                occurred_at::date AS usage_date,
                company_id,
                team_id,
                user_id,
                provider,
                model,
                COALESCE(feature_key, 'unknown') AS feature_key,
                COUNT(*) AS request_count,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(billable_weighted_tokens), 0) AS billable_weighted_tokens,
                COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                MAX(occurred_at) AS updated_at
            FROM llm_usage_events
            WHERE ({event_filter})
            GROUP BY occurred_at::date, company_id, team_id, user_id, provider, model, COALESCE(feature_key, 'unknown')
            """
        ),
        params,
    )
    print_json("aggregates_inserted", {"rows": result.rowcount})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", help="Inclusive start date, YYYY-MM-DD")
    parser.add_argument("--end-date", help="Inclusive end date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=50, help="Max rows per audit section")
    parser.add_argument("--apply", action="store_true", help="Apply recoverable event backfills and rebuild aggregates")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_engine(validate_connection=True)
    engine = get_engine()

    if args.apply:
        with engine.begin() as conn:
            audit(conn, args)
            apply_backfill(conn, args)
            audit(conn, args)
    else:
        with engine.connect() as conn:
            audit(conn, args)


if __name__ == "__main__":
    main()
