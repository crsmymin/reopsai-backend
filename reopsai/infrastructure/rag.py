"""Lazy RAG/vector service adapter accessors."""

from __future__ import annotations

def get_vector_service():
    from reopsai.infrastructure.vector import vector_service

    return vector_service


def __getattr__(name):
    if name == "vector_service":
        return get_vector_service()
    raise AttributeError(name)


__all__ = ["get_vector_service", "vector_service"]
