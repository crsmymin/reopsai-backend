"""Usage metering helpers exposed through the shared package."""

from __future__ import annotations

from importlib import import_module


_usage_metering = import_module("utils.usage_metering")

build_llm_usage_context = _usage_metering.build_llm_usage_context
classify_feature_key = _usage_metering.classify_feature_key
ensure_company_initial_grant = _usage_metering.ensure_company_initial_grant
get_llm_usage_context = _usage_metering.get_llm_usage_context
is_company_quota_exceeded = _usage_metering.is_company_quota_exceeded
run_with_llm_usage_context = _usage_metering.run_with_llm_usage_context
stream_with_llm_usage_context = _usage_metering.stream_with_llm_usage_context

__all__ = [
    "build_llm_usage_context",
    "classify_feature_key",
    "ensure_company_initial_grant",
    "get_llm_usage_context",
    "is_company_quota_exceeded",
    "run_with_llm_usage_context",
    "stream_with_llm_usage_context",
]
