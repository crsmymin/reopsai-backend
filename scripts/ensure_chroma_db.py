#!/usr/bin/env python3
"""
Ensure the local Chroma RAG database exists before starting the app.
"""

import os
import sys

import chromadb
from chromadb.config import Settings


DB_PATH = os.getenv("RAG_DB_PATH", "./chroma_db")
COLLECTION_NAME = os.getenv("RAG_COLLECTION_NAME", "ux_rag")
MIN_DOCUMENTS = int(os.getenv("RAG_MIN_DOCUMENTS", "1"))
FORCE_REBUILD = os.getenv("FORCE_RAG_REBUILD", "").lower() in {"1", "true", "yes"}


def collection_has_documents() -> bool:
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


def main() -> int:
    if FORCE_REBUILD:
        print("FORCE_RAG_REBUILD is enabled. Rebuilding ChromaDB.")
    elif collection_has_documents():
        print("ChromaDB is ready. Skipping RAG database build.")
        return 0
    else:
        print("ChromaDB is missing or empty. Building RAG database.")

    from build_improved_database import build_improved_database

    if build_improved_database():
        return 0

    print("Failed to build ChromaDB.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
