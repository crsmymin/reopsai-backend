from __future__ import annotations

import base64
import concurrent.futures
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import mimetypes
import re
import threading
import time
import os
from types import SimpleNamespace
from typing import Any, Mapping, Optional

from reopsai.domain.persona.generation import (
    PersonaGenerationQualityError,
    build_curated_interview_evidence_bundle,
    generate_segment_suggestions_pipeline,
    generate_personas_pipeline,
    infer_persona_source_type,
    resolve_persona_generation_max_concurrency,
    validate_generation_payload,
    validate_segment_suggestion_payload,
)
from reopsai.domain.persona.interview_evidence import (
    TELECOM_EVIDENCE_VARIABLES,
    chunk_to_payload,
    chunk_vector_id,
    empty_curated_evidence_bundle,
    format_evidence_for_prompt,
    normalize_chunk_row_data,
    search_interview_evidence_chunks,
    upsert_interview_source_embeddings,
)
from reopsai.domain.persona.ui_test_prompts import build_ui_chunk_prompt, build_ui_scoring_prompt, build_ui_summary_prompt, build_ui_test_prompt
from reopsai.infrastructure.persistence.engine import session_scope
from reopsai.infrastructure.persistence.repositories.persona_repository import PersonaRepository
from reopsai.infrastructure.persona_capture import persona_capture
from reopsai.infrastructure.persona_figma_client import PersonaFigmaClientError, make_oauth_state, persona_figma_client
from reopsai.infrastructure.persona_image_generation import generate_persona_image_data_url
from reopsai.infrastructure.persona_storage import persona_storage
from reopsai.shared.usage_metering import build_llm_usage_context, run_with_llm_usage_context


@dataclass(frozen=True)
class PersonaServiceResult:
    status: str
    data: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None
    status_code: int = 200


class PersonaUrlCaptureError(RuntimeError):
    pass


PERSONA_INTERVIEW_PACK_VERSION = "persona_interview_pack_v2"
DEFAULT_PERSONA_PACK_MODEL = "gemini-2.5-flash"
DEFAULT_PERSONA_INTERVIEW_MODEL = "gpt-5.4"
DEFAULT_PERSONA_UI_TEST_SCORING_MODEL = "gpt-5.4-mini"
DEFAULT_INTERVIEW_MAX_CONCURRENCY = 4
DEFAULT_INTERVIEW_RETRY_ATTEMPTS = 2
DEFAULT_UI_TEST_MAX_CONCURRENCY = 4
PERSONA_TAG_MAX_LENGTH = 20


@dataclass(frozen=True)
class PersonaLlmStageConfig:
    provider: str
    model: str
    temperature: float = 0.7
    max_output_tokens: int = 8192
    response_format_json: bool = True
    env_prefix: str = ""


PERSONA_LLM_STAGE_CONFIGS = {
    "persona_segment_suggestion": PersonaLlmStageConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.4,
        max_output_tokens=4096,
        env_prefix="PERSONA_LLM_SEGMENT_SUGGESTION",
    ),
    "persona_generation_segmentation_identity": PersonaLlmStageConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_SEGMENTATION_IDENTITY",
    ),
    "persona_generation_narrative_polish": PersonaLlmStageConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_NARRATIVE_POLISH",
    ),
    "persona_generation_telecom_dimensions": PersonaLlmStageConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.35,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_TELECOM_DIMENSIONS",
    ),
    "persona_generation_telecom_scores": PersonaLlmStageConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.25,
        max_output_tokens=6144,
        env_prefix="PERSONA_LLM_TELECOM_SCORES",
    ),
    "persona_interview_pack": PersonaLlmStageConfig(
        provider="gemini",
        model=DEFAULT_PERSONA_PACK_MODEL,
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_INTERVIEW_PACK",
    ),
    "persona_interview_question_generation": PersonaLlmStageConfig(
        provider="openai",
        model=DEFAULT_PERSONA_INTERVIEW_MODEL,
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_INTERVIEW_QUESTION",
    ),
    "persona_interview": PersonaLlmStageConfig(
        provider="openai",
        model=DEFAULT_PERSONA_INTERVIEW_MODEL,
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_INTERVIEW_RESULT",
    ),
    "persona_ui_test": PersonaLlmStageConfig(
        provider="openai",
        model=DEFAULT_PERSONA_INTERVIEW_MODEL,
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_UI_TEST",
    ),
    "persona_ui_test_scoring": PersonaLlmStageConfig(
        provider="openai",
        model=DEFAULT_PERSONA_UI_TEST_SCORING_MODEL,
        temperature=0.2,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_UI_TEST_SCORING",
    ),
    "persona_ab_test": PersonaLlmStageConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.7,
        max_output_tokens=8192,
        env_prefix="PERSONA_LLM_AB_TEST",
    ),
}

PERSONA_LLM_PROMPT_STAGE_MARKERS = {
    "STAGE: segment_suggestion": "persona_segment_suggestion",
    "STAGE: segmentation_identity": "persona_generation_segmentation_identity",
    "STAGE: narrative_polish": "persona_generation_narrative_polish",
    "STAGE: telecom_dimensions": "persona_generation_telecom_dimensions",
    "STAGE: telecom_scores": "persona_generation_telecom_scores",
}


def _dt(value):
    return value.isoformat() if value else None


def _clean_mapping(value):
    return value if isinstance(value, (dict, list)) else None


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _clean_model_name(value):
    model = str(value or "").strip()
    return model or None


def _normalize_persona_tag(value):
    tag = str(value or "").strip()
    return tag[:PERSONA_TAG_MAX_LENGTH] if tag else None


def _infer_provider_from_model(model: str | None, fallback: str) -> str:
    normalized = (model or "").strip().lower()
    if normalized.startswith("openai:"):
        return "openai"
    if normalized.startswith("gemini:"):
        return "gemini"
    if normalized.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if normalized.startswith("gemini-"):
        return "gemini"
    return fallback


def _split_provider_model(value: str | None, fallback_provider: str) -> tuple[str, str | None]:
    raw = _clean_model_name(value)
    if not raw:
        return fallback_provider, None
    if ":" in raw:
        provider, model = raw.split(":", 1)
        provider = provider.strip().lower()
        if provider in {"openai", "gemini"} and model.strip():
            return provider, model.strip()
    return _infer_provider_from_model(raw, fallback_provider), raw


TELECOM_BEHAVIOR_SCORE_CONFIG = (
    {
        "key": "brandRetention",
        "label": "브랜드 유지 성향",
        "positive": (
            "브랜드", "유지", "장기", "신뢰", "안정", "안도", "검증", "대형", "프리미엄", "품질", "인프라", "결합",
            "loyal", "retention", "premium", "trusted", "stable",
        ),
        "negative": ("알뜰폰", "번호이동", "변경", "이탈", "저가", "가격 우선", "brand switch", "switch"),
    },
    {
        "key": "optimizationResource",
        "label": "최적화 리소스 투입",
        "positive": (
            "직접 비교", "비교", "검토", "분석", "확인", "재검토", "후보", "시간을 쓰", "발품", "관리", "절감",
            "compare", "review", "optimize", "manage",
        ),
        "negative": (
            "위임", "맡기", "알아서", "귀찮", "번거", "시간이 아깝", "관심이 없다", "결론만", "헤매기보다", "추천에 전적",
            "delegate", "hands off", "too much effort",
        ),
    },
    {
        "key": "informationControl",
        "label": "정보탐색 및 통제 욕구",
        "positive": (
            "직접", "탐색", "검색", "검증", "근거", "비교표", "조건", "스스로", "자율", "통제", "확인", "대조",
            "control", "verify", "evidence", "search",
        ),
        "negative": ("위임", "추천", "전문가", "결론만", "알아서", "관심이 없다", "해독하려 들지", "맡기", "delegate"),
    },
    {
        "key": "digitalAiOpenness",
        "label": "디지털 및 AI 개방성",
        "positive": (
            "AI", "인공지능", "디지털", "앱", "자동", "추천", "개인화", "데이터 제공", "데이터까지 허용", "구독", "ChatGPT",
            "제미나이", "Gemini", "DX", "digital", "personalization",
        ),
        "negative": ("거부", "불신", "대면", "오프라인만", "정보 제공을 꺼", "데이터 제공 거부", "꺼림", "distrust"),
    },
)


def _snake_key(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", value).lower()


def _dimension_group(dimensions: dict, key: str) -> dict:
    value = dimensions.get(key) or dimensions.get(_snake_key(key))
    return value if isinstance(value, dict) else {}


def _collect_text(*values) -> str:
    parts = []
    for value in values:
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, dict):
            parts.extend(_collect_text(*value.values()).split("\n"))
        elif isinstance(value, list):
            parts.extend(_collect_text(*value).split("\n"))
    return "\n".join(part for part in parts if part)


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    hits = []
    for keyword in keywords:
        if keyword and keyword.lower() in lowered:
            hits.append(keyword)
    return hits


def _score_from_keywords(text: str, positive: tuple[str, ...], negative: tuple[str, ...]) -> tuple[int, list[str], list[str]]:
    positive_hits = _keyword_hits(text, positive)
    negative_hits = _keyword_hits(text, negative)
    score = 3
    if len(positive_hits) >= 4:
        score += 2
    elif len(positive_hits) >= 2:
        score += 1
    if len(negative_hits) >= 4:
        score -= 2
    elif len(negative_hits) >= 2:
        score -= 1
    return max(1, min(5, score)), positive_hits[:3], negative_hits[:3]


def _build_telecom_behavior_scores(persona) -> list[dict]:
    stored_scores = _clean_mapping(getattr(persona, "telecom_behavior_scores", None))
    if isinstance(stored_scores, list) and stored_scores:
        return stored_scores
    dimensions = _clean_mapping(getattr(persona, "telecom_behavior_dimensions", None)) or {}
    profile = _clean_mapping(getattr(persona, "profile", None)) or {}
    telecom_profile = _clean_mapping(getattr(persona, "telecom_profile", None)) or {}
    telecom_life = _dimension_group(dimensions, "telecomLifeCharacteristics")
    shared_context = _collect_text(
        profile.get("preferences"),
        profile.get("behaviours"),
        profile.get("behavior_pattern"),
        getattr(persona, "preferences", None),
        getattr(persona, "behaviours", None),
        getattr(persona, "personality", None),
        getattr(persona, "motivation", None),
        getattr(persona, "cultural_background", None),
    )
    if not dimensions and not shared_context:
        return []
    context_by_key = {
        "brandRetention": _collect_text(_dimension_group(dimensions, "brandRetention"), telecom_life, shared_context),
        "optimizationResource": _collect_text(_dimension_group(dimensions, "optimizationResource"), telecom_life, shared_context),
        "informationControl": _collect_text(_dimension_group(dimensions, "informationControl"), telecom_life, shared_context),
        "digitalAiOpenness": _collect_text(
            _dimension_group(dimensions, "digitalAiOpenness"),
            getattr(persona, "ux_interaction", None),
            getattr(persona, "telecom_usage", None),
            telecom_profile,
            shared_context,
        ),
    }
    scores = []
    for config in TELECOM_BEHAVIOR_SCORE_CONFIG:
        text = context_by_key.get(config["key"], "")
        score, positive_hits, negative_hits = _score_from_keywords(text, config["positive"], config["negative"])
        scores.append(
            {
                "key": config["key"],
                "label": config["label"],
                "score": score,
                "maxScore": 5,
                "basis": {
                    "positiveSignals": positive_hits,
                    "negativeSignals": negative_hits,
                },
            }
        )
    return scores


def _compact_json(value, *, max_chars: int = 4000):
    if not isinstance(value, (dict, list)):
        return None
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n... [truncated]"


def _clamp_percent(value, fallback: int = 50):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return fallback
    if not isinstance(value, (int, float)):
        return fallback
    return max(0, min(100, round(value)))


SCORING_METRICS = ("명확성", "사용성", "만족도", "혼란도", "이탈 위험", "효율성")
SCORING_SUB_METRICS = (
    "직관성",
    "인지 용이성",
    "유용성",
    "유연성",
    "행동 유도성",
    "디자인 매력도",
    "서비스 신뢰도",
    "맥락 관계성",
    "관심/동기 적합성",
    "효율성",
)
POSITIVE_SCORING_METRICS = {"명확성", "사용성", "만족도", "효율성"}
NEGATIVE_SCORING_METRICS = {"혼란도", "이탈 위험"}
FLOW_STEP_AVERAGE_WEIGHT = 0.45
FLOW_STEP_MAX_WEIGHT = 0.45
FLOW_STEP_COVERAGE_WEIGHT = 0.1
FLOW_FAILURE_RISK_SLOPE = 0.65
FLOW_COMPLETION_DIRECT_WEIGHT = 0.45
FLOW_COMPLETION_CONFUSION_WEIGHT = 0.2
FLOW_COMPLETION_DROPOFF_WEIGHT = 0.35
FLOW_RISK_SEVERITY_EXPONENT = 1.2
FLOW_RISK_SEVERITY_BASE = 5.8


def _read_string(value):
    return value.strip() if isinstance(value, str) else ""


def _read_number(value, fallback):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback


def _read_index(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, round(value))
    if isinstance(value, str):
        try:
            return max(0, round(float(value.strip())))
        except ValueError:
            return None
    return None


def _read_string_array(value):
    return [_read_string(item) for item in value if _read_string(item)] if isinstance(value, list) else []


def _read_nullable_string(value):
    text = _read_string(value)
    return text or None


def _clamp_range(value, minimum, maximum, fallback):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return fallback
    return max(minimum, min(maximum, value))


def _read_scoring_metric(value):
    if value == "예상 완료율":
        return "효율성"
    return value if isinstance(value, str) and value in SCORING_METRICS else None


def _read_scoring_sub_metric(value):
    return value if isinstance(value, str) and value in SCORING_SUB_METRICS else None


def _normalize_ui_scoring_analysis(raw_value):
    source = _as_dict(raw_value)
    if not source:
        return {"keyElements": [], "analysisEvents": []}

    key_elements = []
    for item in _as_list(source.get("keyElements") or source.get("key_elements")):
        entry = _as_dict(item)
        name = _read_string(entry.get("name"))
        if not name:
            continue
        key_elements.append(
            {
                "name": name,
                "importance": _clamp_range(_read_number(entry.get("importance"), 1), 0.5, 1.5, 1),
                "relatedMetrics": [
                    metric
                    for metric in (_read_scoring_metric(value) for value in _read_string_array(entry.get("relatedMetrics") or entry.get("related_metrics")))
                    if metric
                ],
                "reason": _read_string(entry.get("reason")),
            }
        )

    analysis_events = []
    for item in _as_list(source.get("analysisEvents") or source.get("analysis_events")):
        entry = _as_dict(item)
        metric = _read_scoring_metric(entry.get("metric"))
        sub_metric = _read_scoring_sub_metric(entry.get("subMetric") or entry.get("sub_metric"))
        polarity = entry.get("polarity") if entry.get("polarity") in {"positive", "negative"} else None
        mapping_role = "secondary" if entry.get("mappingRole") == "secondary" or entry.get("mapping_role") == "secondary" else "primary"
        source_comment = _read_string(entry.get("sourceComment") or entry.get("source_comment"))
        target_element = _read_string(entry.get("targetElement") or entry.get("target_element"))
        if not metric or not sub_metric or not polarity or not source_comment or not target_element:
            continue
        screen_index = entry.get("screenIndex", entry.get("screen_index"))
        step_index = entry.get("stepIndex", entry.get("step_index"))
        analysis_events.append(
            {
                "testType": "flow" if entry.get("testType") == "flow" or entry.get("test_type") == "flow" else "screen",
                "metric": metric,
                "subMetric": sub_metric,
                "targetElement": target_element,
                "matchedKeyElement": _read_nullable_string(entry.get("matchedKeyElement") or entry.get("matched_key_element")),
                "polarity": polarity,
                "severity": _clamp_range(_read_number(entry.get("severity"), 3), 1, 5, 3),
                "elementImportance": _clamp_range(_read_number(entry.get("elementImportance") or entry.get("element_importance"), 1), 0.5, 1.5, 1),
                "personaRelevance": _clamp_range(_read_number(entry.get("personaRelevance") or entry.get("persona_relevance"), 3), 1, 5, 3),
                "confidence": _clamp_range(_read_number(entry.get("confidence"), 0.7), 0, 1, 0.7),
                "mappingRole": mapping_role,
                "impactMultiplier": _clamp_range(
                    _read_number(entry.get("impactMultiplier") or entry.get("impact_multiplier"), 0.6 if mapping_role == "secondary" else 1),
                    0.2,
                    1,
                    0.6 if mapping_role == "secondary" else 1,
                ),
                "screenIndex": _read_index(screen_index),
                "stepIndex": _read_index(step_index),
                "reason": _read_string(entry.get("reason")),
                "sourceComment": source_comment,
            }
        )

    return {"keyElements": key_elements, "analysisEvents": analysis_events}


def _cap_flow_completion_by_risk(completion_rate, flow_failure_risk):
    risk_ceiling = _clamp_range(
        100 - flow_failure_risk * FLOW_FAILURE_RISK_SLOPE,
        0,
        99 if flow_failure_risk > 0 else 100,
        100,
    )
    return _clamp_percent(min(completion_rate, risk_ceiling), 0)


def _get_comment_score(event):
    severity = _clamp_range(event.get("severity"), 1, 5, 3)
    is_positive_metric = event.get("metric") in POSITIVE_SCORING_METRICS
    positive_spread = 12 if event.get("testType") == "screen" else 9
    negative_spread = 8
    direction_score = 50 + severity * positive_spread if event.get("polarity") == "positive" else 50 - severity * negative_spread
    risk_score = 50 + severity * negative_spread if event.get("polarity") == "negative" else 50 - severity * positive_spread
    return _clamp_percent(direction_score if is_positive_metric else risk_score, 0)


def _get_comment_weight(event):
    severity = _clamp_range(event.get("severity"), 1, 5, 3)
    element_importance = _clamp_range(event.get("elementImportance"), 0.5, 1.5, 1)
    confidence = _clamp_range(event.get("confidence"), 0, 1, 0.8)
    persona_relevance = _clamp_range(event.get("personaRelevance"), 1, 5, 3)
    impact_multiplier = _clamp_range(event.get("impactMultiplier"), 0.2, 1, 1)
    severity_weight = severity**1.35
    element_weight = element_importance**1.2
    persona_weight = 0.7 + ((persona_relevance - 1) / 4) * 0.8
    confidence_weight = 0.55 + confidence * 0.45
    polarity_weight = 1.12 if event.get("polarity") == "negative" else 1
    return max(0.01, severity_weight * element_weight * confidence_weight * persona_weight * polarity_weight * impact_multiplier)


def _weighted_event_score(events):
    value = 0
    weight = 0
    for event in events:
        event_weight = _get_comment_weight(event)
        value += _get_comment_score(event) * event_weight
        weight += event_weight
    if weight <= 0:
        return None
    return _clamp_percent(value / weight, 0)


def _get_flow_risk_impact(event):
    severity = _clamp_range(event.get("severity"), 1, 5, 3)
    element_importance = _clamp_range(event.get("elementImportance"), 0.5, 1.5, 1)
    confidence = _clamp_range(event.get("confidence"), 0, 1, 0.8)
    persona_relevance = _clamp_range(event.get("personaRelevance"), 1, 5, 3)
    impact_multiplier = _clamp_range(event.get("impactMultiplier"), 0.2, 1, 1)
    persona_factor = 0.85 + ((persona_relevance - 1) / 4) * 0.35
    confidence_factor = 0.65 + confidence * 0.35
    return (severity**FLOW_RISK_SEVERITY_EXPONENT) * FLOW_RISK_SEVERITY_BASE * element_importance * persona_factor * confidence_factor * impact_multiplier


def _accumulated_flow_risk_score(events):
    if not events:
        return None
    risk = 0
    for event in events:
        impact = _get_flow_risk_impact(event)
        risk += impact if event.get("polarity") == "negative" else -impact * 0.7
    return _clamp_percent(risk, 0)


def _collect_flow_step_indices(events, total_step_count=None):
    if isinstance(total_step_count, int) and total_step_count > 0:
        return list(range(total_step_count))
    return sorted({event.get("screenIndex") for event in events if event.get("testType") == "flow" and isinstance(event.get("screenIndex"), int)})


def _aggregate_flow_step_risk(scores):
    if not scores:
        return None
    average_score = sum(scores) / len(scores)
    max_score = max(scores)
    coverage_score = (len([score for score in scores if score > 0]) / len(scores)) * 100
    return _clamp_percent(
        average_score * FLOW_STEP_AVERAGE_WEIGHT + max_score * FLOW_STEP_MAX_WEIGHT + coverage_score * FLOW_STEP_COVERAGE_WEIGHT,
        0,
    )


def _score_flow_risk_metric(events, metric, screen_index=None):
    return _accumulated_flow_risk_score(
        [
            event
            for event in events
            if event.get("testType") == "flow"
            and event.get("metric") == metric
            and (screen_index is None or event.get("screenIndex") == screen_index)
        ]
    )


def _weighted_available_scores(entries):
    value = 0
    weight = 0
    for entry in entries:
        entry_value = entry.get("value")
        entry_weight = entry.get("weight", 0)
        if not isinstance(entry_value, (int, float)) or isinstance(entry_value, bool):
            continue
        value += entry_value * entry_weight
        weight += entry_weight
    if weight <= 0:
        return None
    return _clamp_percent(value / weight, 0)


def _finalize_flow_completion_score(*, step_confusion_scores, step_dropoff_risks, step_efficiency_penalties):
    if not step_confusion_scores or not step_dropoff_risks:
        return None
    overall_confusion_score = _aggregate_flow_step_risk(step_confusion_scores)
    overall_dropoff_risk = _aggregate_flow_step_risk(step_dropoff_risks)
    overall_efficiency_penalty = _aggregate_flow_step_risk(step_efficiency_penalties) if step_efficiency_penalties else None
    direct_completion = _clamp_percent(100 - overall_efficiency_penalty, 0) if isinstance(overall_efficiency_penalty, (int, float)) else None
    raw_completion_rate = _weighted_available_scores(
        [
            {"value": direct_completion, "weight": FLOW_COMPLETION_DIRECT_WEIGHT},
            {"value": 100 - overall_confusion_score if isinstance(overall_confusion_score, (int, float)) else None, "weight": FLOW_COMPLETION_CONFUSION_WEIGHT},
            {"value": 100 - overall_dropoff_risk if isinstance(overall_dropoff_risk, (int, float)) else None, "weight": FLOW_COMPLETION_DROPOFF_WEIGHT},
        ]
    )
    flow_failure_risk = _weighted_available_scores(
        [
            {"value": overall_confusion_score, "weight": 0.45},
            {"value": overall_dropoff_risk, "weight": 0.55},
        ]
    )
    if not isinstance(raw_completion_rate, (int, float)):
        return None
    return raw_completion_rate if not isinstance(flow_failure_risk, (int, float)) else _cap_flow_completion_by_risk(raw_completion_rate, flow_failure_risk)


def _flow_efficiency_penalties_for_steps(*, scoped_events, step_indices):
    completion_events = [event for event in scoped_events if event.get("metric") == "효율성"]
    if not completion_events:
        return []
    return [
        _accumulated_flow_risk_score(
            [event for event in scoped_events if event.get("metric") == "효율성" and event.get("screenIndex") == step_index]
        )
        or 0
        for step_index in step_indices
    ]


def _score_flow_completion_from_flow_analysis(flow_analysis, analysis_events, total_step_count=None):
    items = sorted(
        [item for item in _as_list(flow_analysis) if isinstance(item, dict)],
        key=lambda item: item.get("screenIndex", 0),
    )
    if not items:
        return _score_flow_completion_metric(analysis_events, total_step_count=total_step_count)

    step_confusion_scores = [
        _clamp_percent(item.get("confusionScore", item.get("confusion_score")), 0) for item in items
    ]
    step_dropoff_risks = [
        _clamp_percent(item.get("dropoffRisk", item.get("dropoff_risk")), 0) for item in items
    ]
    step_indices = [item.get("screenIndex") for item in items]
    scoped_events = [event for event in analysis_events if event.get("testType") == "flow"]
    step_efficiency_penalties = _flow_efficiency_penalties_for_steps(
        scoped_events=scoped_events,
        step_indices=step_indices,
    )
    return _finalize_flow_completion_score(
        step_confusion_scores=step_confusion_scores,
        step_dropoff_risks=step_dropoff_risks,
        step_efficiency_penalties=step_efficiency_penalties,
    )


def _score_flow_completion_metric(events, screen_index=None, total_step_count=None):
    scoped_events = [
        event
        for event in events
        if event.get("testType") == "flow" and (screen_index is None or event.get("screenIndex") == screen_index)
    ]
    if not scoped_events:
        return None
    completion_events = [event for event in scoped_events if event.get("metric") == "효율성"]
    if isinstance(screen_index, int):
        completion_risk = _accumulated_flow_risk_score(completion_events)
        direct_completion = _clamp_percent(100 - completion_risk, 0) if isinstance(completion_risk, (int, float)) else None
        confusion_score = _score_flow_risk_metric(events, "혼란도", screen_index)
        dropoff_risk = _score_flow_risk_metric(events, "이탈 위험", screen_index)
        raw_completion_rate = _weighted_available_scores(
            [
                {"value": direct_completion, "weight": FLOW_COMPLETION_DIRECT_WEIGHT},
                {"value": 100 - confusion_score if isinstance(confusion_score, (int, float)) else None, "weight": FLOW_COMPLETION_CONFUSION_WEIGHT},
                {"value": 100 - dropoff_risk if isinstance(dropoff_risk, (int, float)) else None, "weight": FLOW_COMPLETION_DROPOFF_WEIGHT},
            ]
        )
        flow_failure_risk = _weighted_available_scores(
            [
                {"value": confusion_score, "weight": 0.45},
                {"value": dropoff_risk, "weight": 0.55},
            ]
        )
        if not isinstance(raw_completion_rate, (int, float)):
            return None
        return raw_completion_rate if not isinstance(flow_failure_risk, (int, float)) else _cap_flow_completion_by_risk(raw_completion_rate, flow_failure_risk)

    step_indices = _collect_flow_step_indices(scoped_events, total_step_count)
    if not step_indices:
        return None
    step_confusion_scores = [_score_flow_risk_metric(events, "혼란도", step_index) or 0 for step_index in step_indices]
    step_dropoff_risks = [_score_flow_risk_metric(events, "이탈 위험", step_index) or 0 for step_index in step_indices]
    step_efficiency_penalties = _flow_efficiency_penalties_for_steps(
        scoped_events=scoped_events,
        step_indices=step_indices,
    )
    return _finalize_flow_completion_score(
        step_confusion_scores=step_confusion_scores,
        step_dropoff_risks=step_dropoff_risks,
        step_efficiency_penalties=step_efficiency_penalties,
    )


def _score_metric(events, metric):
    return _weighted_event_score([event for event in events if event.get("metric") == metric])


def _score_metric_for_screen(events, metric, screen_index, *, include_unscoped=False):
    return _weighted_event_score(
        [
            event
            for event in events
            if event.get("metric") == metric
            and (event.get("screenIndex") == screen_index or (include_unscoped and event.get("screenIndex") is None))
        ]
    )


def _apply_structured_flow_analysis_scores(*, flow_analysis, analysis_events):
    if not flow_analysis:
        return flow_analysis
    updated = []
    for item in flow_analysis:
        screen_index = item.get("screenIndex")
        confusion_score = _score_flow_risk_metric(analysis_events, "혼란도", screen_index)
        dropoff_risk = _score_flow_risk_metric(analysis_events, "이탈 위험", screen_index)
        ui_clarity = _score_metric_for_screen(analysis_events, "명확성", screen_index)
        visual_hierarchy = _score_metric_for_screen(analysis_events, "만족도", screen_index)
        updated.append(
            {
                **item,
                "confusionScore": confusion_score if isinstance(confusion_score, (int, float)) else item.get("confusionScore"),
                "dropoffRisk": dropoff_risk if isinstance(dropoff_risk, (int, float)) else item.get("dropoffRisk"),
                "uiClarity": ui_clarity if isinstance(ui_clarity, (int, float)) else item.get("uiClarity"),
                "visualHierarchy": visual_hierarchy if isinstance(visual_hierarchy, (int, float)) else item.get("visualHierarchy"),
            }
        )
    return updated


def _apply_structured_screen_scores(*, screen_scores, analysis_events):
    include_unscoped = len(_as_list(screen_scores)) == 1
    updated = []
    for item in _as_list(screen_scores):
        if not isinstance(item, dict):
            continue
        screen_index = item.get("screenIndex")
        clarity = _score_metric_for_screen(analysis_events, "명확성", screen_index, include_unscoped=include_unscoped)
        usability = _score_metric_for_screen(analysis_events, "사용성", screen_index, include_unscoped=include_unscoped)
        satisfaction = _score_metric_for_screen(analysis_events, "만족도", screen_index, include_unscoped=include_unscoped)
        next_clarity = clarity if isinstance(clarity, (int, float)) else item.get("clarity")
        next_usability = usability if isinstance(usability, (int, float)) else item.get("usability")
        next_appeal = satisfaction if isinstance(satisfaction, (int, float)) else item.get("appeal")
        updated.append(
            {
                **item,
                "clarity": next_clarity,
                "usability": next_usability,
                "appeal": next_appeal,
                "satisfaction": next_appeal,
                "overall": round((next_clarity + next_usability + next_appeal) / 3)
                if all(isinstance(value, (int, float)) for value in (next_clarity, next_usability, next_appeal))
                else item.get("overall"),
            }
        )
    return updated


def _build_ui_screen_chunks(total_screens: int, chunk_size: int, overlap: int):
    safe_chunk_size = max(1, int(chunk_size))
    safe_overlap = max(0, min(int(overlap), safe_chunk_size - 1))
    step = max(1, safe_chunk_size - safe_overlap)
    chunks = []
    for start in range(0, total_screens, step):
        chunk = list(range(start, min(start + safe_chunk_size, total_screens)))
        if not chunk:
            continue
        if chunks and len(chunk) < safe_chunk_size:
            break
        chunks.append(chunk)
    return chunks or [list(range(total_screens))]


def _merge_ui_screen_feedbacks(current, incoming):
    merged = list(_as_list(current))
    covered = {item.get("screenIndex") for item in merged if isinstance(item, dict)}
    for item in _as_list(incoming):
        if not isinstance(item, dict):
            continue
        if item.get("screenIndex") in covered:
            continue
        merged.append(item)
        covered.add(item.get("screenIndex"))
    return sorted(merged, key=lambda item: item.get("screenIndex", 0))


def _merge_ui_pin_comments(current, incoming):
    merged = list(_as_list(current))
    existing = {
        (item.get("screenIndex"), item.get("type"), item.get("x"), item.get("y"), str(item.get("content") or ""))
        for item in merged
        if isinstance(item, dict)
    }
    for item in _as_list(incoming):
        if not isinstance(item, dict):
            continue
        key = (item.get("screenIndex"), item.get("type"), item.get("x"), item.get("y"), str(item.get("content") or ""))
        if key in existing:
            continue
        merged.append(item)
        existing.add(key)
    return sorted(merged, key=lambda item: item.get("screenIndex", 0))


def _merge_ui_flow_analysis(current, incoming):
    by_index = {}
    for item in [*_as_list(current), *_as_list(incoming)]:
        if not isinstance(item, dict):
            continue
        screen_index = item.get("screenIndex")
        if screen_index not in by_index:
            by_index[screen_index] = item
    return [by_index[index] for index in sorted(index for index in by_index if index is not None)]


def _apply_structured_scoring(*, fallback_scores: dict, analysis_events, is_flow_test: bool, flow_step_count=None, flow_analysis=None):
    clarity = _score_metric(analysis_events, "명확성")
    usability = _score_metric(analysis_events, "사용성")
    satisfaction = _score_metric(analysis_events, "만족도")
    if is_flow_test and _as_list(flow_analysis):
        overall_flow_score = _score_flow_completion_from_flow_analysis(
            flow_analysis,
            analysis_events,
            total_step_count=flow_step_count,
        )
    elif is_flow_test:
        overall_flow_score = _score_flow_completion_metric(analysis_events, total_step_count=flow_step_count)
    else:
        overall_flow_score = None
    scored = {
        "clarity": clarity if isinstance(clarity, (int, float)) else _clamp_percent(fallback_scores.get("clarity"), 70),
        "usability": usability if isinstance(usability, (int, float)) else _clamp_percent(fallback_scores.get("usability"), 70),
        "appeal": satisfaction if isinstance(satisfaction, (int, float)) else _clamp_percent(fallback_scores.get("appeal"), 65),
    }
    scored["overall"] = round((scored["clarity"] + scored["usability"] + scored["appeal"]) / 3)
    scored["overallFlowScore"] = overall_flow_score if isinstance(overall_flow_score, (int, float)) else fallback_scores.get("overallFlowScore")
    return scored


def _resolve_interview_max_concurrency(persona_count: int) -> int:
    configured = os.getenv("PERSONA_INTERVIEW_MAX_CONCURRENCY") or os.getenv("INTERVIEW_MAX_CONCURRENCY") or ""
    try:
        max_workers = int(configured)
    except Exception:
        max_workers = DEFAULT_INTERVIEW_MAX_CONCURRENCY
    if max_workers <= 0:
        max_workers = DEFAULT_INTERVIEW_MAX_CONCURRENCY
    return max(1, min(max(1, int(persona_count or 0)), max_workers))


def _resolve_ui_test_max_concurrency(persona_count: int) -> int:
    configured = os.getenv("PERSONA_UI_TEST_MAX_CONCURRENCY") or os.getenv("UI_TEST_MAX_CONCURRENCY") or ""
    try:
        max_workers = int(configured)
    except Exception:
        max_workers = DEFAULT_UI_TEST_MAX_CONCURRENCY
    if max_workers <= 0:
        max_workers = DEFAULT_UI_TEST_MAX_CONCURRENCY
    return max(1, min(max(1, int(persona_count or 0)), max_workers))


def _resolve_interview_retry_attempts() -> int:
    configured = os.getenv("PERSONA_INTERVIEW_RETRY_ATTEMPTS") or os.getenv("INTERVIEW_RETRY_ATTEMPTS") or ""
    try:
        retries = int(configured)
    except Exception:
        retries = DEFAULT_INTERVIEW_RETRY_ATTEMPTS
    return max(0, retries)


def _log_persona_interview_event(message: str, **values):
    details = " ".join(f"{key}={value}" for key, value in values.items() if value is not None)
    suffix = f" | {details}" if details else ""
    print(f"[persona-interview] {message}{suffix}", flush=True)


def _normalize_marker_percent(value, fallback: int = 50):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, str):
        value = value.strip()
        if value.endswith("%"):
            value = value[:-1]
        try:
            value = float(value)
        except ValueError:
            return fallback
    if not isinstance(value, (int, float)):
        return fallback
    if 0 <= value <= 1:
        value *= 100
    return max(0, min(100, round(value)))


def _has_marker_percent(value):
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return False
        if value.endswith("%"):
            value = value[:-1]
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _storage_asset_id_from_url(value):
    if not isinstance(value, str):
        return None
    match = re.search(r"/api/persona/storage/(\d+)", value)
    if not match:
        return None
    return int(match.group(1))


def _camelize_result_aliases(payload: dict) -> dict:
    aliases = {
        "test_id": "testId",
        "ab_test_id": "testId",
        "interview_id": "interviewId",
        "persona_id": "personaId",
        "persona_goal_fit": "personaGoalFit",
        "pin_comments": "pinComments",
        "flow_analysis": "flowAnalysis",
        "persona_snapshot": "personaSnapshot",
        "evidence_ids": "evidenceIds",
        "screen_insights": "screenInsights",
        "raw_response": "rawResponse",
        "error_message": "errorMessage",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
    }
    for snake, camel in aliases.items():
        if snake in payload and camel not in payload:
            payload[camel] = payload[snake]
    snapshot = payload.get("persona_snapshot") or {}
    if isinstance(snapshot, dict):
        payload.setdefault("personaName", snapshot.get("name") or "알 수 없는 퍼소나")
        payload.setdefault("personaImageUrl", snapshot.get("imageUrl"))
        payload.setdefault("personaTitle", snapshot.get("title") or snapshot.get("roleArea"))
        if "persona" not in payload:
            payload["persona"] = snapshot
    if "error_message" in payload and "error" not in payload:
        payload["error"] = payload["error_message"]
    return payload


def _camelize_record_aliases(payload: dict) -> dict:
    aliases = {
        "company_id": "companyId",
        "created_by_user_id": "createdByUserId",
        "updated_by_user_id": "updatedByUserId",
        "device_type": "deviceType",
        "validation_type": "validationType",
        "scope_type": "scopeType",
        "source_type": "sourceType",
        "persona_count": "personaCount",
        "screen_count": "screenCount",
        "source_data": "sourceData",
        "service_context": "serviceContext",
        "context_data": "contextData",
        "enable_consistency_validation": "enableConsistencyValidation",
        "consistency_run_count": "consistencyRunCount",
        "product_description": "productDescription",
        "question_set": "questionSet",
        "pack_model": "packModel",
        "persona_ids": "personaIds",
        "participant_code": "participantCode",
        "raw_text": "rawText",
        "source_status": "sourceStatus",
        "processing_error": "processingError",
        "error_message": "errorMessage",
        "started_at": "startedAt",
        "completed_at": "completedAt",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
    }
    for snake, camel in aliases.items():
        if snake in payload and camel not in payload:
            payload[camel] = payload[snake]
    return payload


def _parse_image_data_url(value: str | None):
    if not value:
        return None
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", value.strip(), re.DOTALL)
    if not match:
        return None
    return match.group(1), base64.b64decode(match.group(2))


class PersonaService:
    def __init__(
        self,
        *,
        repository=PersonaRepository,
        session_factory=session_scope,
        storage=persona_storage,
        figma_client=persona_figma_client,
        capture=persona_capture,
        llm_adapter=None,
        openai_adapter=None,
        image_generator=generate_persona_image_data_url,
    ):
        self.repository = repository
        self.session_factory = session_factory
        self.storage = storage
        self.figma_client = figma_client
        self.capture = capture
        self.llm_adapter = llm_adapter
        self.openai_adapter = openai_adapter if openai_adapter is not None else llm_adapter
        self.image_generator = image_generator

    def _ok(self, data=None, status_code=200):
        return PersonaServiceResult(status="ok", data=data or {}, status_code=status_code)

    def _error(self, status: str, error: str, status_code: int):
        return PersonaServiceResult(status=status, error=error, status_code=status_code)

    def _require_name(self, data: dict):
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        data["name"] = name
        return name

    def _can_modify(self, db_session, record, *, company_id: int, user_id: int):
        return self.repository.can_modify_record(db_session, record, company_id=company_id, user_id=user_id)

    def _is_company_admin(self, db_session, *, company_id: int, user_id: int):
        return self.repository.is_company_admin(db_session, company_id=company_id, user_id=user_id)

    def _get_llm_adapter(self):
        if self.llm_adapter is None:
            from reopsai.infrastructure.gemini_service import GeminiService

            self.llm_adapter = GeminiService()
        return self.llm_adapter

    def _get_openai_adapter(self):
        if self.openai_adapter is None:
            from reopsai.infrastructure.openai_service import OpenAIService

            self.openai_adapter = OpenAIService()
        return self.openai_adapter

    def _infer_llm_stage(self, prompt: str, explicit_stage: str | None = None) -> str:
        if explicit_stage:
            return explicit_stage
        for marker, stage in PERSONA_LLM_PROMPT_STAGE_MARKERS.items():
            if marker in prompt:
                return stage
        return "persona_generation_segmentation_identity"

    def _resolve_llm_stage_config(self, stage: str, *, model_override: str | None = None) -> PersonaLlmStageConfig:
        base = PERSONA_LLM_STAGE_CONFIGS.get(stage) or PERSONA_LLM_STAGE_CONFIGS["persona_generation_segmentation_identity"]
        provider = base.provider
        model = base.model
        if base.env_prefix:
            env_provider = _clean_model_name(os.getenv(f"{base.env_prefix}_PROVIDER"))
            env_model = _clean_model_name(os.getenv(f"{base.env_prefix}_MODEL"))
            env_combined = _clean_model_name(os.getenv(base.env_prefix))
            if env_combined:
                provider, parsed_model = _split_provider_model(env_combined, provider)
                model = parsed_model or model
            if env_provider:
                provider = env_provider.lower()
            if env_model:
                provider, parsed_model = _split_provider_model(env_model, provider)
                model = parsed_model or model
        if stage == "persona_interview_pack":
            legacy_pack_model = _clean_model_name(os.getenv("PERSONA_INTERVIEW_PACK_MODEL"))
            if legacy_pack_model:
                provider, parsed_model = _split_provider_model(legacy_pack_model, provider)
                model = parsed_model or model
        elif provider == "gemini":
            legacy_text_model = _clean_model_name(os.getenv("PERSONA_GEMINI_TEXT_MODEL"))
            if legacy_text_model:
                provider, parsed_model = _split_provider_model(legacy_text_model, provider)
                model = parsed_model or model
        if model_override:
            provider, parsed_model = _split_provider_model(model_override, provider)
            model = parsed_model or model
        if provider not in {"openai", "gemini"}:
            provider = base.provider
        return PersonaLlmStageConfig(
            provider=provider,
            model=model,
            temperature=base.temperature,
            max_output_tokens=base.max_output_tokens,
            response_format_json=base.response_format_json,
            env_prefix=base.env_prefix,
        )

    def _default_model_for_stage(self, stage: str) -> str:
        return self._resolve_llm_stage_config(stage).model

    def _generate_text(
        self,
        prompt: str,
        *,
        media_parts: Optional[list[dict]] = None,
        stage: str | None = None,
        model_override: str | None = None,
    ) -> tuple[str, dict]:
        stage_key = self._infer_llm_stage(prompt, stage)
        stage_config = self._resolve_llm_stage_config(stage_key, model_override=model_override)
        generation_config = {
            "temperature": stage_config.temperature,
            "max_output_tokens": stage_config.max_output_tokens,
        }
        if stage_config.response_format_json and stage_config.provider == "openai":
            generation_config["response_format"] = {"type": "json_object"}
        elif stage_config.response_format_json and stage_config.provider == "gemini":
            generation_config["response_mime_type"] = "application/json"
        adapter = self._get_openai_adapter() if stage_config.provider == "openai" else self._get_llm_adapter()
        if media_parts and hasattr(adapter, "generate_multimodal_response"):
            result = adapter.generate_multimodal_response(
                prompt,
                media_parts=media_parts,
                generation_config=generation_config,
                model_name=stage_config.model,
            )
        else:
            result = adapter.generate_response(
                prompt,
                generation_config=generation_config,
                model_name=stage_config.model,
            )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Persona LLM generation failed")
        usage = dict(result.get("usage") or {})
        usage["model"] = stage_config.model
        usage["provider"] = stage_config.provider
        usage["stage"] = stage_key
        if media_parts:
            usage["media_parts"] = len(media_parts)
        return result.get("content") or "", usage

    def _generate_json(
        self,
        prompt: str,
        *,
        feature_key: str,
        company_id: int,
        user_id: int,
        media_parts: Optional[list[dict]] = None,
        model_override: str | None = None,
    ) -> tuple[dict, dict]:
        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key=feature_key)

        def generate():
            log_interview_stage = feature_key in {"persona_interview", "persona_interview_pack"}
            if log_interview_stage:
                _log_persona_interview_event("llm_start", feature=feature_key, thread=threading.current_thread().name)
            text, usage = self._generate_text(prompt, media_parts=media_parts, stage=feature_key, model_override=model_override)
            if log_interview_stage:
                _log_persona_interview_event("llm_end", feature=feature_key, thread=threading.current_thread().name)
            try:
                return json.loads(text), usage
            except Exception:
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if not match:
                    raise ValueError("LLM response did not contain JSON")
                return json.loads(match.group(0)), usage

        return run_with_llm_usage_context(usage_context, generate)

    def _persona_snapshot_payload(self, persona):
        payload = self.persona_payload(persona)
        return {
            "id": payload["id"],
            "name": payload["name"],
            "imageUrl": payload.get("imageUrl"),
            "age": payload.get("age"),
            "generation": payload.get("generation"),
            "gender": payload.get("gender"),
            "income": payload.get("income"),
            "profile": payload.get("profile"),
            "title": payload.get("title") or payload.get("roleArea"),
            "sector": payload.get("sector"),
            "organisation": payload.get("organisation"),
            "roleArea": payload.get("roleArea"),
            "roleLevel": payload.get("roleLevel"),
            "currentCity": payload.get("currentCity"),
            "currentCountry": payload.get("currentCountry"),
            "language": payload.get("language"),
        }

    def _detach_persona_for_ui_test(self, persona):
        return SimpleNamespace(
            id=persona.id,
            schema_version=persona.schema_version,
            company_id=persona.company_id,
            team_id=persona.team_id,
            folder_id=persona.folder_id,
            created_by_user_id=persona.created_by_user_id,
            name=persona.name,
            tag=persona.tag,
            gender=persona.gender,
            title=persona.title,
            personality=persona.personality,
            language=persona.language,
            source_type=persona.source_type,
            source_data=_clean_mapping(persona.source_data),
            image_asset_id=persona.image_asset_id,
            image_url=persona.image_url,
            image_mime_type=persona.image_mime_type,
            image_prompt=persona.image_prompt,
            locale=persona.locale,
            age=persona.age,
            profile=_clean_mapping(persona.profile),
            telecom_profile=_clean_mapping(persona.telecom_profile),
            income=persona.income,
            sector=persona.sector,
            generation=persona.generation,
            ethnicity=persona.ethnicity,
            current_city=persona.current_city,
            current_country=persona.current_country,
            locations=_clean_mapping(persona.locations),
            organisation=persona.organisation,
            role_area=persona.role_area,
            role_level=persona.role_level,
            attitudes=persona.attitudes,
            biography=persona.biography,
            demeanour=persona.demeanour,
            interests=persona.interests,
            behaviours=persona.behaviours,
            motivation=persona.motivation,
            upbringing=persona.upbringing,
            preferences=persona.preferences,
            social_context=persona.social_context,
            cultural_background=persona.cultural_background,
            quote=persona.quote,
            additional_info=persona.additional_info,
            telecom_usage=_clean_mapping(persona.telecom_usage),
            telecom_values=_clean_mapping(persona.telecom_values),
            ux_interaction=_clean_mapping(persona.ux_interaction),
            telecom_behavior_dimensions=_clean_mapping(persona.telecom_behavior_dimensions),
            telecom_behavior_scores=_clean_mapping(getattr(persona, "telecom_behavior_scores", None)),
            generation_metadata=_clean_mapping(persona.generation_metadata),
            created_at=persona.created_at,
            updated_at=persona.updated_at,
            interview_pack=_clean_mapping(getattr(persona, "interview_pack", None)),
        )

    def _detach_ui_test_for_parallel(self, test):
        return SimpleNamespace(
            id=test.id,
            name=test.name,
            description=test.description,
            scope_type=test.scope_type,
            source_type=test.source_type,
            device_type=test.device_type,
            validation_type=test.validation_type,
            source_data=_clean_mapping(test.source_data),
        )

    def _persona_context(self, persona, persona_pack: Optional[dict] = None):
        payload = self.persona_payload(persona)
        field_labels = [
            ("Name", payload.get("name")),
            ("Tag", payload.get("tag")),
            ("Age", f"{payload.get('age')}세" if payload.get("age") else None),
            ("Generation", payload.get("generation")),
            ("Gender", payload.get("gender")),
            ("Title/Role", payload.get("title") or payload.get("roleArea")),
            ("Sector", payload.get("sector")),
            ("Organisation", payload.get("organisation")),
            ("Role Level", payload.get("roleLevel")),
            ("City/Country", " / ".join(part for part in [payload.get("currentCity"), payload.get("currentCountry")] if part)),
            ("Personality", payload.get("personality")),
            ("Biography", payload.get("biography")),
            ("Attitudes", payload.get("attitudes")),
            ("Behaviours", payload.get("behaviours")),
            ("Motivation", payload.get("motivation")),
            ("Preferences", payload.get("preferences")),
            ("Interests", payload.get("interests")),
            ("Social Context", payload.get("socialContext")),
            ("Cultural Background", payload.get("culturalBackground")),
            ("Quote", payload.get("quote")),
        ]
        parts = [f"{label}: {value}" for label, value in field_labels if value]
        for label, key in (
            ("Profile JSON", "profile"),
            ("Telecom Profile JSON", "telecom_profile"),
            ("Telecom Usage JSON", "telecomUsage"),
            ("Telecom Values JSON", "telecomValues"),
            ("UX Interaction JSON", "uxInteraction"),
            ("Telecom Behavior Dimensions JSON", "telecomBehaviorDimensions"),
            ("Source Data JSON", "sourceData"),
        ):
            text = _compact_json(payload.get(key), max_chars=1600)
            if text:
                parts.append(f"{label}:\n{text}")
        interview_pack = _compact_json(persona_pack or getattr(persona, "interview_pack", None), max_chars=6000)
        if interview_pack:
            parts.append(f"Persona Interview Pack:\n{interview_pack}")
        return "\n".join(part for part in parts if part)

    def _resolve_run_personas(self, db_session, *, company_id: int, explicit_ids=None, source_data=None):
        persona_ids = explicit_ids or []
        if not persona_ids and isinstance(source_data, dict):
            selection = source_data.get("personaSelection") or source_data.get("persona_selection") or {}
            if not selection.get("useAllPersonas") and selection.get("selectedPersonaIds"):
                persona_ids = selection.get("selectedPersonaIds") or []
        if persona_ids:
            return self.repository.list_personas_by_ids(db_session, company_id=company_id, persona_ids=persona_ids)
        return self.repository.list_all_personas(db_session, company_id=company_id)

    def _screen_manifest(self, source_data):
        if not isinstance(source_data, dict):
            source_data = {}
        screens = []
        figma_preview = _as_dict(source_data.get("figmaPreview") or source_data.get("figma_preview"))
        figma_transitions = _as_list(figma_preview.get("transitions"))

        def interaction_hints_for(screen: dict) -> list[dict]:
            screen_id = str(screen.get("id") or "")
            figma_node_id = str(screen.get("figmaNodeId") or screen.get("figma_node_id") or "")
            match_ids = {item for item in (screen_id, f"screen_{figma_node_id}" if figma_node_id else "") if item}
            width = screen.get("width") or screen.get("imageWidth") or screen.get("image_width")
            height = screen.get("height") or screen.get("imageHeight") or screen.get("image_height")
            try:
                width = float(width or 0)
                height = float(height or 0)
            except (TypeError, ValueError):
                width, height = 0, 0
            hints = []
            for transition in figma_transitions:
                if not isinstance(transition, dict) or str(transition.get("fromScreenId") or "") not in match_ids:
                    continue
                bounds = _as_dict(transition.get("controlBounds") or transition.get("control_bounds"))
                center_x = center_y = None
                if bounds and width > 0 and height > 0:
                    center_x = _normalize_marker_percent(((bounds.get("x") or 0) + (bounds.get("width") or 0) / 2) / width)
                    center_y = _normalize_marker_percent(((bounds.get("y") or 0) + (bounds.get("height") or 0) / 2) / height)
                hints.append(
                    {
                        "controlNodeId": transition.get("controlNodeId") or transition.get("control_node_id"),
                        "controlNodeName": transition.get("controlNodeName") or transition.get("control_node_name"),
                        "controlText": transition.get("controlText") or transition.get("control_text"),
                        "navigationType": transition.get("navigationType") or transition.get("navigation_type"),
                        "toScreenId": transition.get("toScreenId") or transition.get("to_screen_id"),
                        "controlBounds": bounds or None,
                        "centerX": center_x,
                        "centerY": center_y,
                    }
                )
            return hints

        for entry in _as_list(source_data.get("imageEntries") or source_data.get("image_entries")):
            screens.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name") or entry.get("fileName"),
                    "source": entry.get("imageUrl") or entry.get("image_url"),
                    "sourceType": "image",
                    "fileName": entry.get("fileName") or entry.get("file_name"),
                }
            )
        for entry in _as_list(source_data.get("urlEntries") or source_data.get("url_entries")):
            screens.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name") or entry.get("pageTitle") or entry.get("url"),
                    "source": entry.get("capturedImageUrl") or entry.get("captured_image_url") or entry.get("url"),
                    "sourceType": "url",
                    "url": entry.get("url"),
                    "pageTitle": entry.get("pageTitle") or entry.get("page_title"),
                    "capturedImageUrl": entry.get("capturedImageUrl") or entry.get("captured_image_url"),
                }
            )
        for entry in _as_list(source_data.get("figmaScreens") or source_data.get("figma_screens")):
            if not isinstance(entry, dict):
                continue
            screen = {
                **entry,
                "id": entry.get("id") or entry.get("figmaNodeId") or entry.get("figma_node_id"),
                "name": entry.get("name"),
                "source": entry.get("imageUrl") or entry.get("image_url"),
                "sourceType": "figma",
                "figmaNodeId": entry.get("figmaNodeId") or entry.get("figma_node_id"),
            }
            hints = interaction_hints_for(screen)
            if hints:
                screen["interactionHints"] = hints
            screens.append(screen)
        if not screens:
            for entry in _as_list(source_data.get("screens")):
                if not isinstance(entry, dict):
                    continue
                screens.append(
                    {
                        "id": entry.get("id") or entry.get("screenId") or f"screen-{len(screens) + 1}",
                        "name": entry.get("name") or entry.get("label") or entry.get("filename") or f"화면 {len(screens) + 1}",
                        "source": entry.get("imageUrl") or entry.get("image_url") or entry.get("source") or entry.get("url"),
                        "sourceType": source_data.get("sourceType") or source_data.get("source_type"),
                        "url": entry.get("url"),
                    }
                )
        if not screens and isinstance(source_data.get("figma_flow") or source_data.get("figmaFlow"), dict):
            flow = source_data.get("figma_flow") or source_data.get("figmaFlow")
            figma_file = _as_dict(source_data.get("figma_file") or source_data.get("figmaFile"))
            screens.append(
                {
                    "id": flow.get("id") or flow.get("figma_start_node_id") or flow.get("figmaStartNodeId") or "figma-flow",
                    "name": flow.get("figma_flow_name") or flow.get("flowName") or flow.get("figmaFlowName") or "Figma flow",
                    "source": flow.get("imageUrl") or flow.get("thumbnailUrl") or figma_file.get("thumbnail_url") or figma_file.get("thumbnailUrl"),
                    "sourceType": "figma",
                    "figmaNodeId": flow.get("figma_start_node_id") or flow.get("figmaStartNodeId"),
                    "figmaFileName": figma_file.get("figma_file_name") or figma_file.get("fileName") or figma_file.get("figmaFileName"),
                }
            )
        if not screens:
            screens.append({"id": "screen-1", "name": "Provided test source", "source": None})
        return screens

    def _screen_image_source(self, screen: dict):
        return screen.get("capturedImageUrl") or screen.get("source") or screen.get("imageUrl") or screen.get("image_url")

    def _read_screen_media_parts(self, db_session, *, company_id: int, screens):
        media_parts = []
        for screen_index, screen in enumerate(screens):
            source = self._screen_image_source(screen)
            if not source:
                continue
            screen_label = f"screenIndex {screen_index} / 화면 {screen_index + 1} / id={screen.get('id')} / name={screen.get('name') or f'화면 {screen_index + 1}'}"
            if isinstance(source, str) and source.startswith("data:") and ";base64," in source:
                header, data_base64 = source.split(";base64,", 1)
                media_parts.append({"type": "text", "text": screen_label, "screenIndex": screen_index})
                media_parts.append(
                    {
                        "type": "image",
                        "screenIndex": screen_index,
                        "mime_type": header.removeprefix("data:") or "image/png",
                        "data_base64": data_base64,
                    }
                )
                continue

            asset_id = _storage_asset_id_from_url(source)
            if not asset_id:
                continue
            try:
                asset = self.repository.get_asset(db_session, company_id=company_id, asset_id=asset_id)
                if not asset:
                    continue
                path = self.storage.resolve_local_path(asset.storage_key)
                if not path.exists() or not path.is_file():
                    continue
                mime_type = asset.mime_type or mimetypes.guess_type(str(path))[0] or "image/png"
                media_parts.append({"type": "text", "text": screen_label, "screenIndex": screen_index})
                media_parts.append(
                    {
                        "type": "image",
                        "screenIndex": screen_index,
                        "mime_type": mime_type,
                        "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    }
                )
            except Exception:
                continue
        return media_parts

    def _cache_figma_preview_screens(self, db_session, *, company_id: int, user_id: int, figma_file, figma_flow, access_token: str):
        file_key = _first_text(
            getattr(figma_file, "figma_file_key", None),
            figma_file.get("figma_file_key") if isinstance(figma_file, dict) else None,
            figma_file.get("figmaFileKey") if isinstance(figma_file, dict) else None,
        )
        start_node_id = _first_text(
            getattr(figma_flow, "figma_start_node_id", None),
            figma_flow.get("figma_start_node_id") if isinstance(figma_flow, dict) else None,
            figma_flow.get("figmaStartNodeId") if isinstance(figma_flow, dict) else None,
            figma_flow.get("figma_node_id") if isinstance(figma_flow, dict) else None,
            figma_flow.get("figmaNodeId") if isinstance(figma_flow, dict) else None,
        )
        if not file_key or not start_node_id:
            return {"screens": [], "transitions": [], "startScreenId": ""}

        preview = self.figma_client.fetch_flow_preview(file_key=file_key, start_node_id=start_node_id, access_token=access_token)
        cached_screens = []
        for index, screen in enumerate(_as_list(preview.get("screens"))):
            if not isinstance(screen, dict):
                continue
            remote_image_url = _first_text(screen.get("imageUrl"), screen.get("image_url"))
            image_url = remote_image_url
            asset_id = None
            if remote_image_url:
                try:
                    image_bytes, mime_type = self.figma_client.download_image(remote_image_url)
                    node_id = _first_text(screen.get("figmaNodeId"), screen.get("figma_node_id"), start_node_id)
                    storage_data = self.storage.save_bytes(
                        image_bytes,
                        company_id=company_id,
                        filename=f"figma-{str(node_id).replace(':', '-')}-{index + 1}.png",
                        mime_type=mime_type,
                        asset_type="ui_test_figma",
                    )
                    asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
                    asset_id = asset.id
                    image_url = f"/api/persona/storage/{asset.id}"
                except Exception:
                    image_url = remote_image_url
            cached_screens.append(
                {
                    **screen,
                    "imageUrl": image_url,
                    "image_url": image_url,
                    "remoteImageUrl": remote_image_url,
                    "remote_image_url": remote_image_url,
                    "assetId": asset_id,
                    "asset_id": asset_id,
                    "order": screen.get("order", index),
                }
            )

        return {
            **preview,
            "screens": cached_screens,
            "screenCount": len(cached_screens),
            "screen_count": len(cached_screens),
            "transitionCount": len(_as_list(preview.get("transitions"))),
            "transition_count": len(_as_list(preview.get("transitions"))),
        }

    def _resolve_ui_source_data_for_run(self, *, company_id: int, user_id: int, source_data: dict):
        if not isinstance(source_data, dict):
            return {}
        figma_file = _as_dict(source_data.get("figma_file") or source_data.get("figmaFile"))
        figma_flow = _as_dict(source_data.get("figma_flow") or source_data.get("figmaFlow"))
        figma_screens = _as_list(source_data.get("figmaScreens") or source_data.get("figma_screens"))
        if figma_file and figma_flow and not any(_first_text(screen.get("imageUrl"), screen.get("image_url")) for screen in figma_screens if isinstance(screen, dict)):
            file_key = _first_text(figma_file.get("figma_file_key"), figma_file.get("figmaFileKey"))
            node_id = _first_text(figma_flow.get("figma_start_node_id"), figma_flow.get("figmaStartNodeId"), figma_flow.get("figma_node_id"), figma_flow.get("figmaNodeId"))
            if file_key and node_id:
                with self.session_factory() as db_session:
                    account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
                    if not account:
                        raise PersonaUrlCaptureError("Figma account is not connected")
                    access_token = self.figma_client.decrypt(account.access_token_encrypted)
                    if not access_token:
                        raise PersonaUrlCaptureError("Figma account is not connected")
                    preview = self._cache_figma_preview_screens(
                        db_session,
                        company_id=company_id,
                        user_id=user_id,
                        figma_file=figma_file,
                        figma_flow=figma_flow,
                        access_token=access_token,
                    )
                    screens = preview.get("screens") or []
                    return {
                        **source_data,
                        "figmaPreview": preview,
                        "figma_preview": preview,
                        "figmaScreens": screens,
                        "figma_screens": screens,
                        "screens": screens,
                    }
        entries = _as_list(source_data.get("urlEntries") or source_data.get("url_entries"))
        if not entries:
            return source_data
        updated_entries = []
        changed = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("capturedImageUrl") or entry.get("captured_image_url") or not entry.get("url"):
                updated_entries.append(entry)
                continue
            captured = self.capture_url(company_id=company_id, user_id=user_id, url=entry.get("url"))
            if captured.status != "ok":
                raise PersonaUrlCaptureError(captured.error or "URL capture failed")
            captured_data = _as_dict(captured.data).get("data") or {}
            captured_image_url = captured_data.get("capturedImageUrl")
            if not captured_image_url:
                raise PersonaUrlCaptureError("URL capture did not produce a screenshot image")
            entry = {
                **entry,
                "url": captured_data.get("url") or entry.get("url"),
                "pageTitle": captured_data.get("title") or entry.get("pageTitle"),
                "capturedImageUrl": captured_image_url,
                "captureBackend": captured_data.get("capture_backend"),
                "captureStatusCode": captured_data.get("status_code"),
            }
            changed = True
            updated_entries.append(entry)
        if not changed:
            return source_data
        return {
            **source_data,
            "urlEntries": updated_entries,
            "url_entries": updated_entries,
        }

    def _build_ui_prompt(self, *, test, persona, screens, persona_pack: Optional[dict] = None):
        source_data = _as_dict(getattr(test, "source_data", None))
        flow_goal = _first_text(source_data.get("flow_goal"), source_data.get("flowGoal"), test.description)
        persona_name = getattr(persona, "name", None) or "이 퍼소나"
        return build_ui_test_prompt(
            test_name=test.name,
            test_description=test.description,
            scope_type=test.scope_type,
            flow_goal=flow_goal,
            persona_name=persona_name,
            persona_context=self._persona_context(persona, persona_pack=persona_pack),
            screens=screens,
        )

    def _ui_test_source_context(self, test):
        source_data = _as_dict(getattr(test, "source_data", None))
        return {
            "source_type": getattr(test, "source_type", None) or source_data.get("sourceType") or source_data.get("source_type") or "",
            "device_type": source_data.get("deviceType") or source_data.get("device_type") or "desktop",
        }

    def _filter_ui_media_parts(self, media_parts, screen_indices):
        allowed = set(screen_indices)
        return [
            part
            for part in _as_list(media_parts)
            if not isinstance(part, dict) or part.get("screenIndex") in allowed or part.get("screen_index") in allowed
        ]

    def _build_ui_chunk_prompt(self, *, test, persona, screens, screen_indices, persona_pack: Optional[dict] = None, repair_mode: bool = False):
        context = self._ui_test_source_context(test)
        return build_ui_chunk_prompt(
            test_name=test.name,
            test_description=test.description,
            scope_type=test.scope_type,
            source_type=context["source_type"],
            device_type=context["device_type"],
            persona_context=self._persona_context(persona, persona_pack=persona_pack),
            screens=screens,
            screen_indices=screen_indices,
            repair_mode=repair_mode,
        )

    def _fallback_ui_chunk_feedback(self, *, screens, screen_indices, is_flow: bool):
        safe_indices = [index for index in screen_indices if isinstance(index, int) and 0 <= index < len(screens)]
        screen_feedbacks = []
        pin_comments = []
        flow_analysis = []
        for screen_index in safe_indices:
            screen_name = screens[screen_index].get("name") or f"화면 {screen_index + 1}"
            screen_feedbacks.append(
                {
                    "screenIndex": screen_index,
                    "feedback": (
                        f"{screen_name} 단계에서는 목표를 계속 수행하려고 할 때 다음 행동이 바로 이어지는지가 중요해요. 현재 단계와 다음 단계의 연결이 더 분명하면 덜 망설이고 진행할 수 있을 것 같아요."
                        if is_flow
                        else f"{screen_name}에서는 먼저 핵심 정보와 다음 행동이 얼마나 또렷한지 확인하게 돼요. 버튼 이름과 현재 위치가 더 분명하면 처음 쓰는 사람도 덜 망설일 것 같아요."
                    ),
                }
            )
            pin_comments.append(
                {
                    "screenIndex": screen_index,
                    "x": 50,
                    "y": 50,
                    "type": "improvement",
                    "content": (
                        f"{screen_name} 단계에서 이전 행동의 결과와 다음으로 눌러야 할 동선이 함께 보이면 task를 계속 진행하기 쉬워요."
                        if is_flow
                        else f"{screen_name}의 핵심 행동 영역이 더 눈에 띄면 다음 단계를 더 쉽게 판단할 수 있어요."
                    ),
                    "hasMarkerCoordinates": False,
                    "has_marker_coordinates": False,
                }
            )
            if is_flow:
                flow_analysis.append(
                    {
                        "screenIndex": screen_index,
                        "confusionScore": 55,
                        "dropoffRisk": 45,
                        "suggestions": ["현재 단계와 다음 행동을 더 명확하게 보여주면 흐름을 따라가기 쉬워요."],
                        "transitionFromPrevious": None
                        if screen_index == 0
                        else f"{screen_name} 단계에서 이전 단계의 선택 결과가 이어졌는지 더 분명하게 보여야 해요.",
                        "expectedNextAction": f"{screen_name} 단계의 핵심 버튼이나 탐색 경로를 확인하고 다음 단계로 이동해요.",
                        "bottleneckRisk": "medium",
                        "uiClarity": 50,
                        "visualHierarchy": 50,
                    }
                )
        return {"screenFeedbacks": screen_feedbacks, "pinComments": pin_comments, "flowAnalysis": flow_analysis}

    def _normalize_ui_chunk_feedback(self, *, parsed, screens, screen_indices, is_flow: bool):
        allowed = set(screen_indices)
        max_screen_index = max(len(screens) - 1, 0)
        screen_feedbacks = []
        for item in _as_list(_as_dict(parsed).get("screenFeedbacks") or _as_dict(parsed).get("screen_feedbacks")):
            if not isinstance(item, dict):
                continue
            screen_index = self._resolve_ui_screen_reference_index(item, screens)
            if screen_index not in allowed:
                continue
            text = _first_text(item.get("feedback"), item.get("content"), item.get("comment"))
            if text:
                screen_feedbacks.append({**item, "screenIndex": screen_index, "feedback": text})

        pin_comments = []
        for index, item in enumerate(_as_list(_as_dict(parsed).get("pinComments") or _as_dict(parsed).get("pin_comments"))):
            if not isinstance(item, dict):
                continue
            screen_index = self._resolve_ui_screen_reference_index(item, screens)
            if screen_index not in allowed:
                continue
            content = str(item.get("content") or item.get("comment") or "").strip()
            if not content:
                continue
            has_marker_coordinates = _has_marker_percent(item.get("x")) and _has_marker_percent(item.get("y"))
            pin_comments.append(
                {
                    **item,
                    "screenIndex": max(0, min(screen_index, max_screen_index)),
                    "x": _normalize_marker_percent(item.get("x"), 42 + (index % 3) * 8),
                    "y": _normalize_marker_percent(item.get("y"), 38 + (index % 3) * 10),
                    "hasMarkerCoordinates": has_marker_coordinates,
                    "has_marker_coordinates": has_marker_coordinates,
                    "type": self._normalize_ui_pin_type(item.get("type")),
                    "content": content,
                }
            )

        flow_analysis = []
        if is_flow:
            for item in _as_list(_as_dict(parsed).get("flowAnalysis") or _as_dict(parsed).get("flow_analysis")):
                if not isinstance(item, dict):
                    continue
                screen_index = self._resolve_ui_screen_reference_index(item, screens)
                if screen_index not in allowed:
                    continue
                flow_analysis.append(
                    {
                        **item,
                        "screenIndex": max(0, min(screen_index, max_screen_index)),
                        "confusionScore": _clamp_percent(item.get("confusionScore", item.get("confusion_score", 35)), 35),
                        "dropoffRisk": _clamp_percent(item.get("dropoffRisk", item.get("dropoff_risk", 30)), 30),
                        "suggestions": [str(point).strip() for point in _as_list(item.get("suggestions")) if str(point).strip()][:5],
                        "transitionFromPrevious": item.get("transitionFromPrevious") or item.get("transition_from_previous"),
                        "expectedNextAction": item.get("expectedNextAction") or item.get("expected_next_action"),
                        "bottleneckRisk": item.get("bottleneckRisk") or item.get("bottleneck_risk") or "low",
                        "uiClarity": _clamp_percent(item.get("uiClarity", item.get("ui_clarity", 50)), 50),
                        "visualHierarchy": _clamp_percent(item.get("visualHierarchy", item.get("visual_hierarchy", 50)), 50),
                    }
                )
        return {"screenFeedbacks": screen_feedbacks, "pinComments": pin_comments, "flowAnalysis": flow_analysis}

    def _run_ui_chunk_feedback(self, *, company_id: int, user_id: int, test, persona, screens, screen_indices, media_parts=None, persona_pack: Optional[dict] = None, repair_mode: bool = False):
        is_flow = test.scope_type == "flow" and len(screens) > 1
        prompt = self._build_ui_chunk_prompt(
            test=test,
            persona=persona,
            screens=screens,
            screen_indices=screen_indices,
            persona_pack=persona_pack,
            repair_mode=repair_mode,
        )
        try:
            parsed, usage = self._generate_json(
                prompt,
                feature_key="persona_ui_test",
                company_id=company_id,
                user_id=user_id,
                media_parts=self._filter_ui_media_parts(media_parts, screen_indices),
            )
            feedback = self._normalize_ui_chunk_feedback(parsed=parsed, screens=screens, screen_indices=screen_indices, is_flow=is_flow)
            raw_text = json.dumps(parsed, ensure_ascii=False)
        except ValueError as exc:
            feedback = self._fallback_ui_chunk_feedback(screens=screens, screen_indices=screen_indices, is_flow=is_flow)
            raw_text = json.dumps(
                {
                    "promptVersion": "persona_test_v2",
                    "error": str(exc),
                    "stage": "chunk",
                    "screenIndices": screen_indices,
                    "repairMode": repair_mode,
                },
                ensure_ascii=False,
            )
            usage = {"model": "fallback", "stage": "persona_ui_test"}
        return {"feedback": feedback, "rawText": raw_text, "usage": usage}

    def _fallback_ui_feedback(self, *, test, persona, screens):
        persona_name = getattr(persona, "name", "Persona")
        is_flow = test.scope_type == "flow" and len(screens) > 1
        screen_feedbacks = [
            {"screenIndex": index, "feedback": f"{persona_name} 관점에서 {screen.get('name') or index + 1} 화면의 다음 행동과 정보 구조를 확인했습니다."}
            for index, screen in enumerate(screens)
        ]
        screen_scores = [
            {
                "screenIndex": index,
                "screenId": str(screen.get("id") or f"screen-{index + 1}"),
                "clarity": 70,
                "usability": 70,
                "appeal": 65,
                "satisfaction": 65,
                "overall": 68,
            }
            for index, screen in enumerate(screens)
        ]
        flow_analysis = [
            {
                "screenIndex": index,
                "confusionScore": 35,
                "dropoffRisk": 30,
                "suggestions": ["다음 행동을 더 명확히 표시합니다."],
                "expectedNextAction": "다음 단계로 이동",
                "bottleneckRisk": "low",
            }
            for index, _screen in enumerate(screens)
        ] if is_flow else []
        return {
            "summary": f"{persona_name}님은 주요 정보와 다음 행동을 기준으로 화면을 평가했습니다.",
            "personaGoalFit": "목표 수행에 필요한 핵심 정보를 확인할 수 있습니다.",
            "scores": {
                "clarity": 70,
                "usability": 70,
                "appeal": 65,
                "overall": 68,
                "overallFlowScore": 68 if is_flow else None,
                "screenScores": screen_scores,
            },
            "feedback": {"overallFeedback": f"{persona_name}님은 전반적으로 이해 가능한 흐름으로 평가했습니다.", "screenFeedbacks": screen_feedbacks},
            "pinComments": [
                item
                for index, _screen in enumerate(screens)
                for item in (
                    {"screenIndex": index, "x": 42, "y": 44, "type": "praise", "content": "핵심 정보 접근이 가능해 화면의 목적을 빠르게 파악할 수 있습니다."},
                    {"screenIndex": index, "x": 58, "y": 52, "type": "improvement", "content": "핵심 CTA와 근거 정보를 더 가깝게 배치하면 다음 행동 판단이 쉬워집니다."},
                )
            ],
            "flowAnalysis": flow_analysis,
            "strengths": ["핵심 정보 접근이 가능합니다."],
            "risks": ["일부 사용자는 다음 행동을 다시 확인할 수 있습니다."],
            "recommendations": ["주요 CTA와 신뢰 근거를 강화합니다."],
            "screenInsights": [
                {
                    "screenId": str(screen.get("id") or f"screen-{index + 1}"),
                    "name": screen.get("name") or f"화면 {index + 1}",
                    "positives": ["핵심 정보 접근이 가능합니다."],
                    "issues": ["핵심 CTA와 근거 정보의 근접성이 약합니다."],
                    "recommendation": "핵심 행동을 강조합니다.",
                }
                for index, screen in enumerate(screens)
            ],
        }

    def _normalize_ui_pin_type(self, raw_type):
        value = str(raw_type or "").strip().lower()
        if value in {"praise", "positive", "strength"}:
            return "praise"
        if value in {"problem", "negative", "risk", "issue"}:
            return "problem"
        if value == "improvement":
            return "improvement"
        return "improvement"

    def _pin_matches_interaction_hint(self, pin: dict, hint: dict) -> bool:
        if not isinstance(pin, dict) or not isinstance(hint, dict):
            return False
        pin_control_id = _first_text(pin.get("controlNodeId"), pin.get("control_node_id"), pin.get("targetNodeId"), pin.get("target_node_id"))
        hint_control_id = _first_text(hint.get("controlNodeId"), hint.get("control_node_id"))
        if pin_control_id and hint_control_id and pin_control_id == hint_control_id:
            return True
        content = str(pin.get("content") or pin.get("comment") or pin.get("targetElement") or pin.get("target_element") or "").strip().lower()
        if not content:
            return False
        for value in (
            hint.get("controlText"),
            hint.get("control_text"),
            hint.get("controlNodeName"),
            hint.get("control_node_name"),
        ):
            candidate = str(value or "").strip().lower()
            if len(candidate) >= 2 and (candidate in content or content in candidate):
                return True
        return False

    def _apply_ui_pin_coordinate_hints(self, *, pin_comments, screens):
        updated = []
        for pin in _as_list(pin_comments):
            if not isinstance(pin, dict):
                continue
            try:
                screen_index = int(pin.get("screenIndex", pin.get("screen_index", 0)))
            except (TypeError, ValueError):
                screen_index = 0
            screen = screens[screen_index] if 0 <= screen_index < len(screens) else {}
            hints = [
                hint
                for hint in _as_list(screen.get("interactionHints") or screen.get("interaction_hints"))
                if isinstance(hint, dict) and hint.get("centerX") is not None and hint.get("centerY") is not None
            ]
            if not hints:
                updated.append(pin)
                continue
            matched_hint = next((hint for hint in hints if self._pin_matches_interaction_hint(pin, hint)), None)
            should_use_hint = matched_hint is not None or not bool(pin.get("hasMarkerCoordinates") or pin.get("has_marker_coordinates"))
            if not should_use_hint:
                updated.append(pin)
                continue
            hint = matched_hint or hints[0]
            updated.append(
                {
                    **pin,
                    "x": _normalize_marker_percent(hint.get("centerX"), pin.get("x", 50)),
                    "y": _normalize_marker_percent(hint.get("centerY"), pin.get("y", 50)),
                    "hasMarkerCoordinates": True,
                    "has_marker_coordinates": True,
                    "coordinateSource": "figmaControlBounds",
                    "coordinate_source": "figma_control_bounds",
                    "controlNodeId": pin.get("controlNodeId") or hint.get("controlNodeId"),
                    "controlNodeName": pin.get("controlNodeName") or hint.get("controlNodeName"),
                    "controlText": pin.get("controlText") or hint.get("controlText"),
                }
            )
        return updated

    def _normalize_ui_pin_comments(self, *, feedback: dict, screens):
        pins = []
        max_screen_index = max(len(screens) - 1, 0)
        for index, item in enumerate(_as_list(feedback.get("pinComments") or feedback.get("pin_comments"))):
            if not isinstance(item, dict):
                continue
            screen_index = item.get("screenIndex", item.get("screen_index", 0))
            try:
                screen_index = int(screen_index)
            except (TypeError, ValueError):
                screen_index = 0
            screen_index = max(0, min(screen_index, max_screen_index))
            has_marker_coordinates = _has_marker_percent(item.get("x")) and _has_marker_percent(item.get("y"))
            pins.append(
                {
                    **item,
                    "screenIndex": screen_index,
                    "x": _normalize_marker_percent(item.get("x"), 42 + (index % 3) * 8),
                    "y": _normalize_marker_percent(item.get("y"), 38 + (index % 3) * 10),
                    "hasMarkerCoordinates": has_marker_coordinates,
                    "has_marker_coordinates": has_marker_coordinates,
                    "type": self._normalize_ui_pin_type(item.get("type")),
                    "content": str(item.get("content") or item.get("comment") or "").strip(),
                }
            )

        screen_insights = _as_list(feedback.get("screenInsights") or feedback.get("screen_insights"))
        strengths = [str(item).strip() for item in _as_list(feedback.get("strengths")) if str(item).strip()]
        risks = [str(item).strip() for item in _as_list(feedback.get("risks")) if str(item).strip()]

        for screen_index, screen in enumerate(screens or [{"id": "screen-1", "name": "화면 1"}]):
            screen_id = str(screen.get("id") or f"screen-{screen_index + 1}")
            insight = next(
                (
                    item
                    for item in screen_insights
                    if isinstance(item, dict) and str(item.get("screenId") or item.get("screen_id") or "") == screen_id
                ),
                screen_insights[screen_index] if screen_index < len(screen_insights) and isinstance(screen_insights[screen_index], dict) else None,
            )
            has_positive = any(pin.get("screenIndex") == screen_index and pin.get("type") == "praise" for pin in pins)
            has_negative = any(pin.get("screenIndex") == screen_index and pin.get("type") != "praise" for pin in pins)
            positive_text = None
            negative_text = None
            if insight:
                positive_text = next((str(item).strip() for item in _as_list(insight.get("positives")) if str(item).strip()), None)
                negative_text = next((str(item).strip() for item in _as_list(insight.get("issues")) if str(item).strip()), None)
            if not positive_text and screen_index == 0 and strengths:
                positive_text = strengths[0]
            if not negative_text and screen_index == 0 and risks:
                negative_text = risks[0]
            if not has_positive and positive_text:
                pins.append({"screenIndex": screen_index, "x": 36, "y": 34, "type": "praise", "content": positive_text})
            if not has_negative and negative_text:
                pins.append({"screenIndex": screen_index, "x": 62, "y": 54, "type": "improvement", "content": negative_text})

        return [pin for pin in pins if pin.get("content")]

    def _normalize_ui_screen_feedbacks(self, *, feedback: dict, persona, screens):
        feedback_payload = feedback.get("feedback") if isinstance(feedback.get("feedback"), dict) else {}
        existing = []
        for item in _as_list(feedback_payload.get("screenFeedbacks") or feedback_payload.get("screen_feedbacks")):
            if not isinstance(item, dict):
                continue
            try:
                screen_index = int(item.get("screenIndex", item.get("screen_index", 0)))
            except (TypeError, ValueError):
                screen_index = 0
            if screen_index < 0 or screen_index >= max(1, len(screens)):
                continue
            text = _first_text(item.get("feedback"), item.get("content"), item.get("comment"))
            if not text:
                continue
            existing.append({**item, "screenIndex": screen_index, "feedback": text})

        covered = {item["screenIndex"] for item in existing}
        persona_name = getattr(persona, "name", "이 퍼소나")
        for screen_index, screen in enumerate(screens):
            if screen_index in covered:
                continue
            screen_name = screen.get("name") or f"화면 {screen_index + 1}"
            existing.append(
                {
                    "screenIndex": screen_index,
                    "feedback": f"{persona_name}님은 {screen_name}에서 핵심 정보와 다음 행동이 자신의 판단 기준에 맞는지 먼저 확인했을 가능성이 큽니다.",
                }
            )
        return sorted(existing, key=lambda item: item["screenIndex"])

    def _normalize_ui_flow_analysis(self, *, feedback: dict, screens, is_flow: bool):
        if not is_flow:
            return []
        normalized = []
        for item in _as_list(feedback.get("flowAnalysis") or feedback.get("flow_analysis")):
            if not isinstance(item, dict):
                continue
            try:
                screen_index = int(item.get("screenIndex", item.get("screen_index", 0)))
            except (TypeError, ValueError):
                screen_index = 0
            if screen_index < 0 or screen_index >= max(1, len(screens)):
                continue
            normalized.append(
                {
                    **item,
                    "screenIndex": screen_index,
                    "confusionScore": int(item.get("confusionScore", item.get("confusion_score", 35)) or 35),
                    "dropoffRisk": int(item.get("dropoffRisk", item.get("dropoff_risk", 30)) or 30),
                    "suggestions": _as_list(item.get("suggestions")),
                    "transitionFromPrevious": item.get("transitionFromPrevious") or item.get("transition_from_previous"),
                    "expectedNextAction": item.get("expectedNextAction") or item.get("expected_next_action"),
                    "bottleneckRisk": item.get("bottleneckRisk") or item.get("bottleneck_risk") or "low",
                }
            )
        covered = {item["screenIndex"] for item in normalized}
        for screen_index, screen in enumerate(screens):
            if screen_index in covered:
                continue
            screen_name = screen.get("name") or f"화면 {screen_index + 1}"
            normalized.append(
                {
                    "screenIndex": screen_index,
                    "confusionScore": 35,
                    "dropoffRisk": 30,
                    "suggestions": [f"{screen_name}에서 목표 수행에 필요한 다음 행동과 상태 변화를 더 분명히 보여줍니다."],
                    "transitionFromPrevious": None if screen_index == 0 else f"이전 단계의 선택 결과가 {screen_name}에 이어지는지 확인해야 합니다.",
                    "expectedNextAction": f"{screen_name}의 핵심 CTA 또는 탐색 경로를 확인합니다.",
                    "bottleneckRisk": "low",
                }
            )
        return sorted(normalized, key=lambda item: item["screenIndex"])

    def _resolve_ui_screen_reference_index(self, item: dict, screens, fallback_index: int = 0):
        screen_id = item.get("screenId") or item.get("screen_id")
        if screen_id is not None:
            matched_index = next(
                (
                    index
                    for index, screen in enumerate(screens)
                    if str(screen.get("id") or f"screen-{index + 1}") == str(screen_id)
                ),
                None,
            )
            if matched_index is not None:
                return matched_index
        raw_index = item.get("screenIndex", item.get("screen_index", fallback_index))
        try:
            screen_index = int(raw_index)
        except (TypeError, ValueError):
            screen_index = fallback_index
        return max(0, min(screen_index, max(len(screens) - 1, 0)))

    def _score_value(self, source: dict, keys, fallback: int):
        for key in keys:
            if source.get(key) is not None:
                return _clamp_percent(source.get(key), fallback)
        return _clamp_percent(fallback, 0)

    def _normalize_ui_screen_scores(self, *, feedback: dict, screens, scores: dict, flow_analysis):
        base = {
            "clarity": 50,
            "usability": 50,
            "appeal": 50,
            "overall": 50,
        }
        existing = _as_list(
            scores.get("screenScores")
            or scores.get("screen_scores")
            or feedback.get("screenScores")
            or feedback.get("screen_scores")
        )
        normalized_by_index = {}
        for fallback_index, item in enumerate(existing):
            if not isinstance(item, dict):
                continue
            screen_index = self._resolve_ui_screen_reference_index(item, screens, fallback_index)
            screen = screens[screen_index] if screen_index < len(screens) else {}
            clarity = self._score_value(item, ("clarity", "clear", "readability"), base["clarity"])
            usability = self._score_value(item, ("usability", "ease"), base["usability"])
            appeal = self._score_value(item, ("appeal", "satisfaction", "score"), base["appeal"])
            normalized_by_index[screen_index] = {
                **item,
                "screenIndex": screen_index,
                "screenId": str(screen.get("id") or item.get("screenId") or item.get("screen_id") or f"screen-{screen_index + 1}"),
                "clarity": clarity,
                "usability": usability,
                "appeal": appeal,
                "satisfaction": appeal,
                "overall": self._score_value(item, ("overall", "overallScore", "overall_score"), round((clarity + usability + appeal) / 3)),
            }

        flow_by_index = {item.get("screenIndex"): item for item in _as_list(flow_analysis) if isinstance(item, dict)}
        for screen_index, screen in enumerate(screens):
            if screen_index in normalized_by_index:
                continue
            flow_item = flow_by_index.get(screen_index) or {}
            clarity = 100 - _clamp_percent(flow_item.get("confusionScore"), 100 - base["clarity"]) if flow_item else base["clarity"]
            usability = 100 - _clamp_percent(flow_item.get("dropoffRisk"), 100 - base["usability"]) if flow_item else base["usability"]
            appeal = base["appeal"]
            normalized_by_index[screen_index] = {
                "screenIndex": screen_index,
                "screenId": str(screen.get("id") or f"screen-{screen_index + 1}"),
                "clarity": _clamp_percent(clarity, base["clarity"]),
                "usability": _clamp_percent(usability, base["usability"]),
                "appeal": appeal,
                "satisfaction": appeal,
                "overall": round((_clamp_percent(clarity, base["clarity"]) + _clamp_percent(usability, base["usability"]) + appeal) / 3),
            }

        return [normalized_by_index[index] for index in sorted(normalized_by_index)]

    def _normalize_ui_screen_insights(self, *, feedback: dict, screens, pin_comments, screen_feedbacks):
        existing = _as_list(feedback.get("screenInsights") or feedback.get("screen_insights"))
        insights = []
        for screen_index, screen in enumerate(screens):
            screen_id = str(screen.get("id") or f"screen-{screen_index + 1}")
            source = next(
                (
                    item
                    for item in existing
                    if isinstance(item, dict) and str(item.get("screenId") or item.get("screen_id") or "") == screen_id
                ),
                existing[screen_index] if screen_index < len(existing) and isinstance(existing[screen_index], dict) else {},
            )
            positives = [str(item).strip() for item in _as_list(_as_dict(source).get("positives")) if str(item).strip()]
            issues = [str(item).strip() for item in _as_list(_as_dict(source).get("issues")) if str(item).strip()]
            positives.extend(
                pin["content"]
                for pin in pin_comments
                if pin.get("screenIndex") == screen_index and pin.get("type") == "praise" and pin.get("content")
            )
            issues.extend(
                pin["content"]
                for pin in pin_comments
                if pin.get("screenIndex") == screen_index and pin.get("type") != "praise" and pin.get("content")
            )
            if not positives and screen_feedbacks:
                text = next((item["feedback"] for item in screen_feedbacks if item.get("screenIndex") == screen_index), None)
                if text:
                    positives.append(text)
            insights.append(
                {
                    "screenId": screen_id,
                    "name": screen.get("name") or f"화면 {screen_index + 1}",
                    "positives": list(dict.fromkeys(positives))[:3],
                    "issues": list(dict.fromkeys(issues))[:3],
                    "recommendation": _as_dict(source).get("recommendation")
                    or (issues[0] if issues else "퍼소나의 판단 기준에 맞는 핵심 근거를 더 명확히 보여줍니다."),
                }
            )
        return insights

    def _fallback_ui_summary_feedback(self, *, persona, screens, screen_feedbacks, pin_comments, flow_analysis, is_flow: bool):
        persona_name = getattr(persona, "name", None) or "이 퍼소나"
        if is_flow:
            pin_items = [str(comment.get("content") or "").strip() for comment in _as_list(pin_comments) if isinstance(comment, dict) and comment.get("type") != "praise"]
            overall = " ".join([item for item in pin_items if item][:2]) or "저는 전체 흐름에서 다음 단계로 넘어가야 하는 이유와 버튼의 역할이 더 분명해야 목표를 끝까지 수행할 수 있을 것 같아요."
            flow_summary = (
                f"{' '.join([item for item in pin_items if item][:2])} 이런 지점 때문에 흐름을 따라가는 과정에서 잠깐 멈칫할 수 있어요."
                if any(pin_items)
                else "전체 플로우에서 사용자가 다음 행동을 바로 이해할 수 있는지 추가로 확인해야 해요."
            )
            return {"scores": {}, "overallFeedback": overall, "flowSummary": flow_summary, "overallFlowScore": None}

        negative = [str(comment.get("content") or "").strip() for comment in _as_list(pin_comments) if isinstance(comment, dict) and comment.get("type") != "praise"]
        positive = [str(comment.get("content") or "").strip() for comment in _as_list(pin_comments) if isinstance(comment, dict) and comment.get("type") == "praise"]
        feedback = [str(entry.get("feedback") or "").strip() for entry in _as_list(screen_feedbacks) if isinstance(entry, dict)]
        negative_summary = (
            f"{' '.join([item for item in negative if item][:2])}라는 점에서 부정적으로 평가했습니다."
            if any(negative)
            else f"{' '.join([item for item in feedback if item][:1])}라는 반응을 보였습니다."
            if any(feedback)
            else "핵심 정보와 다음 행동이 더 분명해야 한다고 평가했습니다."
        )
        positive_summary = (
            f"반면 {' '.join([item for item in positive if item][:1])}라는 점은 긍정적으로 봤습니다."
            if any(positive)
            else "긍정 근거는 하단 코멘트에서 충분히 확인되지 않았습니다."
        )
        return {
            "scores": {"clarity": 50, "usability": 50, "appeal": 50},
            "overallFeedback": f"{persona_name}님은 {negative_summary} {positive_summary}",
            "flowSummary": None,
            "overallFlowScore": None,
        }

    def _build_ui_summary_prompt(self, *, test, persona, screens, screen_feedbacks, pin_comments, flow_analysis, persona_pack: Optional[dict] = None):
        context = self._ui_test_source_context(test)
        persona_name = getattr(persona, "name", None) or "이 퍼소나"
        return build_ui_summary_prompt(
            test_name=test.name,
            test_description=test.description,
            scope_type=test.scope_type,
            source_type=context["source_type"],
            device_type=context["device_type"],
            persona_name=persona_name,
            persona_context=self._persona_context(persona, persona_pack=persona_pack),
            screens=screens,
            screen_feedbacks=screen_feedbacks,
            pin_comments=pin_comments,
            flow_analysis=flow_analysis,
        )

    def _run_ui_summary_feedback(self, *, company_id: int, user_id: int, test, persona, screens, screen_feedbacks, pin_comments, flow_analysis, persona_pack: Optional[dict] = None):
        is_flow = test.scope_type == "flow" and len(screens) > 1
        fallback = self._fallback_ui_summary_feedback(
            persona=persona,
            screens=screens,
            screen_feedbacks=screen_feedbacks,
            pin_comments=pin_comments,
            flow_analysis=flow_analysis,
            is_flow=is_flow,
        )
        prompt = self._build_ui_summary_prompt(
            test=test,
            persona=persona,
            screens=screens,
            screen_feedbacks=screen_feedbacks,
            pin_comments=pin_comments,
            flow_analysis=flow_analysis,
            persona_pack=persona_pack,
        )
        try:
            parsed, usage = self._generate_json(
                prompt,
                feature_key="persona_ui_test",
                company_id=company_id,
                user_id=user_id,
            )
        except ValueError as exc:
            return {
                "feedback": fallback,
                "rawText": json.dumps({"promptVersion": "persona_test_v2", "error": str(exc), "stage": "summary"}, ensure_ascii=False),
                "usage": {"model": "fallback", "stage": "persona_ui_test"},
            }

        source = _as_dict(parsed)
        scores = _as_dict(source.get("scores"))
        normalized = {
            "scores": {
                "clarity": self._score_value(scores, ("clarity",), fallback["scores"].get("clarity", 50)),
                "usability": self._score_value(scores, ("usability",), fallback["scores"].get("usability", 50)),
                "appeal": self._score_value(scores, ("appeal",), fallback["scores"].get("appeal", 50)),
            }
            if not is_flow
            else {},
            "overallFeedback": _first_text(source.get("overallFeedback"), source.get("overall_feedback"), source.get("summary"), fallback["overallFeedback"]),
            "flowSummary": _first_text(source.get("flowSummary"), source.get("flow_summary"), fallback.get("flowSummary")) if is_flow else None,
            "overallFlowScore": _clamp_percent(source.get("overallFlowScore"), fallback.get("overallFlowScore") or 0)
            if is_flow and source.get("overallFlowScore") is not None
            else fallback.get("overallFlowScore"),
        }
        return {"feedback": normalized, "rawText": json.dumps(parsed, ensure_ascii=False), "usage": usage}

    def _build_ui_scoring_prompt(self, *, test, persona, screens, screen_feedbacks, pin_comments, flow_analysis, persona_pack: Optional[dict] = None):
        return build_ui_scoring_prompt(
            test_name=test.name,
            test_description=test.description,
            scope_type=test.scope_type,
            persona_context=self._persona_context(persona, persona_pack=persona_pack),
            screens=screens,
            screen_feedbacks=screen_feedbacks,
            pin_comments=pin_comments,
            flow_analysis=flow_analysis,
        )

    def _run_ui_scoring_analysis(self, *, company_id: int, user_id: int, test, persona, screens, screen_feedbacks, pin_comments, flow_analysis, persona_pack: Optional[dict] = None):
        prompt = self._build_ui_scoring_prompt(
            test=test,
            persona=persona,
            screens=screens,
            screen_feedbacks=screen_feedbacks,
            pin_comments=pin_comments,
            flow_analysis=flow_analysis,
            persona_pack=persona_pack,
        )
        try:
            parsed, usage = self._generate_json(
                prompt,
                feature_key="persona_ui_test_scoring",
                company_id=company_id,
                user_id=user_id,
            )
        except ValueError as exc:
            return {
                "keyElements": [],
                "analysisEvents": [],
                "rawText": json.dumps(
                    {
                        "promptVersion": "persona_test_v2",
                        "error": str(exc),
                        "stage": "scoring_analysis",
                    },
                    ensure_ascii=False,
                ),
                "usage": {"model": "fallback", "stage": "persona_ui_test_scoring"},
            }
        normalized = _normalize_ui_scoring_analysis(parsed)
        return {
            **normalized,
            "rawText": json.dumps(parsed, ensure_ascii=False),
            "usage": usage,
        }

    def _run_ui_persona_evaluation(self, *, company_id: int, user_id: int, test, persona, screens, media_parts: Optional[list[dict]] = None, persona_pack: Optional[dict] = None):
        is_flow = test.scope_type == "flow" and len(screens) > 1
        chunk_config = {"size": 2, "overlap": 1} if is_flow else {"size": 2, "overlap": 0}
        screen_chunks = _build_ui_screen_chunks(len(screens), chunk_config["size"], chunk_config["overlap"])
        def run_chunk(screen_indices):
            return self._run_ui_chunk_feedback(
                company_id=company_id,
                user_id=user_id,
                test=test,
                persona=persona,
                screens=screens,
                screen_indices=screen_indices,
                media_parts=media_parts,
                persona_pack=persona_pack,
            )

        if len(screen_chunks) <= 1:
            chunk_results = [run_chunk(screen_indices) for screen_indices in screen_chunks]
        else:
            max_workers = min(len(screen_chunks), 2 if is_flow else 3)
            chunk_results = [None] * len(screen_chunks)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ui-test-chunk") as executor:
                future_to_index = {executor.submit(run_chunk, screen_indices): index for index, screen_indices in enumerate(screen_chunks)}
                for future in concurrent.futures.as_completed(future_to_index):
                    chunk_results[future_to_index[future]] = future.result()
            chunk_results = [result for result in chunk_results if result is not None]
        screen_feedbacks = _merge_ui_screen_feedbacks([], [item for result in chunk_results for item in _as_list(_as_dict(result.get("feedback")).get("screenFeedbacks"))])
        pin_comments = _merge_ui_pin_comments([], [item for result in chunk_results for item in _as_list(_as_dict(result.get("feedback")).get("pinComments"))])
        flow_analysis = _merge_ui_flow_analysis([], [item for result in chunk_results for item in _as_list(_as_dict(result.get("feedback")).get("flowAnalysis"))])

        repair_raw_texts = []
        all_screen_indices = list(range(len(screens)))
        missing_feedback_indices = [index for index in all_screen_indices if not any(item.get("screenIndex") == index for item in screen_feedbacks)]
        if missing_feedback_indices:
            repair = self._run_ui_chunk_feedback(
                company_id=company_id,
                user_id=user_id,
                test=test,
                persona=persona,
                screens=screens,
                screen_indices=missing_feedback_indices,
                media_parts=media_parts,
                persona_pack=persona_pack,
                repair_mode=True,
            )
            repair_raw_texts.append(repair.get("rawText"))
            repair_feedback = _as_dict(repair.get("feedback"))
            screen_feedbacks = _merge_ui_screen_feedbacks(screen_feedbacks, repair_feedback.get("screenFeedbacks"))
            pin_comments = _merge_ui_pin_comments(pin_comments, repair_feedback.get("pinComments"))
            flow_analysis = _merge_ui_flow_analysis(flow_analysis, repair_feedback.get("flowAnalysis"))

        missing_pin_indices = [index for index in all_screen_indices if not any(item.get("screenIndex") == index for item in pin_comments)]
        if not is_flow and missing_pin_indices:
            repair = self._run_ui_chunk_feedback(
                company_id=company_id,
                user_id=user_id,
                test=test,
                persona=persona,
                screens=screens,
                screen_indices=missing_pin_indices,
                media_parts=media_parts,
                persona_pack=persona_pack,
                repair_mode=True,
            )
            repair_raw_texts.append(repair.get("rawText"))
            repair_feedback = _as_dict(repair.get("feedback"))
            screen_feedbacks = _merge_ui_screen_feedbacks(screen_feedbacks, repair_feedback.get("screenFeedbacks"))
            pin_comments = _merge_ui_pin_comments(pin_comments, repair_feedback.get("pinComments"))

        if is_flow:
            missing_flow_indices = [index for index in all_screen_indices if not any(item.get("screenIndex") == index for item in flow_analysis)]
            if missing_flow_indices:
                repair = self._run_ui_chunk_feedback(
                    company_id=company_id,
                    user_id=user_id,
                    test=test,
                    persona=persona,
                    screens=screens,
                    screen_indices=missing_flow_indices,
                    media_parts=media_parts,
                    persona_pack=persona_pack,
                    repair_mode=True,
                )
                repair_raw_texts.append(repair.get("rawText"))
                repair_feedback = _as_dict(repair.get("feedback"))
                screen_feedbacks = _merge_ui_screen_feedbacks(screen_feedbacks, repair_feedback.get("screenFeedbacks"))
                pin_comments = _merge_ui_pin_comments(pin_comments, repair_feedback.get("pinComments"))
                flow_analysis = _merge_ui_flow_analysis(flow_analysis, repair_feedback.get("flowAnalysis"))
        else:
            flow_analysis = []

        remaining_feedback_indices = [index for index in all_screen_indices if not any(item.get("screenIndex") == index for item in screen_feedbacks)]
        if remaining_feedback_indices:
            fallback = self._fallback_ui_chunk_feedback(screens=screens, screen_indices=remaining_feedback_indices, is_flow=is_flow)
            screen_feedbacks = _merge_ui_screen_feedbacks(screen_feedbacks, fallback.get("screenFeedbacks"))

        remaining_pin_indices = [index for index in all_screen_indices if not any(item.get("screenIndex") == index for item in pin_comments)]
        if not is_flow and remaining_pin_indices:
            fallback = self._fallback_ui_chunk_feedback(screens=screens, screen_indices=remaining_pin_indices, is_flow=False)
            pin_comments = _merge_ui_pin_comments(pin_comments, fallback.get("pinComments"))

        if is_flow:
            remaining_flow_indices = [index for index in all_screen_indices if not any(item.get("screenIndex") == index for item in flow_analysis)]
            if remaining_flow_indices:
                fallback = self._fallback_ui_chunk_feedback(screens=screens, screen_indices=remaining_flow_indices, is_flow=True)
                flow_analysis = _merge_ui_flow_analysis(flow_analysis, fallback.get("flowAnalysis"))
        pin_comments = self._apply_ui_pin_coordinate_hints(pin_comments=pin_comments, screens=screens)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ui-test-summary") as executor:
            summary_future = executor.submit(
                self._run_ui_summary_feedback,
                company_id=company_id,
                user_id=user_id,
                test=test,
                persona=persona,
                screens=screens,
                screen_feedbacks=screen_feedbacks,
                pin_comments=pin_comments,
                flow_analysis=flow_analysis,
                persona_pack=persona_pack,
            )
            scoring_future = executor.submit(
                self._run_ui_scoring_analysis,
                company_id=company_id,
                user_id=user_id,
                test=test,
                persona=persona,
                screens=screens,
                screen_feedbacks=screen_feedbacks,
                pin_comments=pin_comments,
                flow_analysis=flow_analysis,
                persona_pack=persona_pack,
            )
            summary_result = summary_future.result()
            scoring_analysis = scoring_future.result()
        summary_feedback = _as_dict(summary_result.get("feedback"))
        scores = _as_dict(summary_feedback.get("scores"))
        summary = summary_feedback.get("overallFeedback") or "UI test run completed"
        feedback = {
            "feedback": {"overallFeedback": summary, "screenFeedbacks": screen_feedbacks},
            "pinComments": pin_comments,
            "flowAnalysis": flow_analysis,
            "screenInsights": [],
        }
        screen_scores = self._normalize_ui_screen_scores(
            feedback=feedback,
            screens=screens,
            scores=scores,
            flow_analysis=flow_analysis,
        )
        analysis_events = scoring_analysis.get("analysisEvents") or []
        flow_analysis = _apply_structured_flow_analysis_scores(
            flow_analysis=flow_analysis,
            analysis_events=analysis_events,
        )
        screen_scores = _apply_structured_screen_scores(
            screen_scores=screen_scores,
            analysis_events=analysis_events,
        )
        structured_scores = _apply_structured_scoring(
            fallback_scores={
                "clarity": self._score_value(scores, ("clarity", "clear", "readability"), 70),
                "usability": self._score_value(scores, ("usability", "ease"), 70),
                "appeal": self._score_value(scores, ("appeal", "satisfaction", "score"), 65),
                "overall": self._score_value(scores, ("overall", "overallFlowScore", "overall_flow_score"), 68),
                "overallFlowScore": scores.get("overallFlowScore"),
            },
            analysis_events=analysis_events,
            is_flow_test=is_flow,
            flow_step_count=len(screens),
            flow_analysis=flow_analysis,
        )
        screen_insights = self._normalize_ui_screen_insights(
            feedback=feedback,
            screens=screens,
            pin_comments=pin_comments,
            screen_feedbacks=screen_feedbacks,
        )
        feedback_payload = feedback.get("feedback") if isinstance(feedback.get("feedback"), dict) else {}
        feedback_payload = {
            **feedback_payload,
            "overallFeedback": feedback_payload.get("overallFeedback") or summary,
            "screenFeedbacks": screen_feedbacks,
        }
        return {
            "summary": summary,
            "persona_goal_fit": feedback.get("personaGoalFit") or feedback.get("persona_goal_fit"),
            "scores": {
                "clarity": structured_scores["clarity"],
                "usability": structured_scores["usability"],
                "appeal": structured_scores["appeal"],
                "overall": structured_scores["overall"],
                "overallFlowScore": structured_scores.get("overallFlowScore"),
                "flowSummary": summary_feedback.get("flowSummary"),
                "screenScores": screen_scores,
            },
            "feedback": feedback_payload,
            "pin_comments": pin_comments,
            "flow_analysis": flow_analysis,
            "persona_snapshot": self._persona_snapshot_payload(persona),
            "confidence": {
                "model": _as_dict(summary_result.get("usage")).get("model"),
                "promptVersion": "persona_test_v2",
                "personaPackVersion": PERSONA_INTERVIEW_PACK_VERSION if persona_pack else None,
                "scoreVersion": "comment_weighted_v1",
                "scoringModel": _as_dict(scoring_analysis.get("usage")).get("model") if isinstance(scoring_analysis, dict) else None,
                "scoringEventCounts": {
                    metric: len([event for event in _as_list(scoring_analysis.get("analysisEvents")) if isinstance(event, dict) and event.get("metric") == metric])
                    for metric in SCORING_METRICS
                },
                "screenCoverage": {
                    "screens": len(screens),
                    "screenFeedbacks": len({item.get("screenIndex") for item in screen_feedbacks}),
                    "pinComments": len(pin_comments),
                    "flowAnalysis": len({item.get("screenIndex") for item in flow_analysis}),
                    "imageEvidenceScreens": len({part.get("screenIndex") for part in _as_list(media_parts) if isinstance(part, dict) and part.get("type") == "image"}),
                },
            },
            "evidence_ids": ["promptVersion:persona_test_v2"],
            "strengths": _as_list(feedback.get("strengths")),
            "risks": _as_list(feedback.get("risks")),
            "recommendations": _as_list(feedback.get("recommendations")),
            "screen_insights": screen_insights,
            "raw_response": {
                "parsed": feedback,
                "summary": summary_result.get("rawText"),
                "chunks": [item.get("rawText") for item in chunk_results],
                "repairs": [item for item in repair_raw_texts if item],
                "usage": {
                    "chunks": [item.get("usage") for item in chunk_results],
                    "summary": summary_result.get("usage"),
                },
                "scoringAnalysis": scoring_analysis,
            },
        }

    def _run_ui_evaluations_for_personas(self, *, company_id: int, user_id: int, test, personas, screens, media_parts, persona_packs=None):
        personas = list(personas or [])
        persona_packs = list(persona_packs or [])
        if not personas:
            return []
        max_workers = _resolve_ui_test_max_concurrency(len(personas))
        detached_test = self._detach_ui_test_for_parallel(test)
        detached_personas = [self._detach_persona_for_ui_test(persona) for persona in personas]
        if max_workers == 1:
            return [
                self._run_ui_persona_evaluation(
                    company_id=company_id,
                    user_id=user_id,
                    test=detached_test,
                    persona=detached_persona,
                    screens=screens,
                    media_parts=media_parts,
                    persona_pack=persona_packs[index] if index < len(persona_packs) else None,
                )
                for index, detached_persona in enumerate(detached_personas)
            ]

        results = [None] * len(detached_personas)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="persona-ui-test") as executor:
            future_to_index = {
                executor.submit(
                    self._run_ui_persona_evaluation,
                    company_id=company_id,
                    user_id=user_id,
                    test=detached_test,
                    persona=detached_persona,
                    screens=screens,
                    media_parts=media_parts,
                    persona_pack=persona_packs[index] if index < len(persona_packs) else None,
                ): index
                for index, detached_persona in enumerate(detached_personas)
            }
            for future in concurrent.futures.as_completed(future_to_index):
                results[future_to_index[future]] = future.result()
        return results

    def _build_ab_prompt(self, *, test, persona):
        return "\n".join(
            [
                "You are comparing A/B UX variants from the perspective of the given persona.",
                "Return only JSON with keys: scores, feedback. scores must include winner(A/B/tie) and reasonForChoice.",
                "For flow tests include journeyComparison and stepAnalysis inside scores.",
                f"Test: {test.name}",
                f"Purpose: {test.purpose or ''}",
                f"Mode: {test.mode}",
                f"Screens: {json.dumps(test.screens or {}, ensure_ascii=False)}",
                f"Context: {json.dumps(test.context_data or {}, ensure_ascii=False)}",
                "[Persona]",
                self._persona_context(persona),
            ]
        )

    def _fallback_ab_feedback(self, *, test, persona):
        persona_name = getattr(persona, "name", "Persona")
        is_flow = test.mode == "flow"
        scores = {
            "winner": "tie",
            "reasonForChoice": f"{persona_name}님 관점에서 두 안 모두 장단점이 있어 명확한 우위를 판단하기 어렵습니다.",
        }
        if is_flow:
            scores.update(
                {
                    "journeyComparison": {
                        "flowARating": 65,
                        "flowBRating": 65,
                        "goalAchievementEase": {"flowA": 65, "flowB": 65},
                        "navigationConfidence": {"flowA": 65, "flowB": 65},
                        "estimatedCompletionSpeed": "same",
                        "criticalDropoffStep": {"flowA": None, "flowB": None},
                    },
                    "stepAnalysis": [],
                    "overallFeedback": "두 플로우 모두 목표 수행은 가능하지만 확신을 높일 근거가 더 필요합니다.",
                }
            )
        return {"scores": scores, "feedback": ["A안과 B안 모두 핵심 정보를 비교할 수 있지만 결정적 차이는 약합니다."]}

    def _normalize_ab_scores(self, scores, *, mode: str):
        if not isinstance(scores, dict):
            scores = {}
        normalized = {
            **scores,
            "winner": scores.get("winner") if scores.get("winner") in {"A", "B", "tie"} else "tie",
            "reasonForChoice": _first_text(scores.get("reasonForChoice"), scores.get("reason_for_choice")) or "비교 평가가 완료되었습니다.",
        }
        if mode != "flow":
            return normalized

        journey = scores.get("journeyComparison") or scores.get("journey_comparison")
        normalized["journeyComparison"] = journey if isinstance(journey, dict) else {}

        step_analysis = []
        for step in _as_list(scores.get("stepAnalysis") or scores.get("step_analysis")):
            if not isinstance(step, dict):
                continue
            try:
                step_index = int(step.get("stepIndex", step.get("step_index", 0)) or 0)
            except (TypeError, ValueError):
                step_index = 0
            preferred = step.get("preferredVersion") or step.get("preferred_version")
            step_analysis.append(
                {
                    **step,
                    "stepIndex": step_index,
                    "preferredVersion": preferred if preferred in {"A", "B", "tie"} else "tie",
                }
            )
        normalized["stepAnalysis"] = step_analysis
        return normalized

    def _run_ab_persona_evaluation(self, *, company_id: int, user_id: int, test, persona):
        try:
            parsed, usage = self._generate_json(
                self._build_ab_prompt(test=test, persona=persona),
                feature_key="persona_ab_test",
                company_id=company_id,
                user_id=user_id,
            )
        except ValueError:
            parsed, usage = self._fallback_ab_feedback(test=test, persona=persona), {"model": "fallback"}
        feedback = parsed if isinstance(parsed, dict) else self._fallback_ab_feedback(test=test, persona=persona)
        scores = feedback.get("scores") if isinstance(feedback.get("scores"), dict) else self._fallback_ab_feedback(test=test, persona=persona)["scores"]
        scores = self._normalize_ab_scores(scores, mode=test.mode)
        return {
            "persona_snapshot": self._persona_snapshot_payload(persona),
            "scores": scores,
            "feedback": _as_list(feedback.get("feedback")) or [scores.get("reasonForChoice", "비교 평가가 완료되었습니다.")],
            "confidence": {"model": usage.get("model"), "promptVersion": "persona_test_v2"},
            "evidence_ids": ["promptVersion:persona_test_v2"],
            "raw_response": {"parsed": feedback, "usage": usage},
        }

    def _ab_summary(self, results: list[dict], mode: str):
        total = len(results)
        normalized_scores = [
            self._normalize_ab_scores(result.get("scores") if isinstance(result, dict) else None, mode=mode)
            for result in results
        ]
        vote_a = sum(1 for scores in normalized_scores if scores.get("winner") == "A")
        vote_b = sum(1 for scores in normalized_scores if scores.get("winner") == "B")
        winner = "tie"
        if vote_a > vote_b:
            winner = "A"
        elif vote_b > vote_a:
            winner = "B"
        summary = {
            "voteA": vote_a,
            "voteB": vote_b,
            "percentA": round((vote_a / total) * 100) if total else 0,
            "percentB": round((vote_b / total) * 100) if total else 0,
            "totalVotes": total,
            "winner": winner,
        }
        if mode == "flow":
            flow_a = []
            flow_b = []
            step_votes = {}
            for scores in normalized_scores:
                journey = scores.get("journeyComparison") or {}
                if isinstance(journey.get("flowARating"), (int, float)):
                    flow_a.append(journey["flowARating"])
                if isinstance(journey.get("flowBRating"), (int, float)):
                    flow_b.append(journey["flowBRating"])
                for step in _as_list(scores.get("stepAnalysis")):
                    if not isinstance(step, dict):
                        continue
                    index = int(step.get("stepIndex") or 0)
                    step_votes.setdefault(index, {"stepIndex": index, "voteA": 0, "voteB": 0, "voteTie": 0})
                    preferred = step.get("preferredVersion")
                    if preferred == "A":
                        step_votes[index]["voteA"] += 1
                    elif preferred == "B":
                        step_votes[index]["voteB"] += 1
                    else:
                        step_votes[index]["voteTie"] += 1
            summary["flowMetrics"] = {
                "avgFlowARating": round(sum(flow_a) / len(flow_a)) if flow_a else 0,
                "avgFlowBRating": round(sum(flow_b) / len(flow_b)) if flow_b else 0,
                "stepPreferences": list(step_votes.values()),
            }
        return summary

    def _fallback_interview_question_set(self, *, goal: str, product_description: str | None, length: str):
        target_count = 14 if length == "deep" else 10 if length == "standard" else 7
        base_questions = [
            "이 제품이나 서비스에서 가장 먼저 확인하고 싶은 점은 무엇인가요?",
            "사용 과정에서 불안하거나 망설일 만한 부분은 무엇인가요?",
            "현재 생활 맥락에서 가장 유용하게 느껴질 조건은 무엇인가요?",
            "개선된다면 더 신뢰하거나 자주 사용할 부분은 무엇인가요?",
            "비슷한 대안을 고를 때 비교하는 기준은 무엇인가요?",
            "주변 사람에게 추천하거나 말릴 상황은 언제인가요?",
            "마지막으로 꼭 전달하고 싶은 기대나 우려는 무엇인가요?",
            "처음 이 서비스를 접했을 때 어떤 정보가 가장 먼저 눈에 들어와야 하나요?",
            "가입이나 사용을 포기하게 만들 수 있는 조건은 무엇인가요?",
            "비용, 혜택, 신뢰성 중 어떤 기준을 가장 중요하게 보나요?",
            "비슷한 경험이 있다면 어떤 점이 만족스럽거나 불편했나요?",
            "주변 상황이나 가족/동료의 의견이 판단에 어떤 영향을 주나요?",
            "더 자세히 확인하고 싶은 기능이나 조건은 무엇인가요?",
            "최종적으로 이 서비스를 쓰겠다고 판단하려면 무엇이 필요할까요?",
        ]
        selected = base_questions[:target_count]
        return {
            "opening": selected[:2],
            "tasks": [
                {
                    "title": "핵심 이용 맥락",
                    "questions": selected[2:-1],
                }
            ],
            "closing": selected[-1:],
            "followup_strategies": [
                "답변 속 판단 기준, 망설임, 이전 경험을 기준으로 한 단계 더 구체적인 상황을 물어봅니다.",
            ],
        }

    def _normalize_interview_question_set(self, value, *, goal: str | None = None, product_description: str | None = None, length: str = "quick"):
        if not isinstance(value, dict):
            return self._fallback_interview_question_set(goal=goal or "", product_description=product_description, length=length)

        opening = [str(item).strip() for item in _as_list(value.get("opening")) if str(item).strip()]
        tasks = []
        for item in _as_list(value.get("tasks")):
            if not isinstance(item, dict):
                continue
            questions = [str(question).strip() for question in _as_list(item.get("questions")) if str(question).strip()]
            if questions:
                tasks.append({"title": str(item.get("title") or "질문 영역").strip() or "질문 영역", "questions": questions})
        closing = [str(item).strip() for item in _as_list(value.get("closing")) if str(item).strip()]
        followups = [str(item).strip() for item in _as_list(value.get("followup_strategies")) if str(item).strip()]

        legacy_questions = []
        for item in _as_list(value.get("questions")):
            if isinstance(item, dict):
                text = _first_text(item.get("text"), item.get("question"), item.get("label"))
            else:
                text = str(item).strip()
            if text:
                legacy_questions.append(text)
        if not opening and not tasks and not closing and legacy_questions:
            return {
                "opening": [],
                "tasks": [{"title": "질문 영역", "questions": legacy_questions}],
                "closing": [],
                "followup_strategies": followups,
            }

        normalized = {
            "opening": opening,
            "tasks": tasks,
            "closing": closing,
            "followup_strategies": followups,
        }
        if not self._flatten_interview_questions(normalized):
            return self._fallback_interview_question_set(goal=goal or "", product_description=product_description, length=length)
        return normalized

    def _flatten_interview_questions(self, question_set) -> list[str]:
        normalized = question_set if isinstance(question_set, dict) else {}
        questions = [str(item).strip() for item in _as_list(normalized.get("opening")) if str(item).strip()]
        for task in _as_list(normalized.get("tasks")):
            if isinstance(task, dict):
                questions.extend(str(item).strip() for item in _as_list(task.get("questions")) if str(item).strip())
        questions.extend(str(item).strip() for item in _as_list(normalized.get("closing")) if str(item).strip())
        if not questions:
            for item in _as_list(normalized.get("questions")):
                if isinstance(item, dict):
                    text = _first_text(item.get("text"), item.get("question"), item.get("label"))
                else:
                    text = str(item).strip()
                if text:
                    questions.append(text)
        return questions

    def _interview_question_prompt(self, *, name: str | None, goal: str, product_description: str | None, length: str):
        target_count = 14 if length == "deep" else 10 if length == "standard" else 7
        return f"""
ReOps 1:1 AI 인터뷰 목표 화면에 들어갈 질문 세트를 생성하세요.

[인터뷰 정보]
- 이름: {name or "(미정)"}
- 목표: {goal}
- 서비스/제품 설명: {product_description or "(없음)"}
- 길이: {length}

[작성 규칙]
- 질문은 위 인터뷰 이름, 목표, 서비스/제품 설명에 직접 맞춰 실제 1:1 심층인터뷰에서 바로 사용할 수 있게 작성하세요.
- 총 질문 수는 오프닝, 핵심 과업, 마무리를 합쳐 약 {target_count}문항으로 작성하세요.
- 출력은 전체 진행 스크립트가 아니라, 실제 진행자가 바로 읽고 질문할 수 있는 "질문지"입니다.
- 질문 문장은 딱딱한 키워드가 아니라 실제 발화형으로 작성하세요.
- 흐름은 도입/웜업/핵심 질문/마무리의 구조를 따르되, JSON에는 opening, tasks, closing으로만 나눠 담으세요.
- opening은 1~2문항으로 긴장을 풀고, 현재 서비스/제품과 관련된 평소 이용 행태나 기준선을 파악하게 하세요.
- tasks는 2~4개 주제 영역으로 나누고 핵심 질문을 단계적으로 파고들어야 합니다.
- closing은 1문항으로 가장 중요한 개선점, 최종 니즈, 추가 의견을 회수하세요.
- 질문은 사용자의 실제 경험, 판단 기준, 불편, 니즈, UX/UI 접점, 정보 탐색 과정, 망설임, 포기 조건을 끌어낼 수 있어야 합니다.
- 한 번에 여러 개를 묻는 복합 질문, 유도 질문, "좋나요/싫나요"처럼 얕은 질문은 피하세요.
- 후속 전략은 질문이 아니라 모더레이터 메모처럼 작성하세요.

[응답 JSON]
{{
  "opening": ["오프닝 질문"],
  "tasks": [{{"title": "영역명", "questions": ["핵심 질문"]}}],
  "closing": ["마무리 질문"],
  "followup_strategies": ["후속 질문 전략"]
}}
""".strip()

    def _generate_interview_question_set(
        self,
        *,
        company_id: int,
        user_id: int,
        name: str | None,
        goal: str,
        product_description: str | None,
        length: str,
        model_override: str | None = None,
    ):
        prompt = self._interview_question_prompt(name=name, goal=goal, product_description=product_description, length=length)
        try:
            parsed, _usage = self._generate_json(
                prompt,
                feature_key="persona_interview_question_generation",
                company_id=company_id,
                user_id=user_id,
                model_override=model_override,
            )
        except ValueError:
            parsed = self._fallback_interview_question_set(goal=goal, product_description=product_description, length=length)
        return self._normalize_interview_question_set(parsed, goal=goal, product_description=product_description, length=length)

    def _persona_interview_source(self, db_session, *, company_id: int, persona, include_activities: bool = True):
        payload = self.persona_payload(persona)
        settings = self.repository.get_memory_settings(db_session, company_id=company_id, persona_id=persona.id) if hasattr(self.repository, "get_memory_settings") else None
        activities = (
            self.repository.list_activities(db_session, company_id=company_id, persona_id=persona.id)
            if include_activities and hasattr(self.repository, "list_activities")
            else []
        )
        traits = self.repository.list_traits(db_session, company_id=company_id, persona_id=persona.id) if hasattr(self.repository, "list_traits") else []
        lines = ["[기본 정보]"]
        for label, value in (
            ("ID", payload.get("id")),
            ("이름", payload.get("name")),
            ("태그", payload.get("tag")),
            ("나이", payload.get("age")),
            ("성별", payload.get("gender")),
            ("직함/역할", payload.get("title") or payload.get("roleArea")),
            ("직무 레벨", payload.get("roleLevel")),
            ("업종", payload.get("sector")),
            ("조직", payload.get("organisation")),
            ("세대", payload.get("generation")),
            ("지역", " / ".join(part for part in [payload.get("currentCity"), payload.get("currentCountry")] if part)),
            ("소득", payload.get("income")),
        ):
            if value not in (None, ""):
                lines.append(f"- {label}: {value}")
        lines.append("\n[성격/생활 맥락]")
        for label, key in (
            ("성격", "personality"),
            ("태도", "attitudes"),
            ("전기/배경", "biography"),
            ("말투/태도", "demeanour"),
            ("관심사", "interests"),
            ("행동", "behaviours"),
            ("동기", "motivation"),
            ("성장 배경", "upbringing"),
            ("선호", "preferences"),
            ("사회적 맥락", "socialContext"),
            ("문화적 배경", "culturalBackground"),
            ("인용구", "quote"),
        ):
            value = payload.get(key)
            if value:
                lines.append(f"- {label}: {value}")
        lines.append("\n[통신/UX 맥락]")
        for label, key in (
            ("프로필 JSON", "profile"),
            ("통신 프로필 JSON", "telecom_profile"),
            ("통신 이용 프로필", "telecomUsage"),
            ("통신 가치관", "telecomValues"),
            ("UX 상호작용 프로필", "uxInteraction"),
            ("통신 행동 차원", "telecomBehaviorDimensions"),
            ("원본 데이터", "sourceData"),
        ):
            text = _compact_json(payload.get(key), max_chars=1800) if isinstance(payload.get(key), (dict, list)) else payload.get(key)
            if text:
                lines.append(f"- {label}: {text}")
        if traits:
            lines.append("\n[학습된 특성]")
            for trait in traits:
                lines.append(f"- {getattr(trait, 'category', 'general')}: {getattr(trait, 'trait', '')} (confidence {getattr(trait, 'confidence', 0)})")
        if include_activities:
            lines.append("\n[메모리/활동]")
            if settings:
                lines.append(f"- 메모리 사용: {'활성' if getattr(settings, 'enable_memory', False) else '비활성'}")
                lines.append(f"- 메모리 강도: {getattr(settings, 'memory_strength', None)}")
            lines.append(f"- 활동 수: {len(activities or [])}")
            for activity in (activities or [])[:10]:
                lines.append(f"- {getattr(activity, 'activity_type', 'activity')}: {getattr(activity, 'summary', '')}")
        return "\n".join(lines)

    def _persona_interview_pack_prompt(self, *, persona_text: str):
        return f"""
아래 퍼소나 원문은 통신 DNA를 포함한 여러 변수로 구성되어 있습니다.
이 원문을 사용자가 직접 읽는 프로필이 아니라, 인터뷰 응답자가 답변할 때 참고할 "Persona Interview Pack"으로 정제하세요.

[정제 원칙]
- 원문 변수 전체를 복사하지 말고, 인터뷰 답변에 필요한 핵심 근거를 추립니다.
- 통신 DNA는 평가 축이 아니라 이 인물의 판단 기준, 반복 행동, 경험 맥락으로 반영합니다.
- 이름/나이/직업 등 명시된 기본 정보는 보존합니다.
- 말투/스타일이 있으면 답변 스타일로 쓸 수 있게 정리합니다.
- 원문에 없는 과거 경험, 직업, 가족관계, 선호를 새로 만들지 마세요.
- 실제 경험은 짧게 뭉개지 말고, 답변에서 다시 꺼내 쓸 수 있도록 장면/맥락/행동/감정/니즈/UX 접점을 분리해 보존합니다.
- 원문 속 경험이 여러 개면 가능한 한 모두 experience_library에 남기고, 중요 경험은 experience_memory에도 압축해 연결합니다.
- 좋은 답변을 만들기 위해 아래 세 축의 단서를 반드시 분리해 보존합니다.
  1. 정합성/재현성: 프로필의 어떤 정보가 답변 근거가 되는가
  2. 경험회고/내적일관성: 어떤 생활 장면, 감정, 반복 행동으로 회고할 수 있는가
  3. 참여자 퀄리티: 어떤 니즈, 망설임, 발산적 생각, UX 맥락을 말할 수 있는가
- 직접 확인되는 사실과 합리적 추론을 구분하세요. 추론은 "inference"에 명시합니다.

[JSON 형식]
{{
  "identity": {{
    "name": "",
    "age": "",
    "gender": "",
    "job": "",
    "life_context": ""
  }},
  "core_traits": ["성격, 가치관, 태도 핵심 5~8개"],
  "decision_rules": ["상품/서비스/상황 판단 시 반복적으로 쓰는 기준 5~8개"],
  "experience_memory": [
    {{
      "scene": "답변에서 회고할 수 있는 구체적 생활 장면",
      "context": "그 장면이 발생하는 상황/조건",
      "behavior": "그 사람이 실제로 할 법한 행동",
      "emotion_or_tension": "그때의 감정, 망설임, 불편, 기대",
      "profile_basis": "원문에서 근거가 되는 정보",
      "inference": "직접 사실이 아니라면 어떤 추론인지"
    }}
  ],
  "experience_library": [
    {{
      "source_excerpt": "원문에 있는 실제 경험/에피소드/행동 단서의 핵심 원문 표현",
      "when_where": "언제/어디서/어떤 상황인지",
      "trigger": "경험을 발생시킨 계기나 문제",
      "action": "그 사람이 실제로 한 행동",
      "result": "결과, 만족, 실패, 변화",
      "emotion_or_tension": "감정, 망설임, 불안, 기대",
      "need_signal": "드러난 니즈나 페인포인트",
      "ux_ui_context": "화면, 기능, 접점, 사용 흐름, 정보 구조 등 UX/UI 맥락",
      "reuse_guidance": "어떤 질문에서 이 경험을 답변 근거로 재사용하면 좋은지"
    }}
  ],
  "needs_and_painpoints": ["니즈, 불편, 우려, 기대 5~8개"],
  "ux_context_clues": ["서비스/상품 경험을 이야기할 때 참고할 사용 맥락, 선택 기준, 접점 5~8개"],
  "generative_thought_seeds": ["새 아이디어, 대안, 조건부 니즈로 발산할 수 있는 생각의 씨앗 3~5개"],
  "communication_style": {{
    "tone": "",
    "typical_phrases": ["이 사람이 쓸 법한 표현 3~5개"],
    "avoid": ["피해야 할 말투/행동 3~5개"]
  }},
  "consistency_rules": ["이후 모든 답변에서 유지해야 할 내적 일관성 규칙 5~8개"],
  "grounding_notes": ["답변 시 반드시 유지해야 할 중요한 사실/제약 5~8개"]
}}

[퍼소나 원문]
{persona_text}
""".strip()

    def _pack_source_hash(self, *, persona_text: str, model: str):
        hasher = hashlib.sha256()
        hasher.update(PERSONA_INTERVIEW_PACK_VERSION.encode("utf-8"))
        hasher.update(b"\n")
        hasher.update(model.encode("utf-8"))
        hasher.update(b"\n")
        hasher.update(persona_text.encode("utf-8"))
        return hasher.hexdigest()

    def _get_or_create_persona_interview_pack(self, db_session, *, company_id: int, user_id: int, persona, model: str | None = None, force_refresh: bool = False):
        resolved_model = (model or self._default_model_for_stage("persona_interview_pack")).strip()
        persona_text = self._persona_interview_source(
            db_session,
            company_id=company_id,
            persona=persona,
            include_activities=False,
        )
        source_hash = self._pack_source_hash(persona_text=persona_text, model=resolved_model)
        cached_pack = _clean_mapping(getattr(persona, "interview_pack", None))
        if (
            not force_refresh
            and isinstance(cached_pack, dict)
            and getattr(persona, "interview_pack_source_hash", None) == source_hash
            and getattr(persona, "interview_pack_model", None) == resolved_model
            and getattr(persona, "interview_pack_version", None) == PERSONA_INTERVIEW_PACK_VERSION
        ):
            return cached_pack

        prompt = self._persona_interview_pack_prompt(persona_text=persona_text)
        try:
            pack, _usage = self._generate_json(
                prompt,
                feature_key="persona_interview_pack",
                company_id=company_id,
                user_id=user_id,
                model_override=resolved_model,
            )
        except ValueError:
            pack = {
                "identity": {
                    "name": getattr(persona, "name", None),
                    "age": getattr(persona, "age", None),
                    "gender": getattr(persona, "gender", None),
                    "job": getattr(persona, "title", None) or getattr(persona, "role_area", None),
                    "life_context": getattr(persona, "biography", None),
                },
                "core_traits": [value for value in [getattr(persona, "personality", None), getattr(persona, "behaviours", None)] if value],
                "decision_rules": [],
                "experience_memory": [],
                "experience_library": [],
                "needs_and_painpoints": [],
                "ux_context_clues": [],
                "generative_thought_seeds": [],
                "communication_style": {},
                "consistency_rules": [],
                "grounding_notes": [],
            }
        if not isinstance(pack, dict):
            pack = {}
        pack = {**pack, "source_persona_id": str(getattr(persona, "id", ""))}
        if hasattr(self.repository, "update_persona_interview_pack"):
            self.repository.update_persona_interview_pack(
                db_session,
                persona,
                user_id=user_id,
                pack=pack,
                source_hash=source_hash,
                model=resolved_model,
                version=PERSONA_INTERVIEW_PACK_VERSION,
            )
        return pack

    def _run_interview_for_persona(self, *, db_session, company_id: int, user_id: int, interview, persona, pack_model: str | None = None, model_override: str | None = None):
        question_set = self._normalize_interview_question_set(
            interview.question_set,
            goal=interview.goal,
            product_description=interview.product_description,
            length=interview.length,
        )
        questions = self._flatten_interview_questions(question_set)
        if not questions:
            raise ValueError("인터뷰 질문이 비어 있습니다.")
        pack = self._get_or_create_persona_interview_pack(db_session, company_id=company_id, user_id=user_id, persona=persona, model=pack_model)
        persona_text = self._persona_interview_source(db_session, company_id=company_id, persona=persona)
        prompt = "\n".join(
            [
                "아래 인터뷰 설계에 따라 한 명의 퍼소나 참여자와 1:1 AI 인터뷰를 수행한 결과를 생성하세요.",
                "",
                "[인터뷰 정보]",
                json.dumps(
                    {
                        "name": interview.name,
                        "goal": interview.goal,
                        "productDescription": interview.product_description or "",
                        "length": interview.length,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "[Persona Interview Pack]",
                json.dumps(pack, ensure_ascii=False, indent=2),
                "",
                "[퍼소나 원문]",
                persona_text,
                "",
                "[면접관 질문 목록]",
                json.dumps(questions, ensure_ascii=False, indent=2),
                "",
                "[작성 규칙]",
                "- 퍼소나 본인이 1인칭으로 답한 것처럼 작성하세요.",
                "- 각 답변은 4~7문장으로, 프로필 원문과 Persona Interview Pack의 경험/판단 기준/생활 맥락을 반영하세요.",
                "- 답변은 본인 의견, 과거 경험 또는 가치관 기반 근거, 현재 판단/니즈 흐름으로 구성하세요.",
                "- 원문에 있는 실제 경험, 에피소드, 사용 장면은 질문과 관련될 때 적극적으로 답변 근거로 재사용하세요.",
                "- 원문에 없는 과거 사건, 구매/이용 이력, 가족관계, 서비스 사용 사실을 실제 경험처럼 확정하지 마세요.",
                "- 단순 긍정/부정으로 끝내지 말고 망설임, 상충 욕구, 조건부 판단, 예외적 사용 상황을 포함하세요.",
                "- 질문 수만큼 turns를 생성하세요.",
                "- 요약은 headline, key_needs, pain_points, opportunities로 정리하세요.",
                "",
                '[응답 JSON] {"summary":{"headline":"","key_needs":[],"pain_points":[],"opportunities":[]},"turns":[{"question":"","answer":""}]}',
            ]
        )
        parsed, usage = self._generate_json(
            prompt,
            feature_key="persona_interview",
            company_id=company_id,
            user_id=user_id,
            model_override=model_override,
        )
        turns = []
        if isinstance(parsed, dict):
            for item in _as_list(parsed.get("turns")):
                if not isinstance(item, dict):
                    continue
                question = _first_text(item.get("question"))
                answer = _first_text(item.get("answer"))
                if question and answer:
                    turns.append({"question": question, "answer": answer})
        if len(turns) < len(questions):
            raise ValueError(f"Interview response returned {len(turns)}/{len(questions)} valid turns")
        return {
            "status": "completed",
            "persona_snapshot": self._persona_snapshot_payload(persona),
            "summary": parsed.get("summary") if isinstance(parsed, dict) and isinstance(parsed.get("summary"), dict) else {},
            "turns": turns,
            "pack": pack,
            "raw_response": {"parsed": parsed, "usage": usage},
        }

    def _failed_interview_result_data(self, *, persona_snapshot: dict | None, error: Exception, attempt_errors: list[str] | None = None):
        retry_attempts = max(0, len(attempt_errors or []) - 1)
        error_message = str(error)
        if retry_attempts:
            error_message = f"인터뷰 생성에 {retry_attempts}회 재시도했지만 실패했습니다: {error_message}"
        return {
            "status": "failed",
            "persona_snapshot": persona_snapshot or {},
            "summary": {},
            "turns": [],
            "pack": None,
            "raw_response": {"attemptErrors": attempt_errors} if attempt_errors else None,
            "error_message": error_message,
        }

    def _run_interview_for_persona_id(
        self,
        *,
        company_id: int,
        user_id: int,
        interview_context,
        persona_id: int,
        pack_model: str | None = None,
        model_override: str | None = None,
    ):
        _log_persona_interview_event("worker_start", persona_id=persona_id, thread=threading.current_thread().name)
        with self.session_factory() as db_session:
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=[persona_id])
            persona = personas[0] if personas else None
            if not persona:
                raise ValueError(f"Persona not found for interview: {persona_id}")
            retry_attempts = _resolve_interview_retry_attempts()
            total_attempts = retry_attempts + 1
            attempt_errors = []
            try:
                for attempt in range(1, total_attempts + 1):
                    _log_persona_interview_event(
                        "attempt_start",
                        persona_id=persona_id,
                        attempt=attempt,
                        max_attempts=total_attempts,
                        thread=threading.current_thread().name,
                    )
                    try:
                        result = self._run_interview_for_persona(
                            db_session=db_session,
                            company_id=company_id,
                            user_id=user_id,
                            interview=interview_context,
                            persona=persona,
                            pack_model=pack_model,
                            model_override=model_override,
                        )
                        if attempt > 1:
                            raw_response = result.get("raw_response") if isinstance(result.get("raw_response"), dict) else {}
                            result["raw_response"] = {**raw_response, "retryAttempts": attempt - 1, "previousErrors": attempt_errors}
                        _log_persona_interview_event(
                            "attempt_success",
                            persona_id=persona_id,
                            attempt=attempt,
                            thread=threading.current_thread().name,
                        )
                        return result
                    except Exception as exc:
                        attempt_errors.append(str(exc))
                        _log_persona_interview_event(
                            "attempt_failed",
                            persona_id=persona_id,
                            attempt=attempt,
                            max_attempts=total_attempts,
                            error_type=type(exc).__name__,
                            error=str(exc),
                            thread=threading.current_thread().name,
                        )
                        if attempt >= total_attempts:
                            return self._failed_interview_result_data(
                                persona_snapshot=self._persona_snapshot_payload(persona),
                                error=exc,
                                attempt_errors=attempt_errors,
                            )
            finally:
                _log_persona_interview_event("worker_end", persona_id=persona_id, thread=threading.current_thread().name)

    def _run_interviews_for_personas(
        self,
        *,
        company_id: int,
        user_id: int,
        interview_context,
        persona_ids: list[int],
        persona_snapshots: dict[int, dict],
        pack_model: str | None = None,
        model_override: str | None = None,
    ) -> list[tuple[int, dict]]:
        if not persona_ids:
            return []
        max_workers = _resolve_interview_max_concurrency(len(persona_ids))
        _log_persona_interview_event("batch_start", personas=len(persona_ids), max_workers=max_workers, interview_id=getattr(interview_context, "id", None))
        results: list[tuple[int, dict] | None] = [None] * len(persona_ids)

        def run_one(persona_id: int):
            return self._run_interview_for_persona_id(
                company_id=company_id,
                user_id=user_id,
                interview_context=interview_context,
                persona_id=persona_id,
                pack_model=pack_model,
                model_override=model_override,
            )

        if max_workers == 1:
            for index, persona_id in enumerate(persona_ids):
                try:
                    result_data = run_one(persona_id)
                except Exception as exc:
                    result_data = self._failed_interview_result_data(persona_snapshot=persona_snapshots.get(persona_id), error=exc)
                results[index] = (persona_id, result_data)
            completed = [row for row in results if row is not None]
            _log_persona_interview_event("batch_end", results=len(completed), interview_id=getattr(interview_context, "id", None))
            return completed

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="persona-interview") as executor:
            future_to_index = {executor.submit(run_one, persona_id): index for index, persona_id in enumerate(persona_ids)}
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                persona_id = persona_ids[index]
                try:
                    result_data = future.result()
                except Exception as exc:
                    result_data = self._failed_interview_result_data(persona_snapshot=persona_snapshots.get(persona_id), error=exc)
                results[index] = (persona_id, result_data)

        completed = [row for row in results if row is not None]
        _log_persona_interview_event("batch_end", results=len(completed), interview_id=getattr(interview_context, "id", None))
        return completed

    def _preview_image_timeout(self) -> int:
        try:
            return max(1, int(os.getenv("PERSONA_IMAGE_GENERATION_TIMEOUT_SECONDS", "45")))
        except Exception:
            return 45

    def _generate_preview_image(self, persona: dict):
        try:
            return self.image_generator(persona, timeout=self._preview_image_timeout())
        except TypeError:
            return self.image_generator(persona)

    def _prepare_generated_personas(self, generated_personas: list[dict], *, include_images: bool):
        def prepare(persona: dict):
            clean = dict(persona)
            clean.pop("_sourceSeed", None)
            if not include_images:
                clean["imageUrl"] = None
                return clean
            try:
                clean["imageUrl"] = self._generate_preview_image(clean)
            except Exception:
                clean["imageUrl"] = None
            return clean

        if not include_images:
            return [prepare(persona) for persona in generated_personas]

        max_workers = resolve_persona_generation_max_concurrency(len(generated_personas))
        if max_workers == 1:
            return [prepare(persona) for persona in generated_personas]

        results = [None] * len(generated_personas)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="persona-image") as executor:
            future_to_index = {executor.submit(prepare, persona): index for index, persona in enumerate(generated_personas)}
            for future in concurrent.futures.as_completed(future_to_index):
                results[future_to_index[future]] = future.result()
        return [result for result in results if result is not None]

    def _merge_persona_summaries(self, *summary_groups):
        merged = []
        seen = set()
        for group in summary_groups:
            for item in group or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                merged.append(
                    {
                        "name": name,
                        "tag": _normalize_persona_tag(item.get("tag")),
                        "age": item.get("age"),
                        "generation": item.get("generation"),
                        "title": item.get("title"),
                        "roleArea": item.get("roleArea"),
                        "personality": item.get("personality"),
                    }
                )
        return merged

    def folder_payload(self, folder):
        return {
            "id": folder.id,
            "company_id": folder.company_id,
            "team_id": folder.team_id,
            "name": folder.name,
            "description": folder.description,
            "color": folder.color,
            "is_default": bool(folder.is_default),
            "created_by_user_id": folder.created_by_user_id,
            "created_at": _dt(folder.created_at),
            "updated_at": _dt(folder.updated_at),
        }

    def persona_payload(self, persona):
        source_data = _clean_mapping(persona.source_data)
        locale = None
        if isinstance(source_data, dict) and isinstance(source_data.get("locale"), dict):
            locale = source_data["locale"]
        elif persona.locale:
            locale = {"country": persona.locale, "language": persona.language}
        return {
            "id": persona.id,
            "schemaVersion": persona.schema_version,
            "company_id": persona.company_id,
            "team_id": persona.team_id,
            "folder_id": persona.folder_id,
            "folderId": str(persona.folder_id) if persona.folder_id is not None else None,
            "created_by_user_id": persona.created_by_user_id,
            "name": persona.name,
            "tag": persona.tag,
            "gender": persona.gender,
            "title": persona.title,
            "personality": persona.personality,
            "language": persona.language,
            "source_type": persona.source_type,
            "sourceType": persona.source_type,
            "source_data": source_data,
            "sourceData": source_data,
            "image_asset_id": persona.image_asset_id,
            "image_url": persona.image_url,
            "imageUrl": persona.image_url,
            "imageData": None,
            "imageMimeType": persona.image_mime_type,
            "image_prompt": persona.image_prompt,
            "imagePrompt": persona.image_prompt,
            "schema_version": persona.schema_version,
            "locale": locale,
            "age": persona.age,
            "profile": _clean_mapping(persona.profile),
            "telecom_profile": _clean_mapping(persona.telecom_profile),
            "income": persona.income,
            "sector": persona.sector,
            "generation": persona.generation,
            "ethnicity": persona.ethnicity,
            "currentCity": persona.current_city,
            "currentCountry": persona.current_country,
            "locations": _clean_mapping(persona.locations),
            "organisation": persona.organisation,
            "roleArea": persona.role_area,
            "roleLevel": persona.role_level,
            "attitudes": persona.attitudes,
            "biography": persona.biography,
            "demeanour": persona.demeanour,
            "interests": persona.interests,
            "behaviours": persona.behaviours,
            "motivation": persona.motivation,
            "upbringing": persona.upbringing,
            "preferences": persona.preferences,
            "socialContext": persona.social_context,
            "culturalBackground": persona.cultural_background,
            "quote": persona.quote,
            "additionalInfo": persona.additional_info,
            "telecomUsage": _clean_mapping(persona.telecom_usage),
            "telecomValues": _clean_mapping(persona.telecom_values),
            "uxInteraction": _clean_mapping(persona.ux_interaction),
            "telecomBehaviorDimensions": _clean_mapping(persona.telecom_behavior_dimensions),
            "telecomBehaviorScores": _build_telecom_behavior_scores(persona),
            "generation_metadata": _clean_mapping(persona.generation_metadata),
            "created_at": _dt(persona.created_at),
            "createdAt": _dt(persona.created_at),
            "updated_at": _dt(persona.updated_at),
            "updatedAt": _dt(persona.updated_at),
        }

    def memory_settings_payload(self, settings):
        if not settings:
            return None
        return {
            "id": settings.id,
            "personaId": settings.persona_id,
            "enableMemory": bool(settings.enable_memory),
            "memoryStrength": settings.memory_strength,
            "applyToChat": bool(settings.apply_to_chat),
            "applyToTests": bool(settings.apply_to_tests),
            "createdAt": _dt(settings.created_at),
            "updatedAt": _dt(settings.updated_at),
        }

    def activity_record_payload(self, row):
        return {
            "id": row.id,
            "personaId": row.persona_id,
            "activityType": row.activity_type,
            "activityId": row.activity_id,
            "summary": row.summary,
            "wasValidated": bool(row.was_validated),
            "wasCorrect": row.was_correct,
            "createdAt": _dt(row.created_at),
        }

    def trait_record_payload(self, row):
        return {
            "id": row.id,
            "personaId": row.persona_id,
            "trait": row.trait,
            "category": row.category,
            "confidence": row.confidence,
            "sourceCount": row.source_count,
            "sources": _clean_mapping(row.sources) or [],
            "isActive": bool(row.is_active),
            "createdAt": _dt(row.created_at),
            "updatedAt": _dt(row.updated_at),
        }

    def activity_stats_payload(self, activities):
        validated = sum(1 for row in activities if row.was_validated)
        correct = sum(1 for row in activities if row.was_correct is True)
        incorrect = sum(1 for row in activities if row.was_correct is False)
        return {
            "total": len(activities),
            "byType": {
                "ui_test": sum(1 for row in activities if row.activity_type == "ui_test"),
            },
            "validated": validated,
            "correct": correct,
            "incorrect": incorrect,
        }

    def _persona_create_data_from_generated(self, persona: dict, *, source_type: str = "manual", source_data=None, locale=None, folder_id=None):
        return {
            "folder_id": int(folder_id) if folder_id else None,
            "name": persona.get("name"),
            "tag": _normalize_persona_tag(persona.get("tag")),
            "gender": persona.get("gender"),
            "title": persona.get("title"),
            "personality": persona.get("personality"),
            "language": (locale or {}).get("language") or persona.get("language") or "ko",
            "source_type": source_type,
            "source_data": source_data,
            "image_url": persona.get("imageUrl"),
            "image_prompt": persona.get("imagePrompt"),
            "schema_version": persona.get("schemaVersion") or 3,
            "locale": (locale or {}).get("country") if isinstance(locale, dict) else None,
            "age": persona.get("age"),
            "income": persona.get("income"),
            "sector": persona.get("sector"),
            "generation": persona.get("generation"),
            "ethnicity": persona.get("ethnicity"),
            "current_city": persona.get("currentCity"),
            "current_country": persona.get("currentCountry"),
            "locations": persona.get("locations"),
            "organisation": persona.get("organisation"),
            "role_area": persona.get("roleArea"),
            "role_level": persona.get("roleLevel"),
            "attitudes": persona.get("attitudes"),
            "biography": persona.get("biography"),
            "demeanour": persona.get("demeanour"),
            "interests": persona.get("interests"),
            "behaviours": persona.get("behaviours"),
            "motivation": persona.get("motivation"),
            "upbringing": persona.get("upbringing"),
            "preferences": persona.get("preferences"),
            "social_context": persona.get("socialContext"),
            "cultural_background": persona.get("culturalBackground"),
            "quote": persona.get("quote"),
            "additional_info": persona.get("additionalInfo") or persona.get("additional_info"),
            "telecom_usage": persona.get("telecomUsage"),
            "telecom_values": persona.get("telecomValues"),
            "ux_interaction": persona.get("uxInteraction"),
            "telecom_behavior_dimensions": persona.get("telecomBehaviorDimensions"),
            "telecom_behavior_scores": persona.get("telecomBehaviorScores") or persona.get("telecom_behavior_scores"),
            "profile": None,
            "telecom_profile": None,
            "generation_metadata": persona.get("generationMetadata") or persona.get("generation_metadata"),
        }

    def _persist_persona_image_if_needed(self, db_session, *, company_id: int, user_id: int, persona_data: dict):
        parsed = _parse_image_data_url(persona_data.get("image_url"))
        if not parsed:
            return persona_data
        mime_type, image_bytes = parsed
        extension = mime_type.split("/")[-1].split("+")[0] or "png"
        storage_data = self.storage.save_bytes(
            image_bytes,
            company_id=company_id,
            filename=f"{persona_data.get('name') or 'persona'}.{extension}",
            mime_type=mime_type,
            asset_type="persona_image",
        )
        asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
        return {
            **persona_data,
            "image_asset_id": asset.id,
            "image_url": f"/api/persona/storage/{asset.id}",
            "image_data": image_bytes,
            "image_mime_type": mime_type,
        }

    def memory_payload(self, settings, activities, traits):
        return {
            "settings": {
                "id": settings.id,
                "persona_id": settings.persona_id,
                "enable_memory": bool(settings.enable_memory),
                "memory_strength": settings.memory_strength,
                "apply_to_chat": bool(settings.apply_to_chat),
                "apply_to_tests": bool(settings.apply_to_tests),
                "created_at": _dt(settings.created_at),
                "updated_at": _dt(settings.updated_at),
            }
            if settings
            else None,
            "activities": [
                {
                    "id": row.id,
                    "activity_type": row.activity_type,
                    "activity_id": row.activity_id,
                    "summary": row.summary,
                    "was_validated": bool(row.was_validated),
                    "was_correct": row.was_correct,
                    "metadata": _clean_mapping(row.metadata_),
                    "created_at": _dt(row.created_at),
                }
                for row in activities
            ],
            "learned_traits": [
                {
                    "id": row.id,
                    "trait": row.trait,
                    "category": row.category,
                    "confidence": row.confidence,
                    "source_count": row.source_count,
                    "sources": _clean_mapping(row.sources),
                    "created_at": _dt(row.created_at),
                    "updated_at": _dt(row.updated_at),
                }
                for row in traits
            ],
        }

    def asset_payload(self, asset):
        return {
            "id": asset.id,
            "asset_type": asset.asset_type,
            "mime_type": asset.mime_type,
            "byte_size": asset.byte_size,
            "original_filename": asset.original_filename,
            "url": f"/api/persona/storage/{asset.id}",
            "created_at": _dt(asset.created_at),
        }

    def ui_test_payload(self, test):
        return _camelize_record_aliases({
            "id": test.id,
            "company_id": test.company_id,
            "name": test.name,
            "description": test.description,
            "device_type": test.device_type,
            "validation_type": test.validation_type,
            "scope_type": test.scope_type,
            "source_type": test.source_type,
            "status": test.status,
            "progress": test.progress,
            "error_message": test.error_message,
            "persona_count": test.persona_count,
            "screen_count": test.screen_count,
            "summary": _clean_mapping(test.summary),
            "source_data": _clean_mapping(test.source_data),
            "created_at": _dt(test.created_at),
            "updated_at": _dt(test.updated_at),
            "started_at": _dt(getattr(test, "started_at", None)),
            "completed_at": _dt(getattr(test, "completed_at", None)),
        })

    def ui_result_payload(self, result):
        return _camelize_result_aliases({
            "id": result.id,
            "test_id": result.test_id,
            "persona_id": result.persona_id,
            "status": result.status,
            "summary": result.summary,
            "persona_goal_fit": result.persona_goal_fit,
            "scores": _clean_mapping(result.scores),
            "feedback": _clean_mapping(result.feedback),
            "pin_comments": _clean_mapping(result.pin_comments) or [],
            "flow_analysis": _clean_mapping(result.flow_analysis) or [],
            "persona_snapshot": _clean_mapping(result.persona_snapshot),
            "confidence": _clean_mapping(result.confidence),
            "evidence_ids": _clean_mapping(result.evidence_ids) or [],
            "strengths": _clean_mapping(result.strengths) or [],
            "risks": _clean_mapping(result.risks) or [],
            "recommendations": _clean_mapping(result.recommendations) or [],
            "screen_insights": _clean_mapping(result.screen_insights) or [],
            "evidence": _clean_mapping(result.evidence),
            "raw_response": _clean_mapping(result.raw_response),
            "error_message": result.error_message,
            "created_at": _dt(result.created_at),
            "updated_at": _dt(result.updated_at),
        })

    def ab_test_payload(self, test):
        return _camelize_record_aliases({
            "id": test.id,
            "company_id": test.company_id,
            "name": test.name,
            "purpose": test.purpose,
            "service_context": test.service_context,
            "mode": test.mode,
            "screens": _clean_mapping(test.screens),
            "transitions": _clean_mapping(test.transitions),
            "context_data": _clean_mapping(test.context_data),
            "summary": _clean_mapping(test.summary),
            "status": test.status,
            "progress": test.progress,
            "error_message": test.error_message,
            "enable_consistency_validation": bool(test.enable_consistency_validation),
            "consistency_run_count": test.consistency_run_count,
            "created_at": _dt(test.created_at),
            "updated_at": _dt(test.updated_at),
        })

    def ab_result_payload(self, result):
        return _camelize_result_aliases({
            "id": result.id,
            "ab_test_id": result.ab_test_id,
            "persona_id": result.persona_id,
            "status": result.status,
            "persona_snapshot": _clean_mapping(result.persona_snapshot),
            "scores": _clean_mapping(result.scores),
            "feedback": _clean_mapping(result.feedback),
            "confidence": _clean_mapping(result.confidence),
            "evidence_ids": _clean_mapping(result.evidence_ids),
            "raw_response": _clean_mapping(result.raw_response),
            "error_message": result.error_message,
            "created_at": _dt(result.created_at),
            "updated_at": _dt(result.updated_at),
        })

    def interview_source_payload(self, source, *, chunks=None):
        payload = _camelize_record_aliases({
            "id": source.id,
            "company_id": source.company_id,
            "team_id": source.team_id,
            "created_by_user_id": source.created_by_user_id,
            "updated_by_user_id": source.updated_by_user_id,
            "title": source.title,
            "participant_code": source.participant_code,
            "raw_text": source.raw_text,
            "language": source.language,
            "source_status": source.source_status,
            "processing_error": source.processing_error,
            "metadata": _clean_mapping(source.metadata_),
            "created_at": _dt(source.created_at),
            "updated_at": _dt(source.updated_at),
        })
        if chunks is not None:
            payload["chunks"] = [chunk_to_payload(row) for row in chunks]
            payload["chunkCount"] = len(chunks)
        return payload

    def interview_payload(self, interview, *, results=None):
        payload = _camelize_record_aliases({
            "id": interview.id,
            "company_id": interview.company_id,
            "name": interview.name,
            "goal": interview.goal,
            "product_description": interview.product_description,
            "length": interview.length,
            "question_set": self._normalize_interview_question_set(
                _clean_mapping(interview.question_set),
                goal=interview.goal,
                product_description=interview.product_description,
                length=interview.length,
            ),
            "model": interview.model,
            "pack_model": interview.pack_model,
            "status": interview.status,
            "progress": interview.progress,
            "persona_ids": _clean_mapping(interview.persona_ids) or [],
            "summary": _clean_mapping(interview.summary),
            "error_message": interview.error_message,
            "started_at": _dt(interview.started_at),
            "completed_at": _dt(interview.completed_at),
            "created_at": _dt(interview.created_at),
            "updated_at": _dt(interview.updated_at),
        })
        payload["results"] = results or []
        return payload

    def interview_result_payload(self, result):
        payload = _camelize_result_aliases({
            "id": result.id,
            "interview_id": result.interview_id,
            "persona_id": result.persona_id,
            "status": result.status,
            "persona_snapshot": _clean_mapping(result.persona_snapshot),
            "summary": _clean_mapping(result.summary),
            "turns": _clean_mapping(result.turns) or [],
            "pack": _clean_mapping(result.pack),
            "raw_response": _clean_mapping(result.raw_response),
            "error_message": result.error_message,
            "created_at": _dt(result.created_at),
            "updated_at": _dt(result.updated_at),
        })
        if payload.get("errorMessage") is None and payload.get("error") is None:
            payload["error"] = None
            payload["errorMessage"] = None
        return payload

    def figma_account_payload(self, account):
        return {
            "connected": bool(account),
            "account": {
                "id": account.id,
                "figma_user_id": account.figma_user_id,
                "figma_email": account.figma_email,
                "figma_handle": account.figma_handle,
                "figma_avatar_url": account.figma_avatar_url,
                "expires_at": _dt(account.expires_at),
                "updated_at": _dt(account.updated_at),
            }
            if account
            else None,
        }

    def figma_file_payload(self, row):
        return {
            "id": row.id,
            "figma_file_key": row.figma_file_key,
            "figma_file_name": row.figma_file_name,
            "figma_file_link": row.figma_file_link,
            "thumbnail_url": row.thumbnail_url,
            "last_synced_at": _dt(row.last_synced_at),
            "sync_status": row.sync_status,
            "sync_error": row.sync_error,
        }

    def figma_flow_payload(self, row):
        return {
            "id": row.id,
            "figma_file_id": row.figma_file_id,
            "figma_page_id": row.figma_page_id,
            "figma_page_name": row.figma_page_name,
            "figma_start_node_id": row.figma_start_node_id,
            "figma_flow_name": row.figma_flow_name,
            "metadata": _clean_mapping(row.metadata_),
        }

    def list_folders(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.folder_payload(row) for row in self.repository.list_folders(db_session, company_id=company_id)]})

    def create_folder(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        with self.session_factory() as db_session:
            folder = self.repository.create_folder(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.folder_payload(folder)}, 201)

    def update_folder(self, *, company_id: int, user_id: int, folder_id: int, data: dict):
        with self.session_factory() as db_session:
            folder = self.repository.get_folder(db_session, company_id=company_id, folder_id=folder_id)
            if not folder:
                return self._error("not_found", "folder not found", 404)
            if not self._can_modify(db_session, folder, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            updated = self.repository.update_folder(db_session, folder, user_id=user_id, data=data)
            return self._ok({"data": self.folder_payload(updated)})

    def delete_folder(self, *, company_id: int, user_id: int, folder_id: int):
        with self.session_factory() as db_session:
            folder = self.repository.get_folder(db_session, company_id=company_id, folder_id=folder_id)
            if not folder:
                return self._error("not_found", "folder not found", 404)
            if not self._can_modify(db_session, folder, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_folder(db_session, folder, user_id=user_id)
            return self._ok()

    def list_personas(self, *, company_id: int, page: int, limit: int, search=None, folder_id=None, no_folder=False):
        with self.session_factory() as db_session:
            items, total = self.repository.list_personas(
                db_session,
                company_id=company_id,
                page=page,
                limit=limit,
                search=search,
                folder_id=folder_id,
                no_folder=no_folder,
            )
            return self._ok({"data": [self.persona_payload(row) for row in items], "pagination": {"page": page, "limit": limit, "total": total}})

    def create_persona(self, *, company_id: int, user_id: int, data: dict):
        if "persona" in data and isinstance(data["persona"], dict):
            data = self._persona_create_data_from_generated(
                data["persona"],
                source_type="manual",
                source_data={"createdManually": True},
                locale={"country": data["persona"].get("currentCountry") or "KR", "language": "ko"},
                folder_id=data.get("folderId") or data.get("folder_id"),
            )
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        with self.session_factory() as db_session:
            if data.get("folder_id") and not self.repository.get_folder(db_session, company_id=company_id, folder_id=data["folder_id"]):
                return self._error("invalid", "folder not found", 400)
            data = self._persist_persona_image_if_needed(db_session, company_id=company_id, user_id=user_id, persona_data=data)
            persona = self.repository.create_persona(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.persona_payload(persona)}, 201)

    def _build_persona_generation_context(self, validated: dict) -> str:
        parts: list[str] = []
        for key in ("serviceContext", "productDescription", "goal", "researchGoal", "industry"):
            value = validated.get(key)
            if value:
                parts.append(str(value))
        for segment in validated.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            if segment.get("name"):
                parts.append(str(segment["name"]))
            if segment.get("description"):
                parts.append(str(segment["description"]))
        return " ".join(parts).strip()

    def generate_personas(self, *, company_id: int, user_id: int, data: dict):
        started_at = time.monotonic()
        validated, errors = validate_generation_payload(data)
        if errors or validated is None:
            return PersonaServiceResult(
                status="invalid",
                error="Invalid persona generation request",
                data={"details": errors},
                status_code=400,
            )
        payload_existing_personas = validated.get("existingPersonas") or []
        if validated.get("skipExistingPersonas"):
            existing_personas = self._merge_persona_summaries(payload_existing_personas)
        else:
            with self.session_factory() as db_session:
                db_existing_personas = self.repository.list_existing_persona_summaries(
                    db_session,
                    company_id=company_id,
                )
            existing_personas = self._merge_persona_summaries(db_existing_personas, payload_existing_personas)
        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key="persona_generation")

        def persona_text_generator(prompt: str):
            return run_with_llm_usage_context(usage_context, self._generate_text, prompt)

        interview_evidence_retriever = None
        interview_chunk_count = 0
        try:
            count_chunks = getattr(self.repository, "count_interview_chunks_for_company", None)
            list_chunks = getattr(self.repository, "list_interview_chunks_for_company", None)
            if count_chunks and list_chunks:
                with self.session_factory() as db_session:
                    interview_chunk_count = count_chunks(db_session, company_id=company_id)
                    if interview_chunk_count > 0:
                        interview_chunks = list_chunks(db_session, company_id=company_id)
                        from reopsai.infrastructure.rag import get_vector_service

                        vector_service = get_vector_service()

                        def interview_evidence_retriever(persona: dict, segment: dict, payload: dict):
                            try:
                                return build_curated_interview_evidence_bundle(
                                    vector_service=vector_service,
                                    candidate_chunks=interview_chunks,
                                    persona=persona,
                                    segment=segment,
                                    payload=payload,
                                    text_generator=persona_text_generator,
                                )
                            except Exception as exc:
                                print(
                                    f"[persona-generation] interview_evidence_retrieval_failed "
                                    f"persona={persona.get('name')} error={exc}",
                                    flush=True,
                                )
                                return empty_curated_evidence_bundle()
        except Exception:
            interview_evidence_retriever = None

        def generate():
            return generate_personas_pipeline(
                validated,
                existing_personas=existing_personas,
                text_generator=persona_text_generator,
                interview_evidence_retriever=interview_evidence_retriever,
            )

        try:
            generated = run_with_llm_usage_context(usage_context, generate)
        except FileNotFoundError as exc:
            return self._error("seed_missing", str(exc), 500)
        except PersonaGenerationQualityError as exc:
            return self._error("generation_incomplete", str(exc), 502)
        except ValueError as exc:
            return self._error("seed_invalid", str(exc), 500)
        except RuntimeError as exc:
            return self._error("generation_failed", str(exc), 502)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            return self._error("generation_failed", str(exc), 500)
        personas = self._prepare_generated_personas(generated["personas"], include_images=validated.get("includeImages", True))
        duration_ms = int((time.monotonic() - started_at) * 1000)
        generation_metadata = dict(generated["generation_metadata"])
        timings = dict(generation_metadata.get("timingsMs") or {})
        timings["total"] = duration_ms
        generation_metadata["timingsMs"] = timings
        return self._ok(
            {
                "sourceType": infer_persona_source_type(validated),
                "generationMode": generated["generation_mode"],
                "durationMs": duration_ms,
                "personas": personas,
                "segments": generated["segments"],
                "telecomServiceUsageContextReferences": generated.get("telecom_service_usage_context_references") or [],
                "generationMetadata": generation_metadata,
                "interviewEvidence": {
                    "enabled": bool(interview_evidence_retriever),
                    "companyChunkCount": interview_chunk_count,
                    "mode": generation_metadata.get("interviewEvidenceMode"),
                    "summaries": generation_metadata.get("interviewEvidenceSummaries") or [],
                },
                "tokenUsage": generated["token_usage"],
            }
        )

    def suggest_segments(self, *, company_id: int, user_id: int, data: dict):
        validated, errors = validate_segment_suggestion_payload(data)
        if errors or validated is None:
            return PersonaServiceResult(
                status="invalid",
                error="Invalid segment suggestion payload",
                data={"details": errors},
                status_code=400,
            )

        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key="persona_segment_suggestion")

        def generate():
            return generate_segment_suggestions_pipeline(
                validated,
                text_generator=self._generate_text,
            )

        try:
            segments, usage = run_with_llm_usage_context(usage_context, generate)
        except PersonaGenerationQualityError as exc:
            return self._error("generation_incomplete", str(exc), 502)
        except RuntimeError as exc:
            return self._error("generation_failed", str(exc), 502)
        return self._ok({"segments": segments, "tokenUsage": usage})

    def save_generated_personas(self, *, company_id: int, user_id: int, data: dict):
        personas_input = data.get("personas")
        if not isinstance(personas_input, list) or not personas_input:
            return self._error("invalid", "Invalid generated persona save payload", 400)
        source_type = data.get("sourceType") or data.get("source_type") or "service_based"
        source_data = data.get("sourceData") if "sourceData" in data else data.get("source_data")
        locale = data.get("locale")
        folder_id = data.get("folderId") or data.get("folder_id")
        created = []
        with self.session_factory() as db_session:
            if folder_id and not self.repository.get_folder(db_session, company_id=company_id, folder_id=folder_id):
                return self._error("invalid", "folder not found", 400)
            for item in personas_input:
                persona_data = self._persona_create_data_from_generated(
                    item,
                    source_type=source_type,
                    source_data=source_data,
                    locale=locale,
                    folder_id=folder_id,
                )
                if not persona_data.get("name"):
                    return self._error("invalid", "persona.name is required", 400)
                persona_data = self._persist_persona_image_if_needed(db_session, company_id=company_id, user_id=user_id, persona_data=persona_data)
                persona = self.repository.create_persona(db_session, company_id=company_id, user_id=user_id, data=persona_data)
                created.append(self.persona_payload(persona))
        return self._ok({"personas": created}, 201)

    def get_persona(self, *, company_id: int, persona_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            settings = self.repository.get_memory_settings(db_session, company_id=company_id, persona_id=persona_id)
            activities = self.repository.list_activities(db_session, company_id=company_id, persona_id=persona_id)
            traits = self.repository.list_traits(db_session, company_id=company_id, persona_id=persona_id)
            return self._ok(
                {
                    "persona": self.persona_payload(persona),
                    "memorySettings": self.memory_settings_payload(settings),
                    "activityStats": self.activity_stats_payload(activities),
                    "recentActivities": [self.activity_record_payload(row) for row in activities[:10]],
                    "recentTraits": [self.trait_record_payload(row) for row in traits[:10]],
                }
            )

    def update_persona(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            if data.get("folder_id") and not self.repository.get_folder(db_session, company_id=company_id, folder_id=data["folder_id"]):
                return self._error("invalid", "folder not found", 400)
            updated = self.repository.update_persona(db_session, persona, user_id=user_id, data=data)
            return self._ok({"data": self.persona_payload(updated)})

    def delete_persona(self, *, company_id: int, user_id: int, persona_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_persona(db_session, persona, user_id=user_id)
            return self._ok()

    def get_memory(self, *, company_id: int, persona_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            settings = self.repository.get_memory_settings(db_session, company_id=company_id, persona_id=persona_id)
            activities = self.repository.list_activities(db_session, company_id=company_id, persona_id=persona_id)
            traits = self.repository.list_traits(db_session, company_id=company_id, persona_id=persona_id)
            return self._ok({"data": self.memory_payload(settings, activities, traits)})

    def update_memory_settings(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            settings = self.repository.upsert_memory_settings(db_session, company_id=company_id, persona_id=persona_id, data=data)
            return self._ok({"data": self.memory_payload(settings, [], [])["settings"]})

    def add_activity(self, *, company_id: int, persona_id: int, data: dict):
        if not data.get("activity_type"):
            return self._error("invalid", "activity_type is required", 400)
        with self.session_factory() as db_session:
            if not self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id):
                return self._error("not_found", "persona not found", 404)
            activity = self.repository.create_activity(db_session, company_id=company_id, persona_id=persona_id, data=data)
            return self._ok({"data": {"id": activity.id}}, 201)

    def add_trait(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        if not data.get("trait"):
            return self._error("invalid", "trait is required", 400)
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            trait = self.repository.create_trait(db_session, company_id=company_id, persona_id=persona_id, data=data)
            return self._ok({"data": {"id": trait.id}}, 201)

    def delete_trait(self, *, company_id: int, user_id: int, persona_id: int, trait_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            trait = self.repository.get_trait(db_session, company_id=company_id, persona_id=persona_id, trait_id=trait_id)
            if not persona or not trait:
                return self._error("not_found", "trait not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.deactivate_trait(db_session, trait)
            return self._ok()

    def save_upload(self, *, company_id: int, user_id: int, file, asset_type: str = "upload"):
        if not file:
            return self._error("invalid", "file is required", 400)
        storage_data = self.storage.save_upload(file, company_id=company_id, asset_type=asset_type)
        with self.session_factory() as db_session:
            asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
            return self._ok({"data": self.asset_payload(asset)}, 201)

    def get_asset(self, *, company_id: int, asset_id: int):
        with self.session_factory() as db_session:
            asset = self.repository.get_asset(db_session, company_id=company_id, asset_id=asset_id)
            if not asset:
                return self._error("not_found", "asset not found", 404)
            return self._ok({"asset": asset, "path": self.storage.resolve_local_path(asset.storage_key)})

    def attach_persona_image(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        if not data.get("asset_id") and not data.get("image_url"):
            if data.get("image_prompt"):
                return self._error("image_generation_not_configured", "persona image generation adapter is not configured", 503)
            return self._error("invalid", "asset_id or image_url is required", 400)
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            asset_id = data.get("asset_id")
            if asset_id and not self.repository.get_asset(db_session, company_id=company_id, asset_id=asset_id):
                return self._error("invalid", "asset not found", 400)
            image_url = f"/api/persona/storage/{asset_id}" if asset_id else data.get("image_url")
            updated = self.repository.attach_persona_image(
                db_session,
                persona,
                user_id=user_id,
                asset_id=asset_id,
                image_url=image_url,
                image_prompt=data.get("image_prompt"),
            )
            return self._ok({"data": self.persona_payload(updated)})

    def create_ui_test(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        if not data.get("source_type"):
            return self._error("invalid", "source_type is required", 400)
        with self.session_factory() as db_session:
            test = self.repository.create_ui_test(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.ui_test_payload(test)}, 201)

    def list_ui_tests(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.ui_test_payload(row) for row in self.repository.list_ui_tests(db_session, company_id=company_id)]})

    def get_ui_test(self, *, company_id: int, test_id: int, user_id: int | None = None):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if user_id is not None and test.source_type == "figma":
                source_data = _clean_mapping(test.source_data) or {}
                figma_screens = _as_list(source_data.get("figmaScreens") or source_data.get("figma_screens"))
                if not any(_first_text(screen.get("imageUrl"), screen.get("image_url")) for screen in figma_screens if isinstance(screen, dict)):
                    try:
                        resolved_source_data = self._resolve_ui_source_data_for_run(company_id=company_id, user_id=user_id, source_data=source_data)
                        if resolved_source_data != source_data:
                            test = self.repository.update_ui_test(db_session, test, user_id=user_id, data={"source_data": resolved_source_data})
                    except Exception:
                        pass
            rows = self.repository.list_ui_test_results(db_session, company_id=company_id, test_id=test_id)
            payload = self.ui_test_payload(test)
            payload["results"] = [self.ui_result_payload(row) for row in rows]
            return self._ok({"data": payload})

    def update_ui_test(self, *, company_id: int, user_id: int, test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            updated = self.repository.update_ui_test(db_session, test, user_id=user_id, data=data)
            return self._ok({"data": self.ui_test_payload(updated)})

    def delete_ui_test(self, *, company_id: int, user_id: int, test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_ui_test(db_session, test, user_id=user_id)
            return self._ok()

    def run_ui_test(self, *, company_id: int, user_id: int, test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "running", "progress": 10, "started_at": datetime.now(timezone.utc), "error_message": None})
            try:
                source_data = self._resolve_ui_source_data_for_run(
                    company_id=company_id,
                    user_id=user_id,
                    source_data=_clean_mapping(test.source_data) or {},
                )
            except PersonaUrlCaptureError as exc:
                self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": str(exc)})
                return self._error("capture_failed", str(exc), 502)
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=data.get("persona_ids") or data.get("personaIds"), source_data=source_data)
            if not personas:
                self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": "No personas available for UI test"})
                return self._error("invalid", "No personas available for UI test", 400)
            screens = self._screen_manifest(source_data)
            media_parts = self._read_screen_media_parts(db_session, company_id=company_id, screens=screens)
            try:
                self.repository.delete_ui_test_results(db_session, company_id=company_id, test_id=test.id)
                results = []
                persona_packs = []
                for persona in personas:
                    try:
                        persona_pack = self._get_or_create_persona_interview_pack(
                            db_session,
                            company_id=company_id,
                            user_id=user_id,
                            persona=persona,
                        )
                    except Exception as exc:
                        _log_persona_interview_event(
                            "ui_test_pack_fallback",
                            persona_id=getattr(persona, "id", None),
                            error=str(exc),
                        )
                        persona_pack = _clean_mapping(getattr(persona, "interview_pack", None)) or None
                    persona_packs.append(persona_pack)
                result_data_by_persona = self._run_ui_evaluations_for_personas(
                    company_id=company_id,
                    user_id=user_id,
                    test=test,
                    personas=personas,
                    screens=screens,
                    media_parts=media_parts,
                    persona_packs=persona_packs,
                )
                for persona, result_data in zip(personas, result_data_by_persona):
                    result = self.repository.create_ui_test_result(db_session, company_id=company_id, test_id=test.id, persona_id=persona.id, data=result_data)
                    self.repository.create_activity(
                        db_session,
                        company_id=company_id,
                        persona_id=persona.id,
                        data={
                            "activity_type": "ui_test",
                            "activity_id": str(test.id),
                            "summary": result_data["summary"],
                            "metadata": {"testName": test.name, "scores": result_data.get("scores")},
                        },
                    )
                    results.append(self.ui_result_payload(result))
                summary = {
                    "averageScores": {
                        "clarity": round(sum((row["scores"] or {}).get("clarity", 0) for row in results) / len(results)),
                        "usability": round(sum((row["scores"] or {}).get("usability", 0) for row in results) / len(results)),
                        "appeal": round(sum((row["scores"] or {}).get("appeal", 0) for row in results) / len(results)),
                    },
                    "totalResponses": len(results),
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                }
                self.repository.update_ui_test(
                    db_session,
                    test,
                    user_id=user_id,
                    data={"status": "completed", "progress": 100, "completed_at": datetime.now(timezone.utc), "summary": summary, "persona_count": len(personas), "screen_count": len(screens), "source_data": source_data},
                )
            except Exception as exc:
                self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": str(exc)})
                return self._error("failed", str(exc), 500)
            return self._ok({"data": self.ui_test_payload(test), "results": results})

    def list_ui_results(self, *, company_id: int, test_id: int):
        with self.session_factory() as db_session:
            if not self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id):
                return self._error("not_found", "test not found", 404)
            rows = self.repository.list_ui_test_results(db_session, company_id=company_id, test_id=test_id)
            return self._ok({"data": [self.ui_result_payload(row) for row in rows]})

    def capture_url(self, *, company_id: int, user_id: int, url: str):
        try:
            captured = self.capture.capture_url(url)
            screenshot_base64 = captured.pop("screenshot_base64", None)
            if not screenshot_base64:
                raise PersonaUrlCaptureError("URL capture did not produce a screenshot image")
            image_bytes = base64.b64decode(screenshot_base64)
            storage_data = self.storage.save_bytes(
                image_bytes,
                company_id=company_id,
                filename="captured-url.png",
                mime_type="image/png",
                asset_type="ui_test_capture",
            )
            with self.session_factory() as db_session:
                asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
                captured["capturedImageUrl"] = f"/api/persona/storage/{asset.id}"
                captured["asset_id"] = asset.id
            return self._ok({"data": captured})
        except ValueError as exc:
            return self._error("invalid", str(exc), 400)
        except PersonaUrlCaptureError as exc:
            return self._error("capture_failed", str(exc), 502)
        except Exception as exc:
            return self._error("capture_failed", str(exc), 502)

    def create_ab_test(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        with self.session_factory() as db_session:
            test = self.repository.create_ab_test(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.ab_test_payload(test)}, 201)

    def list_ab_tests(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.ab_test_payload(row) for row in self.repository.list_ab_tests(db_session, company_id=company_id)]})

    def get_ab_test(self, *, company_id: int, ab_test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            rows = self.repository.list_ab_test_results(db_session, company_id=company_id, ab_test_id=ab_test_id)
            payload = self.ab_test_payload(test)
            payload["results"] = [self.ab_result_payload(row) for row in rows]
            return self._ok({"data": payload})

    def update_ab_test(self, *, company_id: int, user_id: int, ab_test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            updated = self.repository.update_ab_test(db_session, test, user_id=user_id, data=data)
            return self._ok({"data": self.ab_test_payload(updated)})

    def delete_ab_test(self, *, company_id: int, user_id: int, ab_test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_ab_test(db_session, test, user_id=user_id)
            return self._ok()

    def run_ab_test(self, *, company_id: int, user_id: int, ab_test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "running", "progress": 10, "error_message": None})
            source_data = _clean_mapping(test.context_data) or {}
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=data.get("persona_ids") or data.get("personaIds"), source_data=source_data)
            if not personas:
                self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "error_message": "No personas available for A/B test"})
                return self._error("invalid", "No personas available for A/B test", 400)
            try:
                self.repository.delete_ab_test_results(db_session, company_id=company_id, ab_test_id=test.id)
                results = []
                result_inputs = []
                for persona in personas:
                    result_data = self._run_ab_persona_evaluation(company_id=company_id, user_id=user_id, test=test, persona=persona)
                    result = self.repository.create_ab_test_result(db_session, company_id=company_id, ab_test_id=test.id, persona_id=persona.id, data=result_data)
                    result_inputs.append(result_data)
                    results.append(self.ab_result_payload(result))
                summary = self._ab_summary(result_inputs, test.mode)
                self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "completed", "progress": 100, "summary": summary})
            except Exception as exc:
                self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "error_message": str(exc)})
                return self._error("failed", str(exc), 500)
            return self._ok({"data": self.ab_test_payload(test), "results": results})

    def list_ab_results(self, *, company_id: int, ab_test_id: int):
        with self.session_factory() as db_session:
            if not self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id):
                return self._error("not_found", "ab test not found", 404)
            rows = self.repository.list_ab_test_results(db_session, company_id=company_id, ab_test_id=ab_test_id)
            return self._ok({"data": [self.ab_result_payload(row) for row in rows]})

    def list_combined_tests(self, *, company_id: int):
        with self.session_factory() as db_session:
            ui_items = [
                {
                    "id": row.id,
                    "kind": "ui-test",
                    "name": row.name,
                    "status": row.status,
                    "created_at": _dt(row.created_at),
                    "createdAt": _dt(row.created_at),
                    "personaCount": row.persona_count,
                    "href": f"/tests/{row.id}",
                    "typeLabel": "UX테스트 > 단일검증",
                }
                for row in self.repository.list_ui_tests(db_session, company_id=company_id)
            ]
            ab_items = [
                {
                    "id": row.id,
                    "kind": "ab-test",
                    "name": row.name,
                    "status": row.status,
                    "created_at": _dt(row.created_at),
                    "createdAt": _dt(row.created_at),
                    "personaCount": len(((row.context_data or {}).get("personaSelection") or {}).get("selectedPersonaIds") or []),
                    "href": f"/tests/{row.id}",
                    "typeLabel": "UX테스트 > A/B테스트",
                }
                for row in self.repository.list_ab_tests(db_session, company_id=company_id)
            ]
            interview_items = [
                {
                    "id": row.id,
                    "kind": "interview",
                    "name": row.name,
                    "status": row.status,
                    "created_at": _dt(row.created_at),
                    "createdAt": _dt(row.created_at),
                    "personaCount": len(row.persona_ids or []),
                    "href": f"/interviews/{row.id}",
                    "typeLabel": "UX테스트 > 1:1 AI 인터뷰",
                }
                for row in self.repository.list_interviews(db_session, company_id=company_id)
            ]
            items = sorted(ui_items + ab_items + interview_items, key=lambda item: item["created_at"] or "", reverse=True)
            return self._ok({"data": items})

    def generate_interview_questions(self, *, company_id: int, user_id: int, data: dict):
        goal = str(data.get("goal") or "").strip()
        if not goal:
            return self._error("invalid", "goal is required", 400)
        length = data.get("length") or "quick"
        model_override = data.get("question_model") or data.get("questionModel") or data.get("model")
        question_set = self._generate_interview_question_set(
            company_id=company_id,
            user_id=user_id,
            name=data.get("name"),
            goal=goal,
            product_description=data.get("productDescription") or data.get("product_description"),
            length=length,
            model_override=model_override,
        )
        return self._ok({"data": {"questions": question_set}})

    def list_interview_personas(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.persona_payload(row) for row in self.repository.list_all_personas(db_session, company_id=company_id)]})

    def _normalize_interview_source_data(self, data: dict, *, require_raw_text: bool = True):
        has_title = "title" in data or "name" in data
        has_raw_text = "raw_text" in data or "rawText" in data
        has_participant_code = "participant_code" in data or "participantCode" in data
        has_language = "language" in data
        title = str(data.get("title") or data.get("name") or "").strip()
        raw_text = str(data.get("raw_text") or data.get("rawText") or "").strip()
        participant_code = str(data.get("participant_code") or data.get("participantCode") or "").strip()
        metadata = data.get("metadata")
        if metadata is None:
            metadata = data.get("meta")
        if metadata is not None and not isinstance(metadata, dict):
            return None, "metadata must be an object"
        if require_raw_text and not title:
            return None, "title is required"
        if has_title and not title:
            return None, "title is required"
        if (require_raw_text or has_raw_text) and len(raw_text) < 20:
            return None, "rawText must be at least 20 characters"
        normalized = {}
        if has_title or require_raw_text:
            normalized["title"] = title
        if has_participant_code or require_raw_text:
            normalized["participant_code"] = participant_code or None
        if has_language or require_raw_text:
            normalized["language"] = str(data.get("language") or "ko").strip() or "ko"
        if metadata is not None or require_raw_text:
            normalized["metadata"] = metadata or {}
        if raw_text or require_raw_text:
            normalized["raw_text"] = raw_text
        return normalized, None

    def create_interview_source(self, *, company_id: int, user_id: int, data: dict):
        normalized, error = self._normalize_interview_source_data(data, require_raw_text=True)
        if error:
            return self._error("invalid", error, 400)
        normalized["source_status"] = "uploaded"
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            source = self.repository.create_interview_source(db_session, company_id=company_id, user_id=user_id, data=normalized)
            return self._ok({"data": self.interview_source_payload(source)}, 201)

    def list_interview_sources(self, *, company_id: int, user_id: int, status: str | None = None):
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            rows = self.repository.list_interview_sources(db_session, company_id=company_id, status=status)
            return self._ok({"data": [self.interview_source_payload(row) for row in rows]})

    def get_interview_source(self, *, company_id: int, user_id: int, source_id: int):
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            source = self.repository.get_interview_source(db_session, company_id=company_id, source_id=source_id)
            if not source:
                return self._error("not_found", "interview source not found", 404)
            return self._ok({"data": self.interview_source_payload(source)})

    def update_interview_source(self, *, company_id: int, user_id: int, source_id: int, data: dict):
        normalized, error = self._normalize_interview_source_data(data, require_raw_text=False)
        if error:
            return self._error("invalid", error, 400)
        if "source_status" in data or "sourceStatus" in data:
            normalized["source_status"] = str(data.get("source_status") or data.get("sourceStatus") or "").strip() or "uploaded"
        if "processing_error" in data or "processingError" in data:
            normalized["processing_error"] = data.get("processing_error") or data.get("processingError")
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            source = self.repository.get_interview_source(db_session, company_id=company_id, source_id=source_id)
            if not source:
                return self._error("not_found", "interview source not found", 404)
            updated = self.repository.update_interview_source(db_session, source, user_id=user_id, data=normalized)
            return self._ok({"data": self.interview_source_payload(updated)})

    def delete_interview_source(self, *, company_id: int, user_id: int, source_id: int):
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            source = self.repository.get_interview_source(db_session, company_id=company_id, source_id=source_id)
            if not source:
                return self._error("not_found", "interview source not found", 404)
            self.repository.soft_delete_interview_source(db_session, source, user_id=user_id)
            return self._ok()

    def embed_interview_source(self, *, company_id: int, user_id: int, source_id: int):
        from reopsai.infrastructure.rag import get_vector_service

        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            source = self.repository.get_interview_source(db_session, company_id=company_id, source_id=source_id)
            if not source:
                return self._error("not_found", "interview source not found", 404)
            chunks = self.repository.list_interview_chunks_for_source(db_session, source_id=source.id)
            if not chunks:
                return self._error("invalid", "semantic interview chunks are required before embedding", 400)
            self.repository.update_interview_source(
                db_session,
                source,
                user_id=user_id,
                data={"source_status": "embedding", "processing_error": None},
            )
            try:
                for chunk in chunks:
                    chunk.embedding_vector_id = chunk_vector_id(chunk.id)
                db_session.flush()
                result = upsert_interview_source_embeddings(get_vector_service(), source, chunks)
                self.repository.mark_interview_chunks_embedded(db_session, chunks, vector_ids=result["ids"])
                metadata = source.metadata_ if isinstance(source.metadata_, dict) else {}
                metadata = {
                    **metadata,
                    "embedding": {
                        "chunk_count": result["chunk_count"],
                        "vector_ids": result["ids"],
                        "collection": "ux_rag",
                        "mode": "semantic_chunks",
                    },
                }
                updated = self.repository.update_interview_source(
                    db_session,
                    source,
                    user_id=user_id,
                    data={"source_status": "embedded", "processing_error": None, "metadata": metadata},
                )
                return self._ok({"data": self.interview_source_payload(updated, chunks=chunks), "embedding": result})
            except Exception as exc:
                updated = self.repository.update_interview_source(
                    db_session,
                    source,
                    user_id=user_id,
                    data={"source_status": "failed", "processing_error": str(exc)},
                )
                return self._error("failed", self.interview_source_payload(updated).get("processingError") or str(exc), 500)

    def search_interview_evidence(self, *, company_id: int, user_id: int, target_variable: str, query: str | None = None, top_k: int = 5):
        from reopsai.infrastructure.rag import get_vector_service

        variable = str(target_variable or "").strip()
        if variable not in TELECOM_EVIDENCE_VARIABLES:
            return self._error("invalid", f"targetVariable must be one of: {', '.join(TELECOM_EVIDENCE_VARIABLES)}", 400)
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            chunks = self.repository.list_interview_chunks_for_company(db_session, company_id=company_id)
            hits = search_interview_evidence_chunks(
                vector_service=get_vector_service(),
                candidate_chunks=chunks,
                target_variable=variable,
                query_text=query or "",
                top_k=max(1, min(int(top_k or 5), 20)),
            )
            return self._ok({"data": hits, "targetVariable": variable, "count": len(hits)})

    def import_local_interview_evidence(
        self,
        *,
        company_id: int,
        user_id: int,
        cleaning_dir: str,
        embed: bool = True,
        replace_existing: bool = True,
    ):
        from pathlib import Path

        from reopsai.infrastructure.rag import get_vector_service

        base = Path(cleaning_dir).resolve()
        cleaned_dir = base / "llm_cleaned_interviews"
        chunk_dir = base / "experience_chunks"
        if not cleaned_dir.exists() or not chunk_dir.exists():
            return self._error("invalid", f"cleaning directories not found under {base}", 400)

        imported = []
        with self.session_factory() as db_session:
            if not self._is_company_admin(db_session, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)

            for chunk_path in sorted(chunk_dir.glob("*_chunks.json")):
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
                participant_code = str(payload.get("participantCode") or "").strip()
                reference_type = str(payload.get("referenceType") or "").strip()
                source_stem = chunk_path.stem.replace("_chunks", "")
                cleaned_path = cleaned_dir / f"{source_stem}_llm_cleaned.txt"
                if not cleaned_path.exists():
                    continue

                normalized_chunks = []
                for item in payload.get("chunks") or []:
                    row = normalize_chunk_row_data(item)
                    if row:
                        normalized_chunks.append(row)
                if not normalized_chunks:
                    continue

                source = self.repository.get_interview_source_by_participant_code(
                    db_session,
                    company_id=company_id,
                    participant_code=participant_code,
                )
                if source and not replace_existing:
                    imported.append(
                        {
                            "sourceId": source.id,
                            "participantCode": participant_code,
                            "skipped": True,
                            "reason": "already_exists",
                        }
                    )
                    continue

                if source and replace_existing:
                    self.repository.delete_interview_chunks_for_source(db_session, source_id=source.id)
                    self.repository.update_interview_source(
                        db_session,
                        source,
                        user_id=user_id,
                        data={
                            "title": source_stem,
                            "participant_code": participant_code,
                            "raw_text": cleaned_path.read_text(encoding="utf-8"),
                            "language": "ko",
                            "source_status": "chunked",
                            "metadata": {
                                "referenceType": reference_type,
                                "sourceFile": f"{source_stem}.txt",
                                "anonymizationMethod": "llm",
                                "chunkCount": len(normalized_chunks),
                            },
                        },
                    )
                else:
                    source = self.repository.create_interview_source(
                        db_session,
                        company_id=company_id,
                        user_id=user_id,
                        data={
                            "title": source_stem,
                            "participant_code": participant_code,
                            "raw_text": cleaned_path.read_text(encoding="utf-8"),
                            "language": "ko",
                            "source_status": "chunked",
                            "metadata": {
                                "referenceType": reference_type,
                                "sourceFile": f"{source_stem}.txt",
                                "anonymizationMethod": "llm",
                                "chunkCount": len(normalized_chunks),
                            },
                        },
                    )

                created_chunks = self.repository.replace_interview_chunks(
                    db_session,
                    source=source,
                    chunks=normalized_chunks,
                )
                embed_result = None
                if embed:
                    for chunk in created_chunks:
                        chunk.embedding_vector_id = chunk_vector_id(chunk.id)
                    db_session.flush()
                    embed_result = upsert_interview_source_embeddings(get_vector_service(), source, created_chunks)
                    self.repository.mark_interview_chunks_embedded(db_session, created_chunks, vector_ids=embed_result["ids"])
                    metadata = source.metadata_ if isinstance(source.metadata_, dict) else {}
                    metadata = {
                        **metadata,
                        "embedding": {
                            "chunk_count": embed_result["chunk_count"],
                            "vector_ids": embed_result["ids"],
                            "collection": "ux_rag",
                            "mode": "semantic_chunks",
                        },
                    }
                    self.repository.update_interview_source(
                        db_session,
                        source,
                        user_id=user_id,
                        data={"source_status": "embedded", "processing_error": None, "metadata": metadata},
                    )

                imported.append(
                    {
                        "sourceId": source.id,
                        "participantCode": participant_code,
                        "chunkCount": len(created_chunks),
                        "embedded": bool(embed),
                    }
                )
        return self._ok({"data": imported, "importedCount": len(imported)})

    def create_interview(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        goal = str(data.get("goal") or "").strip()
        if not goal:
            return self._error("invalid", "goal is required", 400)
        length = data.get("length") or "quick"
        question_set = data.get("question_set") or data.get("questionSet")
        if question_set:
            question_set = self._normalize_interview_question_set(
                question_set,
                goal=goal,
                product_description=data.get("productDescription") or data.get("product_description"),
                length=length,
            )
        else:
            question_model = data.get("question_model") or data.get("questionModel") or data.get("model")
            question_set = self._generate_interview_question_set(
                company_id=company_id,
                user_id=user_id,
                name=data.get("name"),
                goal=goal,
                product_description=data.get("productDescription") or data.get("product_description"),
                length=length,
                model_override=question_model,
            )
        interview_model = _clean_model_name(data.get("model")) or self._default_model_for_stage("persona_interview")
        pack_model = (
            _clean_model_name(data.get("pack_model"))
            or _clean_model_name(data.get("packModel"))
            or self._default_model_for_stage("persona_interview_pack")
        )
        payload = {
            **data,
            "goal": goal,
            "product_description": data.get("productDescription") or data.get("product_description"),
            "length": length,
            "question_set": question_set,
            "model": interview_model,
            "pack_model": pack_model,
            "persona_ids": data.get("persona_ids") or data.get("personaIds") or [],
        }
        with self.session_factory() as db_session:
            interview = self.repository.create_interview(db_session, company_id=company_id, user_id=user_id, data=payload)
            return self._ok({"data": self.interview_payload(interview, results=[])}, 201)

    def list_interviews(self, *, company_id: int):
        with self.session_factory() as db_session:
            payloads = []
            for row in self.repository.list_interviews(db_session, company_id=company_id):
                results = self.repository.list_interview_results(db_session, company_id=company_id, interview_id=row.id)
                payloads.append(self.interview_payload(row, results=[self.interview_result_payload(result) for result in results]))
            return self._ok({"data": payloads})

    def get_interview(self, *, company_id: int, interview_id: int):
        with self.session_factory() as db_session:
            interview = self.repository.get_interview(db_session, company_id=company_id, interview_id=interview_id)
            if not interview:
                return self._error("not_found", "interview not found", 404)
            results = self.repository.list_interview_results(db_session, company_id=company_id, interview_id=interview_id)
            result_payloads = [self.interview_result_payload(row) for row in results]
            payload = self.interview_payload(interview, results=result_payloads)
            return self._ok({"data": payload, "results": result_payloads})

    def delete_interview(self, *, company_id: int, user_id: int, interview_id: int):
        with self.session_factory() as db_session:
            interview = self.repository.get_interview(db_session, company_id=company_id, interview_id=interview_id)
            if not interview:
                return self._error("not_found", "interview not found", 404)
            if not self._can_modify(db_session, interview, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_interview(db_session, interview, user_id=user_id)
            return self._ok()

    def run_interview(self, *, company_id: int, user_id: int, interview_id: int, data: dict):
        with self.session_factory() as db_session:
            interview = self.repository.get_interview(db_session, company_id=company_id, interview_id=interview_id)
            if not interview:
                return self._error("not_found", "interview not found", 404)
            if not self._can_modify(db_session, interview, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            persona_ids = data.get("persona_ids") or data.get("personaIds") or interview.persona_ids or []
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=persona_ids)
            if not personas:
                self.repository.update_interview(db_session, interview, user_id=user_id, data={"status": "failed", "progress": 100, "error_message": "No personas available for interview"})
                return self._error("invalid", "No personas available for interview", 400)
            model = _clean_model_name(data.get("model")) or _clean_model_name(interview.model) or self._default_model_for_stage("persona_interview")
            pack_model = (
                _clean_model_name(data.get("pack_model"))
                or _clean_model_name(data.get("packModel"))
                or _clean_model_name(interview.pack_model)
                or self._default_model_for_stage("persona_interview_pack")
            )
            self.repository.update_interview(
                db_session,
                interview,
                user_id=user_id,
                data={
                    "status": "running",
                    "progress": 10,
                    "persona_ids": [row.id for row in personas],
                    "model": model,
                    "pack_model": pack_model,
                    "started_at": datetime.now(timezone.utc),
                    "completed_at": None,
                    "error_message": None,
                    "question_set": self._normalize_interview_question_set(
                        interview.question_set,
                        goal=interview.goal,
                        product_description=interview.product_description,
                        length=interview.length,
                    ),
                },
            )
            self.repository.delete_interview_results(db_session, company_id=company_id, interview_id=interview.id)
            persona_ids_to_run = [row.id for row in personas]
            persona_snapshots = {row.id: self._persona_snapshot_payload(row) for row in personas}
            interview_context = SimpleNamespace(
                id=interview.id,
                name=interview.name,
                goal=interview.goal,
                product_description=interview.product_description,
                length=interview.length,
                question_set=interview.question_set,
                model=model,
                pack_model=pack_model,
            )
            generated_results = self._run_interviews_for_personas(
                company_id=company_id,
                user_id=user_id,
                interview_context=interview_context,
                persona_ids=persona_ids_to_run,
                persona_snapshots=persona_snapshots,
                pack_model=pack_model,
                model_override=model,
            )
            results = []
            for persona_id, result_data in generated_results:
                result = self.repository.create_interview_result(db_session, company_id=company_id, interview_id=interview.id, persona_id=persona_id, data=result_data)
                results.append(self.interview_result_payload(result))
            has_failures = any(row.get("status") == "failed" or row.get("error") for row in results)
            summary = {
                "totalResponses": len(results),
                "completedResponses": sum(1 for row in results if row.get("status") != "failed"),
                "failedResponses": sum(1 for row in results if row.get("status") == "failed" or row.get("error")),
                "completedAt": datetime.now(timezone.utc).isoformat(),
            }
            self.repository.update_interview(
                db_session,
                interview,
                user_id=user_id,
                data={
                    "status": "completed_with_errors" if has_failures else "completed",
                    "progress": 100,
                    "completed_at": datetime.now(timezone.utc),
                    "summary": summary,
                    "persona_ids": [row.id for row in personas],
                    "error_message": None,
                },
            )
            payload = self.interview_payload(interview, results=results)
            return self._ok({"data": payload, "results": results})

    def figma_status(self, *, company_id: int, user_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            return self._ok(self.figma_account_payload(account))

    def figma_connect_url(self, *, company_id: int, user_id: int, redirect_uri: str):
        try:
            url = self.figma_client.authorization_url(state=make_oauth_state(company_id=company_id, user_id=user_id), redirect_uri=redirect_uri)
            return self._ok({"url": url})
        except Exception as exc:
            return self._error("figma_not_configured", str(exc), 503)

    def figma_callback(self, *, company_id: int, user_id: int, code: str, redirect_uri: str):
        try:
            payload = self.figma_client.exchange_code(code=code, redirect_uri=redirect_uri)
            if not payload.get("figma_user_id"):
                return self._error("figma_error", "Figma user id is missing", 502)
            data = {
                "figma_user_id": payload["figma_user_id"],
                "figma_email": payload.get("figma_email"),
                "figma_handle": payload.get("figma_handle"),
                "figma_avatar_url": payload.get("figma_avatar_url"),
                "access_token_encrypted": self.figma_client.encrypt(payload.get("access_token")),
                "refresh_token_encrypted": self.figma_client.encrypt(payload.get("refresh_token")),
                "scope": payload.get("scope"),
                "expires_at": payload.get("expires_at"),
            }
            with self.session_factory() as db_session:
                account = self.repository.upsert_figma_account(db_session, company_id=company_id, user_id=user_id, data=data)
                return self._ok(self.figma_account_payload(account))
        except Exception as exc:
            return self._error("figma_error", str(exc), 502)

    def figma_disconnect(self, *, company_id: int, user_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            if account:
                self.repository.disconnect_figma_account(db_session, account)
            return self._ok()

    def list_figma_files(self, *, company_id: int, user_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            rows = self.repository.list_figma_files(db_session, company_id=company_id, account_id=account.id if account else None)
            return self._ok({"data": [self.figma_file_payload(row) for row in rows]})

    def sync_figma_file(self, *, company_id: int, user_id: int, data: dict):
        figma_file_key = str(data.get("figma_file_key") or "").strip()
        if not figma_file_key:
            return self._error("invalid", "올바른 Figma 파일 URL 형식이 아닙니다.", 400)
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            if not account:
                return self._error("not_connected", "Figma account is not connected", 409)
            existing = self.repository.get_figma_file_by_key(db_session, company_id=company_id, figma_file_key=figma_file_key)
            if existing:
                return self._error("duplicate", "이미 추가된 URL 주소입니다.", 409)

            access_token = self.figma_client.decrypt(account.access_token_encrypted)
            if not access_token:
                return self._error("not_connected", "Figma account is not connected", 409)
            try:
                figma_payload = self.figma_client.fetch_file_with_flows(file_key=figma_file_key, access_token=access_token)
            except PersonaFigmaClientError as exc:
                return self._error(exc.code, exc.message, exc.status_code)
            flows = figma_payload.get("flows") or []
            if not flows:
                return self._error("missing_flow", "파일 내 프로토 타입 Flow가 연결되어 있는지 확인해주세요.", 400)

            figma_file = self.repository.upsert_figma_file(
                db_session,
                company_id=company_id,
                account_id=account.id,
                data={
                    "figma_account_id": account.id,
                    "figma_file_key": figma_file_key,
                    "figma_file_name": figma_payload.get("figma_file_name") or data.get("figma_file_name") or figma_file_key,
                    "figma_file_link": data.get("figma_file_link"),
                    "thumbnail_url": figma_payload.get("thumbnail_url") or data.get("thumbnail_url"),
                    "last_synced_at": datetime.now(timezone.utc),
                    "sync_status": "completed",
                    "sync_error": None,
                },
            )
            created_flows = self.repository.replace_figma_flows(db_session, company_id=company_id, file_id=figma_file.id, flows=flows)
            payload = self.figma_file_payload(figma_file)
            payload["flows"] = [self.figma_flow_payload(row) for row in created_flows]
            return self._ok({"data": payload}, 201)

    def refresh_figma_file(self, *, company_id: int, user_id: int, file_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            if not account:
                return self._error("not_connected", "Figma account is not connected", 409)
            figma_file = self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id)
            if not figma_file:
                return self._error("not_found", "figma file not found", 404)

            access_token = self.figma_client.decrypt(account.access_token_encrypted)
            if not access_token:
                return self._error("not_connected", "Figma account is not connected", 409)
            try:
                figma_payload = self.figma_client.fetch_file_with_flows(file_key=figma_file.figma_file_key, access_token=access_token)
            except PersonaFigmaClientError as exc:
                return self._error(exc.code, exc.message, exc.status_code)
            flows = figma_payload.get("flows") or []
            if not flows:
                return self._error("missing_flow", "파일 내 프로토 타입 Flow가 연결되어 있는지 확인해주세요.", 400)

            refreshed_file = self.repository.upsert_figma_file(
                db_session,
                company_id=company_id,
                account_id=account.id,
                data={
                    "figma_account_id": account.id,
                    "figma_file_key": figma_file.figma_file_key,
                    "figma_file_name": figma_payload.get("figma_file_name") or figma_file.figma_file_name or figma_file.figma_file_key,
                    "figma_file_link": figma_file.figma_file_link,
                    "thumbnail_url": figma_payload.get("thumbnail_url") or figma_file.thumbnail_url,
                    "last_synced_at": datetime.now(timezone.utc),
                    "sync_status": "completed",
                    "sync_error": None,
                },
            )
            created_flows = self.repository.replace_figma_flows(db_session, company_id=company_id, file_id=refreshed_file.id, flows=flows)
            payload = self.figma_file_payload(refreshed_file)
            payload["flows"] = [self.figma_flow_payload(row) for row in created_flows]
            return self._ok({"data": payload})

    def delete_figma_file(self, *, company_id: int, file_id: int):
        with self.session_factory() as db_session:
            figma_file = self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id)
            if not figma_file:
                return self._error("not_found", "figma file not found", 404)
            self.repository.delete_figma_file(db_session, figma_file)
            return self._ok()

    def list_figma_flows(self, *, company_id: int, file_id: int):
        with self.session_factory() as db_session:
            if not self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id):
                return self._error("not_found", "figma file not found", 404)
            rows = self.repository.list_figma_flows(db_session, company_id=company_id, file_id=file_id)
            return self._ok({"data": [self.figma_flow_payload(row) for row in rows]})

    def sync_figma_flows(self, *, company_id: int, file_id: int, data: dict):
        flows = data.get("flows")
        if not isinstance(flows, list):
            return self._error("invalid", "flows must be a list", 400)
        with self.session_factory() as db_session:
            if not self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id):
                return self._error("not_found", "figma file not found", 404)
            created = self.repository.replace_figma_flows(db_session, company_id=company_id, file_id=file_id, flows=flows)
            return self._ok({"data": [self.figma_flow_payload(row) for row in created]}, 201)

    def preview_figma_flow(self, *, company_id: int, user_id: int, file_id: int, flow_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            if not account:
                return self._error("not_connected", "Figma account is not connected", 409)
            figma_file = self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id)
            if not figma_file:
                return self._error("not_found", "figma file not found", 404)
            flows = self.repository.list_figma_flows(db_session, company_id=company_id, file_id=file_id)
            figma_flow = next((row for row in flows if str(row.id) == str(flow_id)), None)
            if not figma_flow:
                return self._error("not_found", "figma flow not found", 404)
            access_token = self.figma_client.decrypt(account.access_token_encrypted)
            if not access_token:
                return self._error("not_connected", "Figma account is not connected", 409)
            try:
                preview = self._cache_figma_preview_screens(
                    db_session,
                    company_id=company_id,
                    user_id=user_id,
                    figma_file=figma_file,
                    figma_flow=figma_flow,
                    access_token=access_token,
                )
            except PersonaFigmaClientError as exc:
                return self._error(exc.code, exc.message, exc.status_code)
            return self._ok(
                {
                    "data": {
                        **preview,
                        "flowName": getattr(figma_flow, "figma_flow_name", None),
                        "flow_name": getattr(figma_flow, "figma_flow_name", None),
                        "pageName": getattr(figma_flow, "figma_page_name", None),
                        "page_name": getattr(figma_flow, "figma_page_name", None),
                        "startNodeId": getattr(figma_flow, "figma_start_node_id", None),
                        "start_node_id": getattr(figma_flow, "figma_start_node_id", None),
                    }
                }
            )


persona_service = PersonaService()
