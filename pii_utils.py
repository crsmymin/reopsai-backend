"""
PII(개인정보) 마스킹/제거 유틸리티.

목표:
- 로그(Log)에는 PII를 가능한 한 남기지 않는다.
- LLM 전송 프롬프트에는 불필요한 PII를 마스킹 후 전달한다.

주의:
- 정규식 기반 탐지는 완벽하지 않습니다(오탐/미탐 가능).
- '주소'는 언어/형식이 다양해 완전 탐지가 어렵기 때문에 기본은 전화/이메일/주민등록번호 위주로 처리합니다.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Tuple


# --- Patterns (best-effort) ---

EMAIL_RE = re.compile(r"(?i)\b([a-z0-9._%+\-]+)@([a-z0-9.\-]+\.[a-z]{2,})\b")

# 한국 휴대폰/유선전화(대략) + 일반 숫자구분자 패턴 일부
PHONE_RE = re.compile(
    r"\b(01[016789]|0\d{1,2})[-.\s]?\d{3,4}[-.\s]?\d{4}\b"
)

# 주민등록번호: 6자리 + (- or space optional) + 7자리, 첫자(성별/세대) 1~4를 우선 허용
RRN_RE = re.compile(r"\b\d{6}[-\s]?[1-4]\d{6}\b")


SENSITIVE_KEY_EXACT = {
    "password",
    "passwd",
    "pwd",
    "pass",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "ssn",
    "rrn",
    "resident_registration_number",
    "주민등록번호",
}

SENSITIVE_KEY_CONTAINS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "authorization",
    "api_key",
)

EMAIL_KEYS = {"email", "e-mail", "mail"}
PHONE_KEYS = {"phone", "mobile", "tel", "contact", "연락처", "전화", "휴대폰", "핸드폰"}
NAME_KEYS = {"name", "username", "full_name", "성명", "이름"}
# 디지털 식별자: 결합 시 개인 식별 가능 → 로그에는 마스킹 (user_id는 내부 추적용으로 제외)
IDENTIFIER_KEYS = {"google_id", "device_id", "cookie_id", "ip", "ip_address", "sub"}


def _key_normalize(key: Any) -> str:
    try:
        return str(key).strip().lower()
    except Exception:
        return ""


def mask_email(email: str) -> str:
    """
    test@gmail.com -> t***@gmail.com
    a@b.com -> a***@b.com
    """
    m = EMAIL_RE.search(email or "")
    if not m:
        return email
    local, domain = m.group(1), m.group(2)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_phone(phone: str) -> str:
    """전화번호는 뒷 4자리만 남김 (best-effort)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "***"
    return f"***-****-{digits[-4:]}"


def mask_identifier(value: str, visible_prefix: int = 4) -> str:
    """디지털 식별자(google_id, sub 등) 로그용 마스킹. 앞 몇 자만 남김."""
    if not value or not isinstance(value, str):
        return "[REDACTED]"
    s = value.strip()
    if len(s) <= visible_prefix:
        return "***"
    return s[:visible_prefix] + "***"


def redact_text(text: str, *, mask_emails: bool = True, mask_phones: bool = True) -> Tuple[str, bool, Dict[str, int]]:
    """
    문자열에서 PII를 마스킹/제거한다.
    Returns: (sanitized_text, changed?, counts)
    """
    if not isinstance(text, str) or not text:
        return text, False, {"email": 0, "phone": 0, "rrn": 0}

    counts = {"email": 0, "phone": 0, "rrn": 0}
    changed = False
    out = text

    def _email_sub(match: re.Match) -> str:
        nonlocal changed
        counts["email"] += 1
        changed = True
        if mask_emails:
            return mask_email(match.group(0))
        return "[REDACTED_EMAIL]"

    def _phone_sub(match: re.Match) -> str:
        nonlocal changed
        counts["phone"] += 1
        changed = True
        if mask_phones:
            return mask_phone(match.group(0))
        return "[REDACTED_PHONE]"

    def _rrn_sub(match: re.Match) -> str:
        nonlocal changed
        counts["rrn"] += 1
        changed = True
        return "[REDACTED_RRN]"

    out = EMAIL_RE.sub(_email_sub, out)
    out = PHONE_RE.sub(_phone_sub, out)
    out = RRN_RE.sub(_rrn_sub, out)

    return out, changed, counts


def sanitize_for_log(obj: Any, *, max_string_len: int = 2000) -> Any:
    """
    로그 저장/출력용: dict/list를 재귀적으로 sanitize.
    - password/token 등 민감 키는 [REDACTED]
    - email/phone은 마스킹
    - 문자열 내부에 포함된 email/phone/rrn도 best-effort로 마스킹
    """
    if obj is None:
        return None

    if isinstance(obj, (int, float, bool)):
        return obj

    if isinstance(obj, bytes):
        return f"<bytes:{len(obj)}>"

    if isinstance(obj, str):
        s = obj
        if len(s) > max_string_len:
            s = s[:max_string_len] + "...(truncated)"
        s2, _, _ = redact_text(s, mask_emails=True, mask_phones=True)
        return s2

    if isinstance(obj, list):
        return [sanitize_for_log(v, max_string_len=max_string_len) for v in obj]

    if isinstance(obj, tuple):
        return tuple(sanitize_for_log(v, max_string_len=max_string_len) for v in obj)

    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            nk = _key_normalize(k)

            # 민감 키는 값 자체를 제거/치환
            if nk in SENSITIVE_KEY_EXACT or any(part in nk for part in SENSITIVE_KEY_CONTAINS):
                out[str(k)] = "[REDACTED]"
                continue

            # 이메일/전화/이름 키는 우선적으로 마스킹 처리
            if nk in EMAIL_KEYS and isinstance(v, str):
                out[str(k)] = mask_email(v)
                continue
            if nk in PHONE_KEYS and isinstance(v, str):
                out[str(k)] = mask_phone(v)
                continue
            # 디지털 식별자(google_id, sub 등): 로그에 전체 노출 방지
            if nk in IDENTIFIER_KEYS and isinstance(v, str):
                out[str(k)] = mask_identifier(v)
                continue

            # 그 외는 재귀/문자열 scrub
            out[str(k)] = sanitize_for_log(v, max_string_len=max_string_len)
        return out

    # 기타 타입은 string化 후 scrub
    try:
        s = str(obj)
    except Exception:
        return "<unserializable>"
    s2, _, _ = redact_text(s, mask_emails=True, mask_phones=True)
    return s2


def sanitize_prompt_for_llm(prompt: str) -> Tuple[str, bool, Dict[str, int]]:
    """
    LLM 전송용: 가능한 한 PII를 제거/마스킹.
    - 이메일/전화: 마스킹
    - 주민번호: 제거
    """
    # LLM에는 "원문"이 남는게 더 위험하므로 주민번호는 완전 치환, email/phone은 마스킹.
    return redact_text(prompt, mask_emails=True, mask_phones=True)


def detect_pii(text: str) -> Dict[str, bool]:
    """텍스트에 PII 패턴이 있는지 best-effort로 감지."""
    if not isinstance(text, str) or not text:
        return {"email": False, "phone": False, "rrn": False}
    return {
        "email": EMAIL_RE.search(text) is not None,
        "phone": PHONE_RE.search(text) is not None,
        "rrn": RRN_RE.search(text) is not None,
    }

