"""Lazy evaluator service accessors."""

from __future__ import annotations

def run_evaluation(*args, **kwargs):
    from reopsai.infrastructure.dev_evaluator import run_evaluation as _run_evaluation

    return _run_evaluation(*args, **kwargs)


__all__ = ["run_evaluation"]
