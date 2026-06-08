#!/usr/bin/env python3
"""Copy local persona asset files to S3 and flip verified DB rows.

The script is intentionally conservative:
- dry-run is the default;
- local files are never deleted;
- each row is updated only after S3 HEAD verifies the uploaded object size.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: F401 - loads backend env files
from sqlalchemy import select

from reopsai.infrastructure.database import init_engine, session_scope
from reopsai.infrastructure.persistence.models.persona import PersonaAsset
from reopsai.infrastructure.persona_storage import persona_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local persona asset files to S3.")
    parser.add_argument("--commit", action="store_true", help="Upload files and update DB rows. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of local assets to process.")
    parser.add_argument("--company-id", type=int, help="Restrict migration to one company.")
    parser.add_argument("--asset-type", action="append", help="Restrict migration to asset_type. Can be repeated.")
    parser.add_argument("--include-existing", action="store_true", help="Update DB if an S3 object with matching size already exists.")
    return parser.parse_args()


def local_assets_query(args: argparse.Namespace):
    query = select(PersonaAsset).where(
        PersonaAsset.storage_backend == "local",
        PersonaAsset.deleted_at.is_(None),
    ).order_by(PersonaAsset.id.asc())
    if args.company_id:
        query = query.where(PersonaAsset.company_id == args.company_id)
    if args.asset_type:
        query = query.where(PersonaAsset.asset_type.in_(args.asset_type))
    if args.limit and args.limit > 0:
        query = query.limit(args.limit)
    return query


def migrate_asset(asset: PersonaAsset, *, commit: bool, include_existing: bool) -> str:
    path = persona_storage.resolve_local_path(asset.storage_key)
    if not path.exists() or not path.is_file():
        return "missing_local"

    local_size = path.stat().st_size
    remote_size = persona_storage.s3_object_size(asset.storage_key)
    if remote_size == local_size:
        if commit and include_existing:
            asset.storage_backend = "s3"
        return "existing_s3_verified"
    if remote_size is not None and remote_size != local_size:
        return "remote_size_mismatch"
    if not commit:
        return "ready"

    copied_size = persona_storage.copy_local_file_to_s3(asset.storage_key, mime_type=asset.mime_type)
    verified_size = persona_storage.s3_object_size(asset.storage_key)
    if verified_size != copied_size:
        return "verify_failed"
    asset.storage_backend = "s3"
    return "migrated"


def main() -> int:
    args = parse_args()
    if persona_storage.backend != "s3":
        print("PERSONA_STORAGE_BACKEND must be set to s3 for this migration.")
        return 2
    if not init_engine(validate_connection=True):
        print("DATABASE_URL is required.")
        return 2

    counters: dict[str, int] = {}
    with session_scope() as session:
        assets = session.execute(local_assets_query(args)).scalars().all()
        for asset in assets:
            status = migrate_asset(asset, commit=args.commit, include_existing=args.include_existing)
            counters[status] = counters.get(status, 0) + 1
            print(f"{status}: id={asset.id} company={asset.company_id} type={asset.asset_type} key={asset.storage_key}")

    mode = "commit" if args.commit else "dry-run"
    print(f"mode={mode} total={sum(counters.values())} {counters}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
