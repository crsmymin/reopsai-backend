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

    # 코드 블록 제거 (json, python 등 모든 코드 블록)
    # ```json ... ``` 또는 ```python ... ``` 같은 코드 블록 제거
    response_text = re.sub(r'```(?:json|python)?\s*\n', '', response_text)
    response_text = re.sub(r'\n\s*```', '', response_text)
    response_text = response_text.strip()

    # 마크다운 헤더 제거 (##, ### 등)
    response_text = re.sub(r'^#+\s+.*$', '', response_text, flags=re.MULTILINE)
    response_text = response_text.strip()

    # 첫 번째 { 찾아서 시작
    start_idx = response_text.find('{')
    if start_idx != -1:
        response_text = response_text[start_idx:]

    # 마지막 } 찾아서 끝내기
    end_idx = response_text.rfind('}')
    if end_idx != -1:
        response_text = response_text[:end_idx + 1]

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        # JSON 내 이스케이프 문자 문제 해결 시도
        print(f"⚠️ JSON 파싱 1차 실패: {e}")

        try:
            # 백슬래시 이스케이프 처리 (이미 이스케이프된 것은 제외)
            # \s, \d, \n 등 잘못된 이스케이프를 \\s, \\d, \\n으로 변경
            # 하지만 이미 올바른 \", \\, \/ 등은 그대로 유지
            fixed_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response_text)
            print(f"🔧 이스케이프 수정 시도...")
            return json.loads(fixed_text)
        except json.JSONDecodeError as e2:
            print(f"❌ JSON 파싱 2차 실패: {e2}")
            # 추가 디버깅 정보 포함하여 에러 메시지 개선
            raise ValueError(f"LLM 응답 JSON 파싱 실패: {e}")


def _safe_parse_json_object(raw: object) -> Optional[dict]:
    """
    LLM 응답에서 JSON 객체를 안전하게 추출한다.
    - 기존 `parse_llm_json_response`는 {"content": "..."} 형태(dict)를 기대하므로,
      여기서는 str/dict 모두 처리하도록 래핑한다.
    """
    try:
        # 1) 기존 호환: dict 형태면 기존 파서 사용
        if isinstance(raw, dict):
            parsed = parse_llm_json_response(raw)
            return parsed if isinstance(parsed, dict) else None

        # 2) 문자열이면 JSON 블록을 직접 추출/파싱
        if not isinstance(raw, str):
            return None

        text = raw.strip()
        if not text:
            return None

        # 코드 블록(JSON) 우선 추출
        json_str = None
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text, re.DOTALL)
        if m:
            json_str = m.group(1)
        else:
            # 첫 번째 { ... 마지막 }까지 (가장 보수적인 방식)
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
