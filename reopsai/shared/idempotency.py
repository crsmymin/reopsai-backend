"""
멱등성(Idempotency) 캐시 관리 유틸리티.

동일 요청 ID에 대한 중복 처리를 방지합니다.
"""
import threading
import time
from typing import Dict

from flask import jsonify

IDEMPOTENCY_TTL_SECONDS = 300
_idempotency_cache: Dict[str, Dict[str, object]] = {}
_idempotency_lock = threading.Lock()


def _cleanup_idempotency_cache(now: float) -> None:
    with _idempotency_lock:
        expired_keys = [
            key for key, entry in _idempotency_cache.items()
            if entry.get('expires_at', 0) < now
        ]
        for key in expired_keys:
            _idempotency_cache.pop(key, None)


def _reserve_idempotency_entry(key: str):
    now = time.time()
    _cleanup_idempotency_cache(now)

    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if entry:
            return entry, False

        event = threading.Event()
        entry = {
            'event': event,
            'response': None,
            'status': None,
            'error': None,
            'created_at': now,
            'expires_at': now + IDEMPOTENCY_TTL_SECONDS,
        }
        _idempotency_cache[key] = entry
        return entry, True


def _complete_idempotency_entry(key: str, response_data: Dict[str, object], status: int = 200) -> None:
    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if not entry:
            return
        entry['response'] = response_data
        entry['status'] = status
        entry['error'] = None
        entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
        entry['event'].set()


def _fail_idempotency_entry(key: str, error_data: Dict[str, object], status: int = 500) -> None:
    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if not entry:
            return
        entry['error'] = error_data
        entry['status'] = status
        entry['response'] = None
        entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
        entry['event'].set()


def _respond_from_entry(entry):
    event = entry.get('event')
    if event and not event.is_set():
        event.wait(timeout=15)
    with _idempotency_lock:
        if entry.get('response') is not None:
            entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
            return jsonify(entry['response']), entry.get('status', 200)
        if entry.get('error') is not None:
            entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
            return jsonify(entry['error']), entry.get('status', 200)
    return jsonify({'success': False, 'error': '중복 요청이 아직 처리 중입니다. 잠시 후 다시 시도해주세요.'}), 409


__all__ = [
    "IDEMPOTENCY_TTL_SECONDS",
    "_cleanup_idempotency_cache",
    "_complete_idempotency_entry",
    "_fail_idempotency_entry",
    "_idempotency_cache",
    "_idempotency_lock",
    "_reserve_idempotency_entry",
    "_respond_from_entry",
]
