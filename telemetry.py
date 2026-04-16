"""
Lightweight telemetry helpers (no prompt/output logging).

- Provides per-request trace context (thread-local).
- Helpers to log durations, token usage, and RAG query/results previews.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


_tls = threading.local()


@dataclass
class TraceContext:
    trace_id: str
    endpoint: str = ""


def set_trace(trace_id: str, endpoint: str = "") -> None:
    _tls.trace = TraceContext(trace_id=trace_id, endpoint=endpoint or "")


def clear_trace() -> None:
    if hasattr(_tls, "trace"):
        delattr(_tls, "trace")


def get_trace() -> Optional[TraceContext]:
    return getattr(_tls, "trace", None)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def now() -> float:
    return time.time()


def log_duration(label: str, duration_s: float, extra: Optional[str] = None) -> None:
    trace = get_trace()
    trace_part = f"[{trace.trace_id}]" if trace else ""
    endpoint_part = f" {trace.endpoint}" if trace and trace.endpoint else ""
    extra_part = f" | {extra}" if extra else ""
    print(f"\n⏱️ {_ts()} {trace_part}{endpoint_part} {label} = {duration_s:.3f}s{extra_part}")


def log_tokens(provider: str, usage: Optional[Dict[str, Any]] = None, extra: Optional[str] = None) -> None:
    """
    Logs token usage without printing prompt/output/model name.
    Expected usage keys (best-effort): prompt_tokens, completion_tokens, total_tokens.
    """
    usage = usage or {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    trace = get_trace()
    trace_part = f"[{trace.trace_id}]" if trace else ""
    endpoint_part = f" {trace.endpoint}" if trace and trace.endpoint else ""
    extra_part = f" | {extra}" if extra else ""

    print(
        f"\n🧾 {_ts()} {trace_part}{endpoint_part} TOKENS provider={provider}"
        f" prompt={prompt_tokens} completion={completion_tokens} total={total_tokens}{extra_part}"
    )


def log_rag(
    label: str,
    query_text: str,
    results: Any,
    duration_s: Optional[float] = None,
    preview_chars: int = 260,
) -> None:
    """
    Logs RAG query (preview) and results (preview) for visibility.
    """
    trace = get_trace()
    trace_part = f"[{trace.trace_id}]" if trace else ""
    endpoint_part = f" {trace.endpoint}" if trace and trace.endpoint else ""

    q = (query_text or "").replace("\n", " ").strip()
    if len(q) > preview_chars:
        q = q[:preview_chars] + "..."

    dur = f" duration={duration_s:.3f}s" if isinstance(duration_s, (int, float)) else ""
    print(f"\n🔎 {_ts()} {trace_part}{endpoint_part} RAG {label}{dur}")
    print(f"   query: {q}")

    # results can be {"principles": str, "examples": str} or a plain str
    if isinstance(results, dict):
        for key in ["principles", "examples"]:
            val = results.get(key)
            if isinstance(val, str):
                chunks = [c for c in val.split("\n\n") if c.strip()]
                print(f"   {key}: {len(val)} chars | {len(chunks)} chunks")
                for i, chunk in enumerate(chunks[:2]):
                    preview = chunk.replace("\n", " ").strip()
                    if len(preview) > 160:
                        preview = preview[:160] + "..."
                    print(f"     - {i+1}. {preview}")
            elif val is not None:
                print(f"   {key}: {type(val)}")
    elif isinstance(results, str):
        chunks = [c for c in results.split("\n\n") if c.strip()]
        print(f"   results: {len(results)} chars | {len(chunks)} chunks")
        for i, chunk in enumerate(chunks[:2]):
            preview = chunk.replace("\n", " ").strip()
            if len(preview) > 160:
                preview = preview[:160] + "..."
            print(f"     - {i+1}. {preview}")
    else:
        print(f"   results: {type(results)}")


def timed() -> Tuple[float, callable]:
    """
    Simple timer helper:
      start, stop = timed()
      ... do work ...
      duration = stop()
    """
    start = now()

    def stop() -> float:
        return now() - start

    return start, stop

