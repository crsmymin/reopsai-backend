"""Compatibility wrapper for the OpenAI service singleton."""

from __future__ import annotations

from reopsai.infrastructure.openai_service import OpenAIService, openai_service

__all__ = ["OpenAIService", "openai_service"]
