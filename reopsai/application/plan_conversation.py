"""Conversation-study-maker helper policies and parsers."""

from __future__ import annotations

import json

from reopsai.shared.llm import _safe_parse_json_object


def build_conversation_recommendation_prompt(
    *,
    step_int,
    conversation_text,
    ledger_text,
    ledger_cards,
    previous_analysis,
    principles_context,
    examples_context,
):
    step_goal_map = {
        0: "[상황값 명확화] 이 단계에서는 리서치를 시작하게 된 배경과 상황을 명확히 하는 것이 목표입니다. 핵심 맥락(리스크/사용 맥락/검증할 화면·기능)을 먼저 파악하여 컨텍스트를 고해상도로 만들기. 사용자가 이 단계에서 '어떤 상황에서 어떤 문제를 해결하려는지'를 구체적으로 생각할 수 있도록 도와주세요.",
        1: "[목적값 명확화] 이 단계에서는 리서치의 목적, 연구 질문, 가설을 명확히 하는 것이 목표입니다. 목표/연구질문/가설 후보 카드를 많이 생성하여 사용자가 '이번 조사로 무엇을 결정하고 싶은지'를 구체적으로 생각할 수 있도록 도와주세요.",
        2: "[방법론값 명확화] 이 단계에서는 리서치 방법론과 세션 설계를 명확히 하는 것이 목표입니다. 이전 단계에서 선택한 목적/가설을 바탕으로 방법론/세션 설계 후보 카드를 생성하여 사용자가 '어떤 방법으로 조사할지'를 구체적으로 생각할 수 있도록 도와주세요.",
        3: "[대상값 명확화] 이 단계에서는 조사 대상과 스크리너 기준을 명확히 하는 것이 목표입니다. 대상/쿼터/스크리너(필수/제외) 후보 카드를 생성하여 사용자가 '누구를 대상으로 조사할지'를 구체적으로 생각할 수 있도록 도와주세요.",
        4: "[추가 요구사항 명확화] 이 단계에서는 지금까지 수집한 정보를 종합 분석하여, 리서치 설계를 더욱 구체화하기 위해 필요한 추가 요구사항을 판단하고 제안합니다. 예: UT/IDI의 경우 task/시나리오, 특정 기능/화면 집중 관찰 포인트, 편향 제거 고려사항, 추가 제약사항 등. 사용자가 '추가로 무엇을 고려해야 하는지'를 구체적으로 생각할 수 있도록 도와주세요.",
    }
    step_goal = step_goal_map.get(step_int, step_goal_map[0])
    context_summary = build_previous_context_summary(step_int, previous_analysis)

    schema_hint = {
        "draft_cards": [
            {
                "id": "string",
                "type": "project_context|research_goal|hypothesis|scope_item|audience_segment|quota_plan|screener_rule|methodology_set|task|analysis_plan|note",
                "title": "string",
                "content": "string",
                "because": "string",
                "fields": {},
                "tags": ["string"],
            }
        ],
        "next_question": {
            "title": "string",
            "content": "string",
            "because": "string",
        },
        "message": "string",
    }

    interrogation_rules = get_interrogation_rules(step_int)
    previous_summary = f"\n[이전 단계 결과 분석]\n{context_summary}\n" if context_summary else ""
    has_previous_selections = len(ledger_cards) > 0
    is_step_transition = not conversation_text.strip()
    transition_hint = build_transition_hint(step_int, has_previous_selections, is_step_transition)

    return f"""
당신은 시니어 UX 리서처입니다. 사용자가 선택해서 누적한 카드(LEDGER)와 대화 내용(CONVERSATION)을 근거로, 다음 단계를 더 뾰족하게 만들 후보 카드를 생성하세요.

**[역할 및 맥락]**
- 당신은 리서치 설계를 도와주는 AI 어시스턴트입니다.
- 모든 메시지는 리서치 설계자(서비스 사용자)에게 직접 말하는 형식으로 작성하세요.
- **next_question 생성 시 필수: LEDGER와 CONVERSATION을 구체적으로 분석하여 부족한 정보를 파악하고 질문하세요.**
- next_question의 because 필드는, 이 질문에 답변해주시면 어떤 도움이 되는지 자연스럽게 설명하세요.

[중요한 원칙]
- **⚠️ 매우 중요: 최근 사용자 입력에 최우선 집중하되, 적극적으로 추론하여 확장하세요**
  * CONVERSATION의 **가장 최근 사용자 입력**이 가장 중요합니다.
  * **추론 확장이 핵심**: 사용자가 명시적으로 말하지 않은 부분도 적극적으로 추론하여 카드로 생성하세요.
- **컨텍스트 종합 분석**: CONVERSATION 전체를 종합해서 핵심 키워드와 맥락을 파악하세요.
- **중복 방지 (완화된 기준)**: 완전히 동일한 내용의 카드는 생성하지 마세요. 하지만 새로운 관점이 있으면 새로운 카드로 생성하세요.
- 후보는 "과감하게" 구체적으로 제시하세요.
- 교과서 설명 금지. 일반론 금지. 추상적 표현 금지.
- 표( | ) 금지. 마크다운 불필요.
- 오직 JSON 하나만 출력하세요.

{previous_summary}

[현재 목표]
{step_goal}

{interrogation_rules}

{transition_hint}

[CONVERSATION - 지금까지의 대화 (⚠️ 최근 입력에 집중하세요)]
{conversation_text if conversation_text.strip() else "(새 단계 시작)"}

[LEDGER - 지금까지 선택된 카드들 (참고용)]
{ledger_text if ledger_text.strip() else "(선택된 카드 없음)"}

[참고 원칙]
{principles_context}

[참고 예시]
{examples_context}

[출력 스키마 예시]
{json.dumps(schema_hint, ensure_ascii=False, indent=2)}
"""


def build_conversation_final_plan_prompt(
    *,
    ledger_text,
    selected_methods,
    project_keywords,
    principles_context,
    examples_context,
):
    return f"""
당신은 15년차 시니어 UX 리서처입니다. 아래 '선택된 카드(LEDGER)'를 1차 근거로 삼아, 실무자가 바로 실행 가능한 **리서치 설계 프레임**을 작성하세요.

[이번 버전 범위]
- 스크리너 설문/가이드라인/상세 Task 설계는 포함하지 않습니다. (별도 기능에서 생성)
- 대신 "무엇을 검증/관찰할지"의 프레임(관찰 포인트, 성공 신호, 위험요인, 세션 구성)을 명확히 합니다.

[중요 규칙]
- LEDGER에 없는 사실을 '있는 것처럼' 만들지 마세요. 부족한 정보는 마지막에 '추가로 확인할 질문'으로 명시하세요.
- 교과서형 일반론 금지. 추상적 문장 금지. 이 프로젝트 맥락에 맞춰 구체화하세요.
- 표( | ) 금지.
- 인사/서론/확인 멘트 없이 바로 결과물로 시작.

[프로젝트 키워드]
{', '.join(project_keywords) if project_keywords else '(없음)'}

[사용자가 선택한 방법론(있다면)]
{', '.join(selected_methods) if selected_methods else '(미확정)'}

[선택된 카드(LEDGER)]
{ledger_text}

[참고 원칙]
{principles_context}

[참고 예시]
{examples_context}

[출력 형식]
# [프로젝트 명] 리서치 계획서

## 1. 리서치 배경
## 2. 리서치 목표 및 검증 가설
## 3. 검증 대상 및 범위
## 4. 대상자 설계
## 5. 리서치 방법 및 세션 시나리오
## 6. 주요 지표
## 7. 관찰 및 분석 프레임
## 8. 예상 산출물
"""


def build_previous_context_summary(step_int, previous_analysis):
    context_summary = ""
    if step_int == 2:
        if previous_analysis["selected_goals"]:
            goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:3]])
            context_summary += f"이미 설정된 목적: {goals_text}\n"
        if previous_analysis["selected_methodologies"]:
            methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
            context_summary += f"⚠️ 이미 선택된 방법론이 있습니다: {methods_text}\n"
            context_summary += "이 경우, 선택된 방법론의 세부 설계나 추가 방법론 제안에 집중하세요.\n"
    elif step_int == 3:
        if previous_analysis["selected_methodologies"]:
            methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
            context_summary += f"선택된 방법론: {methods_text}\n"
            has_ut = any("ut" in m["title"].lower() or "usability" in m["title"].lower() or "사용성" in m["title"].lower()
                         for m in previous_analysis["selected_methodologies"])
            has_interview = any("interview" in m["title"].lower() or "인터뷰" in m["title"].lower()
                                for m in previous_analysis["selected_methodologies"])
            if has_ut:
                context_summary += "UT의 경우: 경험 유무, 사용 빈도, 숙련도가 중요한 기준입니다.\n"
            if has_interview:
                context_summary += "인터뷰의 경우: 페르소나, 세그먼트, 행동 패턴이 중요한 기준입니다.\n"
        if previous_analysis["selected_goals"]:
            goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:2]])
            context_summary += f"설정된 목적: {goals_text}\n"
    elif step_int == 4:
        context_summary += "이 단계는 지금까지 수집한 정보를 바탕으로, 리서치 설계를 더욱 구체화하기 위해 필요한 추가 요구사항을 수집하는 단계입니다.\n"
        if previous_analysis["selected_methodologies"]:
            methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
            context_summary += f"선택된 방법론: {methods_text}\n"
            has_ut = any("ut" in m["title"].lower() or "usability" in m["title"].lower() or "사용성" in m["title"].lower()
                         for m in previous_analysis["selected_methodologies"])
            if has_ut:
                context_summary += "→ UT/IDI 방법론이 선택되었으므로, task/시나리오나 관찰 포인트가 필요할 수 있습니다.\n"
        if previous_analysis["selected_goals"]:
            goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:2]])
            context_summary += f"설정된 목적: {goals_text}\n"
        if previous_analysis["selected_audiences"]:
            audiences_text = ", ".join([a["title"] for a in previous_analysis["selected_audiences"][:2]])
            context_summary += f"설정된 대상: {audiences_text}\n"
        context_summary += "→ 현재 설계에서 보완이 필요한 부분(예: 특정 기능/화면 집중, 편향 제거, 추가 제약사항 등)을 판단하여 질문하고 카드를 생성하세요.\n"
        context_summary += "→ **중요: Step4(추가 요구사항)는 '추론'이 핵심입니다. LEDGER에 없는 사실을 단정하지는 말되, 부족한 정보/리스크를 추론해 카드로 제안하세요. (카드 0개 금지)\n"
    return context_summary


def build_transition_hint(step_int, has_previous_selections, is_step_transition):
    if not is_step_transition:
        return ""
    if has_previous_selections:
        step_purpose_map = {
            0: "리서치를 시작하게 된 배경이나 상황",
            1: "리서치의 목적, 연구 질문, 또는 가설",
            2: "리서치의 방법론이나 설계에 대한 정보",
            3: "조사 대상이나 스크리너 기준",
            4: "리서치 설계를 더욱 구체화하기 위해 필요한 추가 정보",
        }
        step_purpose = step_purpose_map.get(step_int, "이번 단계의 정보")
        return f"""
[단계 전환 모드 - 이전 단계 선택 있음]
- 사용자가 이전 단계에서 선택한 카드들을 기반으로 이번 단계를 시작합니다.
- **중요**: 프론트엔드에서 이미 기본 프롬프트가 표시되므로, message 필드는 선택사항입니다.
- message 필드를 생성하는 경우: 이전 단계 선택을 구체적으로 언급하고, 이번 단계({step_purpose})로 자연스럽게 이어지도록 하세요.
- message 필드를 생성하지 않아도 됩니다 (기본 프롬프트가 이미 표시되므로).
"""
    return """
[단계 전환 모드 - 새 단계 시작]
- 사용자가 이전 단계에서 아직 카드를 선택하지 않았습니다.
- 이번 단계의 기본 프롬프트를 따르고, 이전 단계 선택을 언급하지 마세요.
- message 필드에서 이번 단계의 목적과 필요성을 자연스럽게 안내하세요.
"""


def get_interrogation_rules(step_int: int) -> str:
    if step_int == 0:
        return """
[Step0 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 5개, 최대 10개를 반드시 생성하세요.
- **절대 규칙 - 카드 타입 제한**: Step 0에서는 **오직 project_context와 scope_item 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    if step_int == 1:
        return """
[Step1 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 7개, 최대 10개 생성하세요. (0개는 금지)
- **카드 구성 강제**: research_goal 타입 최소 3개, hypothesis 타입 최소 4개
- **절대 규칙 - 카드 타입 제한**: Step 1에서는 **오직 research_goal과 hypothesis 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    if step_int == 2:
        return """
[Step2 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 2개, 최대 5개 생성하세요. (0개는 금지)
- **절대 규칙 - 카드 타입 제한**: Step 2에서는 **오직 methodology_set 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    if step_int == 3:
        return """
[Step3 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 6개, 최대 10개 생성하세요. (0개는 금지)
- **카드 구성 강제**: audience_segment 최소 2개, quota_plan 최소 2개, screener_rule 최소 2개
- **절대 규칙 - 카드 타입 제한**: Step 3에서는 **오직 audience_segment, quota_plan, screener_rule 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    if step_int == 4:
        return """
[Step4 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 **최소 3개, 최대 8개** 생성하세요. (0개 금지)
- **카드 타입**: task, analysis_plan, scope_item, note 타입을 사용 가능합니다.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    return ""


def allowed_card_types(step):
    if step == 0:
        return ["project_context", "scope_item"]
    if step == 1:
        return ["research_goal", "hypothesis"]
    if step == 2:
        return ["methodology_set"]
    if step == 3:
        return ["audience_segment", "quota_plan", "screener_rule"]
    if step == 4:
        return ["task", "analysis_plan", "scope_item", "note"]
    return []


def parse_conversation_recommendation_payload(*, raw_content, step_int, mode):
    parsed = _safe_parse_json_object(raw_content) or {}
    draft_cards = parsed.get("draft_cards", [])
    next_question = parsed.get("next_question")
    missing_questions = parsed.get("missing_questions", [])
    message = parsed.get("message", "추천을 생성했습니다. 필요한 카드만 선택해 누적하세요.")

    extracted_question = None
    allowed_types = allowed_card_types(step_int)
    if isinstance(draft_cards, list):
        filtered_cards = []
        for card in draft_cards:
            if not isinstance(card, dict):
                continue
            card_type = str(card.get("type") or "").lower()
            card_title = str(card.get("title") or "").strip()
            card_content = str(card.get("content") or "").strip()

            is_question_like = ("question" in card_type) or card_title.endswith("?") or card_content.endswith("?")
            if is_question_like and extracted_question is None:
                extracted_question = {
                    "title": card_title or "추가 질문",
                    "content": card_content or card_title,
                    "because": str(card.get("because") or "").strip(),
                }
                continue
            if is_question_like:
                continue

            if step_int < 4:
                type_matches = any(allowed_type in card_type for allowed_type in allowed_types)
                if not type_matches:
                    continue
            filtered_cards.append(card)
        draft_cards = filtered_cards

    if not isinstance(next_question, dict):
        if isinstance(missing_questions, list) and len(missing_questions) > 0:
            first_question = missing_questions[0] if isinstance(missing_questions[0], dict) else {}
            next_question = {
                "title": (first_question.get("title") or "추가 질문"),
                "content": (first_question.get("content") or first_question.get("title") or ""),
                "because": "",
            }
        elif extracted_question is not None:
            next_question = extracted_question
        else:
            next_question = None

    return {
        "success": True,
        "draft_cards": draft_cards if isinstance(draft_cards, list) else [],
        "missing_questions": [],
        "next_question": next_question,
        "message": message,
        "step": step_int,
        "mode": mode,
    }
