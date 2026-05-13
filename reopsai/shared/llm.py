"""
LLM 응답 파싱 유틸리티.

screener.py 순환 참조 해결을 위해 app.py에서 분리됨.
"""
import json
import re
from typing import Optional


def parse_llm_json_response(raw_result):
    """LLM의 응답에서 JSON을 안전하게 파싱하는 함수"""
    if not raw_result or not raw_result.get('content'):
        raise ValueError('LLM 응답이 비어있습니다.')

    response_text = raw_result['content'].strip()

    response_text = re.sub(r'```(?:json|python)?\s*\n', '', response_text)
    response_text = re.sub(r'\n\s*```', '', response_text)
    response_text = response_text.strip()

    response_text = re.sub(r'^#+\s+.*$', '', response_text, flags=re.MULTILINE)
    response_text = response_text.strip()

    start_idx = response_text.find('{')
    if start_idx != -1:
        response_text = response_text[start_idx:]

    end_idx = response_text.rfind('}')
    if end_idx != -1:
        response_text = response_text[:end_idx + 1]

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"⚠️ JSON 파싱 1차 실패: {e}")

        try:
            fixed_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response_text)
            print(f"🔧 이스케이프 수정 시도...")
            return json.loads(fixed_text)
        except json.JSONDecodeError as e2:
            print(f"❌ JSON 파싱 2차 실패: {e2}")
            raise ValueError(f"LLM 응답 JSON 파싱 실패: {e}")


def _safe_parse_json_object(raw: object) -> Optional[dict]:
    """
    LLM 응답에서 JSON 객체를 안전하게 추출한다.
    - 기존 `parse_llm_json_response`는 {"content": "..."} 형태(dict)를 기대하므로,
      여기서는 str/dict 모두 처리하도록 래핑한다.
    """
    try:
        if isinstance(raw, dict):
            parsed = parse_llm_json_response(raw)
            return parsed if isinstance(parsed, dict) else None

        if not isinstance(raw, str):
            return None

        text = raw.strip()
        if not text:
            return None

        json_str = None
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text, re.DOTALL)
        if m:
            json_str = m.group(1)
        else:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = text[start:end + 1]

        if not json_str:
            return None

        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, dict) else None

    except Exception:
        return None


__all__ = ["_safe_parse_json_object", "parse_llm_json_response"]
