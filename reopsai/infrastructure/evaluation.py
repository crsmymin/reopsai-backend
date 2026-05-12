"""Lazy evaluator service accessors."""

from __future__ import annotations

from importlib import import_module


def run_evaluation(*args, **kwargs):
    evaluator = import_module("services.dev_evaluator_service")
    return evaluator.run_evaluation(*args, **kwargs)


__all__ = ["run_evaluation"]
