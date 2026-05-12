"""Lazy RAG/vector service adapter accessors."""

from __future__ import annotations

from importlib import import_module


def get_vector_service():
    return import_module("services.vector_service").vector_service


def __getattr__(name):
    if name == "vector_service":
        return get_vector_service()
    raise AttributeError(name)


__all__ = ["get_vector_service", "vector_service"]
