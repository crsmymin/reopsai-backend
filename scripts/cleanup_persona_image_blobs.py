#!/usr/bin/env python3
"""Clear redundant image blobs after asset-backed images exist.

Dry-run is the default. Rows are modified only when --commit is passed, and only
when the image already points to asset-backed storage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: F401 - loads backend env files
from sqlalchemy import or_, select

from reopsai.infrastructure.database import init_engine, session_scope
from reopsai.infrastructure.persistence.models.persona import Persona, PersonaAsset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear redundant persona image blobs.")
    parser.add_argument("--commit", action="store_true", help="Actually clear redundant DB blobs. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of rows per table to process.")
    parser.add_argument("--company-id", type=int, help="Restrict cleanup to one company.")
    return parser.parse_args()


def cleanup_query(args: argparse.Namespace):
    query = select(Persona).where(
        Persona.image_data.is_not(None),
        or_(
            Persona.image_asset_id.is_not(None),
            Persona.image_url.like("/api/persona/storage/%"),
        ),
        Persona.deleted_at.is_(None),
    ).order_by(Persona.id.asc())
    if args.company_id:
        query = query.where(Persona.company_id == args.company_id)
    if args.limit and args.limit > 0:
        query = query.limit(args.limit)
    return query


def asset_blob_query(args: argparse.Namespace):
    query = select(PersonaAsset).where(
        PersonaAsset.data.is_not(None),
        PersonaAsset.deleted_at.is_(None),
        PersonaAsset.storage_key.is_not(None),
    ).order_by(PersonaAsset.id.asc())
    if args.company_id:
        query = query.where(PersonaAsset.company_id == args.company_id)
    if args.limit and args.limit > 0:
        query = query.limit(args.limit)
    return query


def main() -> int:
    args = parse_args()
    if not init_engine(validate_connection=True):
        print("DATABASE_URL is required.")
        return 2

    persona_bytes = 0
    persona_count = 0
    asset_bytes = 0
    asset_count = 0
    with session_scope() as session:
        personas = session.execute(cleanup_query(args)).scalars().all()
        for persona in personas:
            byte_size = len(persona.image_data or b"")
            persona_bytes += byte_size
            persona_count += 1
            print(
                f"persona_blob_candidate: id={persona.id} company={persona.company_id} "
                f"asset_id={persona.image_asset_id} bytes={byte_size}"
            )
            if args.commit:
                persona.image_data = None
        assets = session.execute(asset_blob_query(args)).scalars().all()
        for asset in assets:
            byte_size = len(asset.data or b"")
            asset_bytes += byte_size
            asset_count += 1
            print(
                f"asset_blob_candidate: id={asset.id} company={asset.company_id} "
                f"backend={asset.storage_backend} key={asset.storage_key} bytes={byte_size}"
            )
            if args.commit:
                asset.data = None

    mode = "commit" if args.commit else "dry-run"
    print(
        f"mode={mode} persona_rows={persona_count} persona_bytes={persona_bytes} "
        f"asset_rows={asset_count} asset_bytes={asset_bytes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
