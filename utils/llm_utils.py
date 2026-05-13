"""Compatibility wrapper for LLM parsing helpers."""

from __future__ import annotations

from reopsai.shared.llm import _safe_parse_json_object, parse_llm_json_response

__all__ = ["_safe_parse_json_object", "parse_llm_json_response"]
