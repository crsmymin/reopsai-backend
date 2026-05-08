#!/usr/bin/env python3
"""
Ensure the local Chroma RAG database exists before starting the app.
"""

import os
import subprocess
import sys
from pathlib import Path

import chromadb
from chromadb.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = os.getenv("RAG_DB_PATH", "./chroma_db")
COLLECTION_NAME = os.getenv("RAG_COLLECTION_NAME", "ux_rag")
MIN_DOCUMENTS = int(os.getenv("RAG_MIN_DOCUMENTS", "1"))
FORCE_REBUILD = os.getenv("FORCE_RAG_REBUILD", "").lower() in {"1", "true", "yes"}


def database_path_has_files() -> bool:
    db_path = Path(DB_PATH)
    return db_path.exists() and any(db_path.iterdir())


def collection_has_documents() -> bool:
    if not database_path_has_files():
        print(f"ChromaDB path is missing or empty (path={DB_PATH}).")
        return False

    client = None
    collection = None
    try:
        client = chromadb.PersistentClient(
            path=DB_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_collection(name=COLLECTION_NAME)
        count = collection.count()
        print(
            f"ChromaDB collection '{COLLECTION_NAME}' has {count} documents "
            f"(path={DB_PATH})."
        )
        return count >= MIN_DOCUMENTS
    except Exception as exc:
        print(f"ChromaDB is not ready yet ({exc}).")
        return False
    finally:
        del collection
        del client


def main() -> int:
    if FORCE_REBUILD:
        print("FORCE_RAG_REBUILD is enabled. Rebuilding ChromaDB.")
    elif collection_has_documents():
        print("ChromaDB is ready. Skipping RAG database build.")
        return 0
    else:
        print("ChromaDB is missing or empty. Building RAG database.")

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "build_improved_database.py")],
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        print("Failed to build ChromaDB.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
