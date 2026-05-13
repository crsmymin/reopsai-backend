"""Pure context helpers for plan generation flows."""

from __future__ import annotations

from typing import List, Set


def analyze_previous_step_selections(ledger_cards, step_int):
    analysis = {
        "selected_methodologies": [],
        "selected_goals": [],
        "selected_audiences": [],
        "selected_context": [],
    }

    for card in ledger_cards:
        if not isinstance(card, dict):
            continue
        card_type = str(card.get("type", "")).lower()
        title = str(card.get("title", "")).strip()
        content = str(card.get("content", "")).strip()

        if "methodology" in card_type:
            analysis["selected_methodologies"].append({"title": title, "content": content})
        elif "goal" in card_type or "hypothesis" in card_type or "question" in card_type:
            analysis["selected_goals"].append({"title": title, "content": content})
        elif "audience" in card_type or "quota" in card_type or "screener" in card_type:
            analysis["selected_audiences"].append({"title": title, "content": content})
        elif "context" in card_type or "project_context" in card_type:
            analysis["selected_context"].append({"title": title, "content": content})

    return analysis


def ledger_cards_to_context_text(ledger_cards: object, max_chars: int = 12000) -> str:
    if not isinstance(ledger_cards, list):
        return ""

    chunks: List[str] = []
    for idx, card in enumerate(ledger_cards):
        if not isinstance(card, dict):
            continue
        status = str(card.get("status", "") or "").strip()
        card_type = str(card.get("type", "") or "note").strip()
        title = str(card.get("title", "") or "").strip()
        content = str(card.get("content", "") or "").strip()
        because = str(card.get("because", "") or "").strip()

        if not (title or content):
            continue

        chunk = f"""[CARD {idx + 1}]
type: {card_type}
status: {status or "unknown"}
title: {title or "(no title)"}
content:
{content}
"""
        if because:
            chunk += f"because: {because}\n"
        chunks.append(chunk)

    text = "\n\n".join(chunks).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[TRUNCATED]"
    return text


def extract_selected_methodologies_from_ledger(ledger_cards: object) -> List[str]:
    if not isinstance(ledger_cards, list):
        return []

    methods: List[str] = []
    for card in ledger_cards:
        if not isinstance(card, dict):
            continue
        if str(card.get("type", "")).strip() != "methodology_set":
            continue
        fields = card.get("fields") if isinstance(card.get("fields"), dict) else {}
        raw_methods = fields.get("methods")
        if isinstance(raw_methods, list):
            for method in raw_methods:
                if isinstance(method, str) and method.strip():
                    methods.append(method.strip())

    seen: Set[str] = set()
    out: List[str] = []
    for method in methods:
        if method.lower() in seen:
            continue
        seen.add(method.lower())
        out.append(method)
    return out
