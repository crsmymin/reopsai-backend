"""Keyword helper exports used by application services."""

from __future__ import annotations

from importlib import import_module


_keyword_utils = import_module("utils.keyword_utils")

_clean_metadata_text = _keyword_utils._clean_metadata_text
_refine_extracted_keywords = _keyword_utils._refine_extracted_keywords
extract_contextual_keywords_from_input = _keyword_utils.extract_contextual_keywords_from_input
fetch_project_keywords = _keyword_utils.fetch_project_keywords

__all__ = [
    "_clean_metadata_text",
    "_refine_extracted_keywords",
    "extract_contextual_keywords_from_input",
    "fetch_project_keywords",
]
