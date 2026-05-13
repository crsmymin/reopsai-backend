"""Keyword preparation helpers for plan generation."""

from __future__ import annotations

from api_logger import log_keyword_extraction
from reopsai.application.keywords import _refine_extracted_keywords


def normalize_project_keywords(project_keywords):
    return [
        keyword
        for keyword in (project_keywords or [])
        if isinstance(keyword, str) and keyword.strip()
    ]


def fetch_project_keywords_for_project(project_id, project_keyword_fetcher):
    project_keywords = []
    try:
        if project_id is not None:
            project_keywords = project_keyword_fetcher(int(project_id))
    except Exception:
        project_keywords = []
    return project_keywords


def extract_and_log_keywords(source_text, *, contextual_keyword_extractor, project_keywords=None):
    keywords = contextual_keyword_extractor(source_text)
    if project_keywords:
        keywords = _refine_extracted_keywords(keywords, project_keywords)
    log_keyword_extraction(keywords)
    return keywords
