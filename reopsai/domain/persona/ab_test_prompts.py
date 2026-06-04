from __future__ import annotations

import json


AB_TEST_PROMPT_VERSION = "ab_test_v4"
AB_SCREEN_ANALYSIS_STAGE = "STAGE: ab_screen_analysis"
AB_PERSONA_PREFERENCE_STAGE = "STAGE: ab_persona_preference"


def _read_string(value):
    return value.strip() if isinstance(value, str) else ""


def _persona_response_guidance(*, is_flow: bool = False):
    return "\n".join(
        [
            "[응답 방향성]",
            "- 퍼소나 원문과 interview pack에 없는 과거 경험, 가족관계, 직업, 선호를 새로 만들지 마세요.",
            "- 퍼소나의 성격/행동/선호/동기/사회적 맥락과 pack의 decision_rules, ux_context_clues를 판단 렌즈로 쓰세요.",
            "- 화면 안 더미 이름/계정 정보는 샘플로 간주하고, 이 퍼소나 본인이 사용하는 상황이라고 가정하세요.",
            "- 1인칭 경험 회고처럼 쓰되, 간결하게 최대 2문장까지 말하세요.",
            "- 단순 선호 선언으로 끝내지 말고, 이 퍼소나에게 중요한 기준 1개와 연결하세요.",
            "- 사용자에게 보이는 모든 문장은 해요체 또는 합니다/했습니다체로 자연스럽게 작성하세요.",
            "- '~하다', '~함', '~됨' 같은 보고서식/명사형 종결은 쓰지 마세요.",
        ]
    )


def _persona_lens_guidance():
    return "\n".join(
        [
            "[퍼소나 렌즈 규칙]",
            "- interview pack의 decision_rules, needs_and_painpoints, ux_context_clues, communication_style을 우선 참고하세요.",
            "- 응답 전에 이 퍼소나의 주 렌즈 1개를 내부적으로 정하세요. 예: 가격 민감도, 신뢰/개인정보, 시간 절약.",
            "- A/B 비교는 한 문장 안에서 두 버전을 직접 대조하세요. 예: 'A안 대비 B안은 ...'.",
            "- inventory에 적힌 문장을 그대로 반복하지 말고, 이 퍼소나 관점의 해석만 쓰세요.",
            "- generic UX 평가가 아니라, 이 퍼소나에게 특히 중요한 기준과 연결해 이유를 쓰세요.",
        ]
    )


def _ab_evaluation_criteria(*, is_flow: bool):
    return "\n".join(
        [
            "[평가 기준]",
            "- inventory에 없는 UI 사실은 만들지 마세요. 좋다/나쁘다/과부하/신뢰 등의 판단은 퍼소나 렌즈로만 내리세요.",
            "- 플로우 비교는 화면 간 맥락, 다음 액션 연결성, 목표 달성 용이성을 함께 판단하세요."
            if is_flow
            else "- 단일 화면 비교는 정보 구조, CTA, 이 퍼소나에게 중요한 단서 차이를 중심으로 판단하세요.",
        ]
    )


def _ab_screen_inventory_json_contract(*, is_flow: bool):
    step_item = (
        '{ "stepIndex": 0, "name": "화면명", '
        '"visibleHeadings": ["보이는 제목/섹션"], '
        '"ctaLabels": ["버튼/링크 문구 그대로"], '
        '"uiElements": ["카드 4개", "2열 그리드", "하단 탭바"], '
        '"textBlocks": ["눈에 띄는 본문/라벨 문구"], '
        '"structureNotes": ["상단 고정 헤더", "세로 스크롤 2~3屏"] }'
    )
    lines = [
        "[출력 JSON 계약]",
        "반드시 한국어 JSON만 반환하세요.",
        "- 해석, 평가, 선호, winner, 추천, 좋음/나쁨/과부하/신뢰/피로감 같은 판단어는 금지합니다.",
        "- 보이는 텍스트, 요소 개수, 배치, 구조만 사실적으로 적으세요.",
        "{",
        '  "mode": "single" | "flow",',
        '  "elementDifferences": ["A: ... / B: ... 형태의 대칭 fact bullet"],',
        '  "variants": {',
        '    "A": [' + step_item + "],",
        '    "B": [' + step_item + "]",
        "  },",
    ]
    if is_flow:
        lines.extend(
            [
                '  "stepElementDifferences": [{ "stepIndex": 0, "facts": ["A: ...", "B: ..."] }],',
            ]
        )
    lines.append("}")
    return "\n".join(lines)


def _ab_persona_preference_json_contract(*, is_flow: bool):
    lines = [
        "[출력 JSON 계약]",
        "반드시 한국어 JSON만 반환하세요.",
        "{",
        '  "scores": {',
        '    "winner": "A" | "B" | "tie",',
        '    "reasonForChoice": "2문장 이내",',
    ]
    if is_flow:
        lines.extend(
            [
                '    "journeyComparison": {',
                '      "flowARating": 0, "flowBRating": 0,',
                '      "goalAchievementEase": { "flowA": 0, "flowB": 0 },',
                '      "navigationConfidence": { "flowA": 0, "flowB": 0 },',
                '      "estimatedCompletionSpeed": "A" | "B" | "same",',
                '      "criticalDropoffStep": { "flowA": null, "flowB": null }',
                "    },",
                '    "stepAnalysis": [{ "stepIndex": 0, "preferredVersion": "A"|"B"|"tie", "reason": "1~2문장" }]',
            ]
        )
    lines.extend(
        [
            "  },",
            '  "feedback": ["A vs B 직접 비교 1문장", "추가 비교 1문장(선택)"]',
            "}",
        ]
    )
    return "\n".join(lines)


def build_ab_screen_analysis_prompt(
    *,
    test_name: str,
    purpose: str | None,
    service_context: str | None,
    mode: str,
    device_type: str | None,
    flow_purpose: str | None,
    screens_a: list[dict],
    screens_b: list[dict],
) -> str:
    is_flow = mode == "flow"
    screen_meta = {
        "versionA": [{"stepIndex": index, **screen} for index, screen in enumerate(screens_a)],
        "versionB": [{"stepIndex": index, **screen} for index, screen in enumerate(screens_b)],
    }
    return "\n".join(
        [
            AB_SCREEN_ANALYSIS_STAGE,
            f"프롬프트 버전: {AB_TEST_PROMPT_VERSION}",
            "당신은 UI inventory 작성자입니다. A/B 화면에 보이는 요소만 사실적으로 나열하세요.",
            "UX 평가, 사용성 해석, 선호, winner, 추천은 금지합니다.",
            "",
            f"테스트명: {test_name}",
            f"테스트 목적: {_read_string(purpose) or '(없음)'}",
            f"서비스 맥락: {_read_string(service_context) or '(없음)'}",
            f"모드: {'flow' if is_flow else 'single'}",
            f"디바이스: {_read_string(device_type) or '(미지정)'}",
            f"플로우 목표: {_read_string(flow_purpose) or '(없음)'}" if is_flow else "",
            "",
            "[지시]",
            "- 첨부된 Version A/B 이미지를 순서대로 보고, 각 화면의 제목, CTA 문구, 카드/버튼/탭/배너 등 요소를 inventory로 적으세요.",
            "- elementDifferences는 'A: ... / B: ...' 형태의 대칭 fact bullet만 쓰세요. 어느 쪽이 더 낫다는 표현은 금지합니다.",
            "- flow 모드면 stepElementDifferences에 stepIndex별 fact만 적으세요.",
            "- 화면에 보이는 텍스트/요소만 근거로 쓰고, 보이지 않는 기능은 추측하지 마세요.",
            "",
            "[화면 메타]",
            json.dumps(screen_meta, ensure_ascii=False),
            "",
            _ab_screen_inventory_json_contract(is_flow=is_flow),
        ]
    )


def build_ab_persona_preference_prompt(
    *,
    test_name: str,
    purpose: str | None,
    service_context: str | None,
    mode: str,
    device_type: str | None,
    flow_purpose: str | None,
    variant_brief: dict,
    persona_context: str,
) -> str:
    is_flow = mode == "flow"
    return "\n".join(
        [
            AB_PERSONA_PREFERENCE_STAGE,
            f"프롬프트 버전: {AB_TEST_PROMPT_VERSION}",
            "당신은 주어진 퍼소나입니다. 아래 UI inventory를 참고해 A/B 중 더 선호하는 버전을 1인칭으로 판단하세요.",
            "inventory는 화면에 무엇이 있는지 확인하는 용도입니다. inventory 문장을 그대로 반복하지 마세요.",
            "이미지는 제공되지 않습니다. inventory에 없는 UI 사실은 새로 만들지 마세요.",
            "",
            f"테스트명: {test_name}",
            f"테스트 목적: {_read_string(purpose) or '(없음)'}",
            f"서비스 맥락: {_read_string(service_context) or '(없음)'}",
            f"모드: {'flow' if is_flow else 'single'}",
            f"디바이스: {_read_string(device_type) or '(미지정)'}",
            f"플로우 목표: {_read_string(flow_purpose) or '(없음)'}" if is_flow else "",
            "",
            _persona_response_guidance(is_flow=is_flow),
            _persona_lens_guidance(),
            _ab_evaluation_criteria(is_flow=is_flow),
            "",
            "[UI inventory]",
            json.dumps(variant_brief, ensure_ascii=False),
            "",
            "[퍼소나]",
            persona_context,
            "",
            "[지시]",
            "- 더 선호하는 버전을 A/B/tie 중 하나로 선택하세요.",
            "- reasonForChoice는 2문장 이내로 작성하세요. 한 문장으로 너무 짧게 끝내지 마세요.",
            "- feedback는 1~2개 작성하세요. 각 항목은 1문장, A vs B 직접 비교. reasonForChoice와 같은 내용을 반복하지 마세요.",
            "- 12명 규모를 고려해 3문장 이상은 쓰지 마세요.",
            "",
            _ab_persona_preference_json_contract(is_flow=is_flow),
        ]
    )
