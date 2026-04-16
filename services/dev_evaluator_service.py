"""
개발용 산출물 평가 서비스 (범용 판때기).
- artifact_type: plan | survey | guideline | report (향후 확장)
- stage: 1|2|3|4|5|"final"
- criteria: 항목(id, name, max_score, sub_items) → 하위항목 Pass/Fail, 항목 점수, 최종 점수
"""
import json
import re
from typing import Any, Dict, List, Optional

from services.gemini_service import gemini_service


def _extract_evaluation_text(artifact_type: str, stage, payload: Dict[str, Any]) -> str:
    """payload에서 평가할 본문 텍스트를 추출한다."""
    if artifact_type == "plan":
        if stage == "final":
            content = (payload.get("content") or "").strip()
            if not content and payload.get("artifact_id"):
                # artifact_id만 오면 호출측에서 content를 채워줘야 함 (라우트에서 처리)
                return ""
            return content
        # stage 1~5: UI에 보이는 카드 목록 그대로 붙여넣은 content 우선, 없으면 messages_text + ledger_cards
        pasted = (payload.get("content") or "").strip()
        if pasted:
            return pasted
        messages_text = (payload.get("messages_text") or "").strip()
        ledger_cards = payload.get("ledger_cards")
        if isinstance(ledger_cards, list):
            ledger_text = _ledger_cards_to_text(ledger_cards)
        else:
            ledger_text = ""
        return f"[대화 내용]\n{messages_text}\n\n[선택 누적 카드]\n{ledger_text}".strip()
    # 향후 survey, guideline, report 등
    return (payload.get("content") or "").strip()


def _ledger_cards_to_text(ledger_cards: List[Dict], max_chars: int = 12000) -> str:
    """ledger 카드 리스트를 평가용 텍스트로 직렬화."""
    if not ledger_cards:
        return ""
    chunks = []
    for i, card in enumerate(ledger_cards):
        if not isinstance(card, dict):
            continue
        title = (card.get("title") or "").strip()
        content = (card.get("content") or "").strip()
        if not title and not content:
            continue
        card_type = (card.get("type") or "note").strip()
        chunks.append(f"[카드 {i+1}] type={card_type}\ntitle: {title or '(없음)'}\ncontent:\n{content}")
    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[TRUNCATED]"
    return text


def _build_evaluation_prompt(
    evaluation_text: str,
    artifact_type: str,
    stage,
    criteria: List[Dict],
    evaluation_mode: Optional[str] = None,
) -> str:
    """LLM에 보낼 평가 프롬프트를 만든다."""
    stage_label = str(stage) if stage != "final" else "최종"
    criteria_block = []
    for c in criteria:
        c_id = c.get("id") or ""
        c_name = c.get("name") or ""
        max_score = c.get("max_score", 0)
        sub_items = c.get("sub_items") or []
        lines = [f"- 항목 id: {c_id}", f"  이름: {c_name} (만점: {max_score}점)"]
        for s in sub_items:
            s_id = s.get("id") or ""
            s_name = (s.get("name") or s.get("description") or "").strip()
            lines.append(f"  - 하위 id: {s_id} | {s_name}")
        criteria_block.append("\n".join(lines))

    if (evaluation_mode or "").strip().lower() == "card_appropriateness":
        intro = f"""당신은 UX 리서치 계획 수립 단계에서 **생성·노출된 후보 카드 목록**을 평가하는 전문가입니다.

아래 [평가 대상]은 대화형 계획 수립의 {stage_label}단계에서 사용자에게 노출된 **후보 카드**들입니다. 형식은 UI 그대로 붙여넣은 것일 수 있습니다(예: "후보 항목이 생성됐어요. 필요한 것만 선택해 주세요.", 카드 제목, 타입(project_context/scope_item 등), 본문, "선택하기" 등). 또는 JSON 형태의 ledger_cards일 수 있습니다.

**이 후보 카드들이 해당 단계(상황/컨텍스트·목적·방법론 등) 목적에 부합하는지, 사용자가 "필요한 것만 선택"할 수 있도록 적절히 제안되었는지**를 판단하고, 주어진 평가 기준의 각 하위항목에 대해 Pass/Fail, 항목별 점수를 부여하세요. 입력이 UI 복사본이어도 그 안에 담긴 카드 제목·타입·내용을 기준으로 평가하면 됩니다."""
    else:
        intro = """당신은 UX 리서치 산출물 품질을 평가하는 전문가입니다.
아래 [평가 대상 텍스트]를 읽고, 주어진 평가 기준의 각 **하위항목**에 대해 Pass/Fail을 판정하고, 각 **항목**에 대해 만점 대비 점수를 부여하세요."""

    if (evaluation_mode or "").strip().lower() == "card_appropriateness":
        target_heading = "## 평가 대상 (후보 카드 목록 — UI 그대로 붙여넣었거나 ledger_cards JSON)"
    else:
        target_heading = "## 평가 대상 텍스트"

    return f"""{intro}

## 평가 대상
- 산출물 유형: {artifact_type}
- 단계: {stage_label}

## 평가 기준 (항목 및 하위항목)
{chr(10).join(criteria_block)}

{target_heading}
---
{evaluation_text[:30000]}
---

## 출력 형식 (반드시 아래 JSON만 출력, 다른 설명 없이)
{{
  "criteria": [
    {{
      "id": "항목id",
      "score": 0,
      "sub_items": [
        {{ "id": "하위id", "pass_fail": true, "reason": "Fail인 경우에만 한 줄 이유(한국어, 선택)" }}
      ]
    }}
  ]
}}

- sub_items의 pass_fail: true=Pass, false=Fail
- sub_items의 reason: pass_fail이 false일 때 해당 하위항목이 왜 충족되지 않았는지 한 줄로 작성 (한국어). Pass면 비우거나 생략 가능.
- score: 해당 항목의 점수 (만점 이하 정수)
- 모든 항목 id, 하위 id는 위 평가 기준에 나온 id와 동일하게 사용하세요.
- JSON만 출력하세요."""


def _parse_llm_evaluation_response(content: str) -> Optional[Dict]:
    """LLM 응답 텍스트에서 JSON을 추출한다."""
    if not (content or isinstance(content, str)):
        return None
    text = content.strip()
    text = re.sub(r"```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n\s*```", "", text)
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return None
    end = text.rfind("}") + 1
    if end <= 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def run_evaluation(
    artifact_type: str,
    stage,
    payload: Dict[str, Any],
    criteria: List[Dict],
    evaluation_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    평가 실행.
    Returns:
        {
          "success": bool,
          "error": str | None,
          "final_score": number,
          "criteria": [ { "id", "score", "sub_items": [ { "id", "pass_fail" } ] } ],
          "raw_response": str (optional, for debug)
        }
    """
    evaluation_text = _extract_evaluation_text(artifact_type, stage, payload)
    if not evaluation_text and (stage != "final" or not payload.get("artifact_id")):
        return {
            "success": False,
            "error": "평가할 내용이 비어 있습니다. content 또는 (messages_text + ledger_cards) 또는 artifact_id를 넣어주세요.",
            "final_score": 0,
            "criteria": [],
        }

    if not criteria:
        return {
            "success": False,
            "error": "평가 기준(criteria)이 비어 있습니다.",
            "final_score": 0,
            "criteria": [],
        }

    prompt = _build_evaluation_prompt(
        evaluation_text, artifact_type, stage, criteria, evaluation_mode
    )
    result = gemini_service.generate_response(
        prompt,
        generation_config={"temperature": 0.2},
    )

    if not result.get("success") or not result.get("content"):
        return {
            "success": False,
            "error": result.get("error") or "LLM 평가 호출 실패",
            "final_score": 0,
            "criteria": [],
        }

    parsed = _parse_llm_evaluation_response(result["content"])
    if not parsed or "criteria" not in parsed:
        return {
            "success": False,
            "error": "LLM 응답에서 평가 결과 JSON을 파싱할 수 없습니다.",
            "final_score": 0,
            "criteria": [],
            "raw_response": result.get("content", "")[:2000],
        }

    criteria_result = parsed.get("criteria") or []
    final_score = 0
    for c in criteria_result:
        final_score += int(c.get("score") or 0)

    return {
        "success": True,
        "error": None,
        "final_score": final_score,
        "criteria": criteria_result,
    }
