"""Compatibility wrapper for keyword helpers."""

from __future__ import annotations

from reopsai.application.keywords import (
    KEYWORD_STOPWORDS,
    KEYWORD_STOPWORDS_LOWER,
    _clean_metadata_text,
    _refine_extracted_keywords,
    create_concise_summary_for_rag,
    extract_contextual_keywords_from_input,
    fetch_project_keywords,
)

__all__ = [
    "KEYWORD_STOPWORDS",
    "KEYWORD_STOPWORDS_LOWER",
    "_clean_metadata_text",
    "_refine_extracted_keywords",
    "create_concise_summary_for_rag",
    "extract_contextual_keywords_from_input",
    "fetch_project_keywords",
]
