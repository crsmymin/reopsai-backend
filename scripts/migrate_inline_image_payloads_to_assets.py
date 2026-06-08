#!/usr/bin/env python3
"""Move inline data:image payloads from JSONB records into persona_assets.

Dry-run is the default. With --commit, inline image data in UI/A-B test JSONB
fields is saved through PersonaStorage and replaced with /api/persona/storage/{id}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: F401 - loads backend env files
from sqlalchemy import Text, cast, or_, select

from reopsai.application.persona_service import PersonaService, _parse_image_data_url
from reopsai.infrastructure.database import init_engine, session_scope
from reopsai.infrastructure.persistence.models.persona import PersonaABTest, PersonaUITest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate inline data:image JSON payloads to persona assets.")
    parser.add_argument("--commit", action="store_true", help="Actually create assets and update JSONB fields. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows per table to process.")
    parser.add_argument("--company-id", type=int, help="Restrict migration to one company.")
    return parser.parse_args()


def contains_inline_image(value) -> bool:
    if isinstance(value, str):
        return _parse_image_data_url(value) is not None
    if isinstance(value, list):
        return any(contains_inline_image(item) for item in value)
    if isinstance(value, dict):
        return any(contains_inline_image(item) for item in value.values())
    return False


def inline_image_count(value) -> int:
    if isinstance(value, str):
        return 1 if _parse_image_data_url(value) is not None else 0
    if isinstance(value, list):
        return sum(inline_image_count(item) for item in value)
    if isinstance(value, dict):
        return sum(inline_image_count(item) for item in value.values())
    return 0


def ui_query(args: argparse.Namespace):
    query = select(PersonaUITest).where(
        PersonaUITest.deleted_at.is_(None),
        cast(PersonaUITest.source_data, Text).like("%data:image/%"),
    ).order_by(PersonaUITest.id.asc())
    if args.company_id:
        query = query.where(PersonaUITest.company_id == args.company_id)
    if args.limit and args.limit > 0:
        query = query.limit(args.limit)
    return query


def ab_query(args: argparse.Namespace):
    query = select(PersonaABTest).where(
        PersonaABTest.deleted_at.is_(None),
        or_(
            cast(PersonaABTest.screens, Text).like("%data:image/%"),
            cast(PersonaABTest.context_data, Text).like("%data:image/%"),
        ),
    ).order_by(PersonaABTest.id.asc())
    if args.company_id:
        query = query.where(PersonaABTest.company_id == args.company_id)
    if args.limit and args.limit > 0:
        query = query.limit(args.limit)
    return query


def main() -> int:
    args = parse_args()
    if not init_engine(validate_connection=True):
        print("DATABASE_URL is required.")
        return 2

    service = PersonaService()
    ui_rows = 0
    ui_images = 0
    ab_rows = 0
    ab_images = 0
    with session_scope() as session:
        for test in session.execute(ui_query(args)).scalars().all():
            count = inline_image_count(test.source_data)
            if count <= 0:
                continue
            ui_rows += 1
            ui_images += count
            print(f"ui_inline_candidate: id={test.id} company={test.company_id} images={count}")
            if args.commit:
                test.source_data = service._persist_inline_images_in_payload(
                    session,
                    company_id=test.company_id,
                    user_id=test.created_by_user_id,
                    payload=json.loads(json.dumps(test.source_data)),
                    asset_type="test_source_inline",
                )
        for test in session.execute(ab_query(args)).scalars().all():
            count = inline_image_count(test.screens) + inline_image_count(test.context_data)
            if count <= 0:
                continue
            ab_rows += 1
            ab_images += count
            print(f"ab_inline_candidate: id={test.id} company={test.company_id} images={count}")
            if args.commit:
                if contains_inline_image(test.screens):
                    test.screens = service._persist_inline_images_in_payload(
                        session,
                        company_id=test.company_id,
                        user_id=test.created_by_user_id,
                        payload=json.loads(json.dumps(test.screens)),
                        asset_type="ab_test_source_inline",
                    )
                if contains_inline_image(test.context_data):
                    test.context_data = service._persist_inline_images_in_payload(
                        session,
                        company_id=test.company_id,
                        user_id=test.created_by_user_id,
                        payload=json.loads(json.dumps(test.context_data)),
                        asset_type="ab_test_source_inline",
                    )

    mode = "commit" if args.commit else "dry-run"
    print(f"mode={mode} ui_rows={ui_rows} ui_images={ui_images} ab_rows={ab_rows} ab_images={ab_images}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
