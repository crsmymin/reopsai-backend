"""Lazy LLM client adapter accessors."""

from __future__ import annotations

def get_openai_service():
    from reopsai.infrastructure.openai_service import openai_service

    return openai_service


def get_gemini_service():
    from reopsai.infrastructure.gemini_service import gemini_service

    return gemini_service


def __getattr__(name):
    if name == "openai_service":
        return get_openai_service()
    if name == "gemini_service":
        return get_gemini_service()
    raise AttributeError(name)


__all__ = ["get_openai_service", "get_gemini_service", "openai_service", "gemini_service"]
