"""Lazy LLM client adapter accessors."""

from __future__ import annotations

from importlib import import_module


def get_openai_service():
    return import_module("services.openai_service").openai_service


def get_gemini_service():
    return import_module("services.gemini_service").gemini_service


def __getattr__(name):
    if name == "openai_service":
        return get_openai_service()
    if name == "gemini_service":
        return get_gemini_service()
    raise AttributeError(name)


__all__ = ["get_openai_service", "get_gemini_service", "openai_service", "gemini_service"]
