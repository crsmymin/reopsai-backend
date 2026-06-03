"""Interview experience chunks: DB helpers, vector indexing, and retrieval."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable


TELECOM_EVIDENCE_VARIABLES = (
    "brandRetentionTendency",
    "premiumInfraBenefitOrientation",
    "optimizationResourceInvestment",
    "paymentResistanceLine",
    "informationExplorationStyle",
    "problemSolvingAutonomy",
    "aiProviderTrust",
    "personalizationDataSharingScope",
    "householdDecisionLeadership",
    "productServiceUnderstanding",
    "telecomServiceUsageContext",
)

TELECOM_DOMAIN_ANCHOR = (
    "통신 요금제 요금 이동 번호이동 결합 가족요금 앱 고객센터 "
    "AI 추천 개인정보 데이터 혜택 멤버십 알뜰폰 T월드"
)

GLOBAL_EVIDENCE_TOP_K = 12
GLOBAL_EVIDENCE_MAX_CHUNKS_PER_SOURCE = 3
CURATED_EVIDENCE_MIN_KEEP = 3
CURATED_EVIDENCE_MAX_KEEP = 7

VARIABLE_SEARCH_HINTS: dict[str, str] = {
    "brandRetentionTendency": "통신사 유지 장기 고객 전환 번호이동 브랜드",
    "premiumInfraBenefitOrientation": "통신 품질 끊김 데이터 속도 프리미엄 혜택",
    "optimizationResourceInvestment": "요금제 비교 최적화 시간 노력",
    "paymentResistanceLine": "요금 부담 가격 저항 할인 위약금",
    "informationExplorationStyle": "정보 탐색 비교 검색 상담",
    "problemSolvingAutonomy": "문제 해결 고객센터 앱 챗봇 자가 해결",
    "aiProviderTrust": "AI 추천 챗봇 신뢰 디지털",
    "personalizationDataSharingScope": "개인정보 맞춤 데이터 공유",
    "householdDecisionLeadership": "가구 가족 요금 결합 의사결정",
    "productServiceUnderstanding": "요금제 이해 서비스 조건",
    "telecomServiceUsageContext": "통신 사용 맥락 일상 경험",
}


def _metadata_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)[:2000]


def chunk_vector_id(chunk_id: int) -> str:
    return f"persona_interview_chunk_{int(chunk_id)}"


def normalize_chunk_row_data(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    experience_text = str(item.get("experienceText") or item.get("experience_text") or "").strip()
    source_quote = str(item.get("sourceQuote") or item.get("source_quote") or "").strip()
    if len(experience_text) < 20 or len(source_quote) < 10:
        return None
    target_variables = [
        str(value).strip()
        for value in (item.get("targetVariables") or item.get("target_variables") or [])
        if str(value).strip() in TELECOM_EVIDENCE_VARIABLES
    ]
    if not target_variables:
        return None
    return {
        "external_chunk_id": str(item.get("chunkId") or item.get("external_chunk_id") or "").strip(),
        "experience_text": experience_text,
        "source_quote": source_quote,
        "summary": str(item.get("summary") or "").strip() or None,
        "target_variables": target_variables,
        "behavioral_signals": [
            str(value).strip()
            for value in (item.get("behavioralSignals") or item.get("behavioral_signals") or [])
            if str(value).strip()
        ],
        "tags": [str(value).strip() for value in (item.get("tags") or []) if str(value).strip()],
        "evidence_strength": str(item.get("evidenceStrength") or item.get("evidence_strength") or "").strip() or None,
        "confidence": float(item["confidence"]) if item.get("confidence") is not None else None,
    }


def chunk_to_payload(chunk) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "sourceId": chunk.source_id,
        "externalChunkId": chunk.external_chunk_id,
        "experienceText": chunk.experience_text,
        "sourceQuote": chunk.source_quote,
        "summary": chunk.summary,
        "targetVariables": chunk.target_variables or [],
        "behavioralSignals": chunk.behavioral_signals or [],
        "tags": chunk.tags or [],
        "evidenceStrength": chunk.evidence_strength,
        "confidence": chunk.confidence,
        "embeddingVectorId": chunk.embedding_vector_id,
        "embeddedAt": chunk.embedded_at.isoformat() if chunk.embedded_at else None,
    }


def build_chunk_vector_records(source, chunks: Iterable) -> tuple[list[str], list[str], list[dict[str, str | int | float | bool]]]:
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str | int | float | bool]] = []

    for chunk in chunks:
        vector_id = chunk.embedding_vector_id or chunk_vector_id(chunk.id)
        document = "\n".join(
            part
            for part in [
                chunk.experience_text,
                chunk.summary or "",
                " ".join(chunk.tags or []),
            ]
            if part
        ).strip()
        ids.append(vector_id)
        documents.append(document)
        metadatas.append({
            "data_type": "persona_interview_evidence",
            "domain": "telecom",
            "source": f"persona_interview_source:{source.id}",
            "source_id": int(source.id),
            "chunk_db_id": int(chunk.id),
            "external_chunk_id": chunk.external_chunk_id,
            "source_title": source.title,
            "participant_code": source.participant_code or "",
            "company_id": int(source.company_id),
            "language": source.language or "ko",
            "target_variables": ",".join(chunk.target_variables or []),
            "evidence_strength": chunk.evidence_strength or "",
            "confidence": float(chunk.confidence or 0.0),
        })

    return ids, documents, metadatas


def upsert_interview_source_embeddings(vector_service, source, chunks: Iterable) -> dict[str, Any]:
    if vector_service is None or not getattr(vector_service, "improved_service", None):
        raise RuntimeError("vector service is not available")

    chunk_list = list(chunks)
    if not chunk_list:
        return {"chunk_count": 0, "ids": []}

    ids, documents, metadatas = build_chunk_vector_records(source, chunk_list)
    service = vector_service.improved_service
    embeddings = service.model.encode(documents).tolist()
    service.collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )
    return {"chunk_count": len(documents), "ids": ids}


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, bytes)):
        return len(value) == 0
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _coerce_chroma_ids(value: Any) -> list[str]:
    if _is_empty_value(value):
        return []
    if isinstance(value, list):
        if value and isinstance(value[0], list):
            return [str(item) for item in value[0]]
        return [str(item) for item in value]
    return [str(value)]


def _coerce_chroma_embedding_rows(value: Any) -> list[Any]:
    """Chroma may return embeddings as list, list-of-lists, or 2D numpy array."""
    if _is_empty_value(value):
        return []
    if hasattr(value, "tolist"):
        converted = value.tolist()
    elif isinstance(value, list):
        converted = value
    else:
        return []
    if not converted:
        return []
    if isinstance(converted[0], (list, tuple)) or hasattr(converted[0], "tolist"):
        return converted
    return [converted]


def _embedding_to_list(embedding: Any) -> list[float]:
    if embedding is None:
        return []
    if hasattr(embedding, "tolist"):
        values = embedding.tolist()
    elif isinstance(embedding, (list, tuple)):
        values = list(embedding)
    else:
        return []
    if not values:
        return []
    if isinstance(values[0], (list, tuple)) or hasattr(values[0], "tolist"):
        values = values[0].tolist() if hasattr(values[0], "tolist") else list(values[0])
    try:
        return [float(value) for value in values]
    except (TypeError, ValueError):
        return []


def _cosine_similarity(left: Any, right: Any) -> float:
    left_list = _embedding_to_list(left)
    right_list = _embedding_to_list(right)
    if not left_list or not right_list or len(left_list) != len(right_list):
        return 0.0
    left = left_list
    right = right_list
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _collect_text_parts(*values: Any) -> list[str]:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()
            if text:
                parts.append(text)
    return parts


def build_generation_request_context(payload: dict | None) -> str:
    payload = payload if isinstance(payload, dict) else {}
    parts = _collect_text_parts(
        payload.get("serviceContext"),
        payload.get("service_context"),
        payload.get("productDescription"),
        payload.get("product_description"),
        payload.get("goal"),
        payload.get("researchGoal"),
        payload.get("research_goal"),
        payload.get("industry"),
    )
    return " ".join(parts).strip()


def build_segment_context(segment: dict | None) -> str:
    segment = segment if isinstance(segment, dict) else {}
    characteristics = segment.get("characteristics")
    key_traits = []
    if isinstance(characteristics, dict):
        key_traits = characteristics.get("keyTraits") or characteristics.get("key_traits") or []
    return " ".join(
        _collect_text_parts(
            segment.get("name"),
            segment.get("description"),
            key_traits,
        )
    ).strip()


def build_persona_evidence_query(
    *,
    persona: dict | None,
    segment: dict | None,
    payload: dict | None,
    target_variable: str,
) -> str:
    """Similarity query: user input + segment + generated persona traits (not static variable keywords)."""
    persona = persona if isinstance(persona, dict) else {}
    parts = _collect_text_parts(
        build_generation_request_context(payload),
        build_segment_context(segment),
        persona.get("name"),
        persona.get("title"),
        persona.get("personality"),
        persona.get("attitudes"),
        persona.get("behaviours"),
        persona.get("behaviors"),
        persona.get("motivation"),
        persona.get("biography"),
        persona.get("preferences"),
        persona.get("socialContext"),
        persona.get("social_context"),
        persona.get("culturalBackground"),
        persona.get("cultural_background"),
    )
    if not parts:
        parts.append(VARIABLE_SEARCH_HINTS.get(target_variable, target_variable))
    return " ".join(parts).strip()


def build_global_evidence_query(
    *,
    persona: dict | None,
    segment: dict | None,
    payload: dict | None,
) -> str:
    """Single retrieval query: segment + user input + seed narrative + telecom domain anchor."""
    persona = persona if isinstance(persona, dict) else {}
    parts = _collect_text_parts(
        build_generation_request_context(payload),
        build_segment_context(segment),
        TELECOM_DOMAIN_ANCHOR,
        persona.get("name"),
        persona.get("title"),
        persona.get("personality"),
        persona.get("attitudes"),
        persona.get("behaviours"),
        persona.get("behaviors"),
        persona.get("motivation"),
        persona.get("biography"),
        persona.get("preferences"),
        persona.get("socialContext"),
        persona.get("social_context"),
        persona.get("culturalBackground"),
        persona.get("cultural_background"),
    )
    return " ".join(parts).strip()


def _select_diverse_chunks(
    ranked: list[tuple[float, Any]],
    *,
    top_k: int,
    used_chunk_ids: set[int],
    source_usage_counts: dict[int, int],
    max_chunks_per_source: int,
) -> list[tuple[float, Any]]:
    selected: list[tuple[float, Any]] = []

    for score, chunk in ranked:
        chunk_id = int(getattr(chunk, "id", 0) or 0)
        source_id = int(getattr(chunk, "source_id", 0) or 0)
        if chunk_id and chunk_id in used_chunk_ids:
            continue
        if source_id and source_usage_counts.get(source_id, 0) >= max_chunks_per_source:
            continue
        selected.append((score, chunk))
        if chunk_id:
            used_chunk_ids.add(chunk_id)
        if source_id:
            source_usage_counts[source_id] = source_usage_counts.get(source_id, 0) + 1
        if len(selected) >= top_k:
            break

    if selected:
        return selected

    # Fallback: allow reusing interviewees but still avoid duplicate chunk rows.
    for score, chunk in ranked:
        chunk_id = int(getattr(chunk, "id", 0) or 0)
        if chunk_id and chunk_id in used_chunk_ids:
            continue
        selected.append((score, chunk))
        if chunk_id:
            used_chunk_ids.add(chunk_id)
        if len(selected) >= top_k:
            break
    return selected


def search_interview_evidence_chunks(
    *,
    vector_service,
    candidate_chunks: list,
    target_variable: str | None,
    query_text: str,
    top_k: int = 5,
    used_chunk_ids: set[int] | None = None,
    source_usage_counts: dict[int, int] | None = None,
    max_chunks_per_source: int = 1,
    pool_size: int = 24,
) -> list[dict[str, Any]]:
    if target_variable is not None and target_variable not in TELECOM_EVIDENCE_VARIABLES:
        return []

    used_chunk_ids = used_chunk_ids if used_chunk_ids is not None else set()
    source_usage_counts = source_usage_counts if source_usage_counts is not None else {}

    if target_variable:
        filtered = [
            chunk
            for chunk in candidate_chunks
            if target_variable in (chunk.target_variables or [])
        ]
    else:
        filtered = list(candidate_chunks)
    if not filtered:
        return []

    embedded = [
        chunk
        for chunk in filtered
        if isinstance(chunk.embedding_vector_id, str) and chunk.embedding_vector_id.strip()
    ]
    if (
        not embedded
        or vector_service is None
        or not getattr(vector_service, "improved_service", None)
    ):
        fallback = _select_diverse_chunks(
            [(0.0, chunk) for chunk in filtered],
            top_k=top_k,
            used_chunk_ids=used_chunk_ids,
            source_usage_counts=source_usage_counts,
            max_chunks_per_source=max_chunks_per_source,
        )
        return [chunk_to_payload(chunk) for _, chunk in fallback]

    service = vector_service.improved_service
    if (query_text or "").strip():
        query = str(query_text).strip()
    elif target_variable:
        query = VARIABLE_SEARCH_HINTS[target_variable]
    else:
        query = TELECOM_DOMAIN_ANCHOR
    query_vector = _embedding_to_list(service.model.encode(query))
    vector_ids = [chunk.embedding_vector_id for chunk in embedded]
    stored = service.collection.get(ids=vector_ids, include=["embeddings", "metadatas"])
    id_to_embedding: dict[str, list[float]] = {}
    stored_ids = _coerce_chroma_ids(stored.get("ids"))
    stored_embeddings = _coerce_chroma_embedding_rows(stored.get("embeddings"))
    for index, vector_id in enumerate(stored_ids):
        if index >= len(stored_embeddings):
            continue
        embedding_list = _embedding_to_list(stored_embeddings[index])
        if len(embedding_list) > 0:
            id_to_embedding[vector_id] = embedding_list

    ranked: list[tuple[float, Any]] = []
    for chunk in embedded:
        embedding = id_to_embedding.get(chunk.embedding_vector_id)
        if len(embedding or []) == 0:
            ranked.append((0.0, chunk))
            continue
        ranked.append((_cosine_similarity(query_vector, embedding), chunk))

    ranked.sort(key=lambda item: item[0], reverse=True)
    diverse = _select_diverse_chunks(
        ranked[:pool_size],
        top_k=top_k,
        used_chunk_ids=used_chunk_ids,
        source_usage_counts=source_usage_counts,
        max_chunks_per_source=max_chunks_per_source,
    )
    results = []
    for score, chunk in diverse:
        payload = chunk_to_payload(chunk)
        payload["similarityScore"] = round(score, 4)
        results.append(payload)
    return results


def search_global_interview_evidence_chunks(
    *,
    vector_service,
    candidate_chunks: list,
    persona: dict | None,
    segment: dict | None,
    payload: dict | None,
    top_k: int = GLOBAL_EVIDENCE_TOP_K,
    max_chunks_per_source: int = GLOBAL_EVIDENCE_MAX_CHUNKS_PER_SOURCE,
    pool_size: int = 36,
) -> list[dict[str, Any]]:
    """Variable-agnostic retrieval: one similarity pass over all embedded chunks."""
    query = build_global_evidence_query(persona=persona, segment=segment, payload=payload)
    return search_interview_evidence_chunks(
        vector_service=vector_service,
        candidate_chunks=candidate_chunks,
        target_variable=None,
        query_text=query,
        top_k=top_k,
        used_chunk_ids=set(),
        source_usage_counts={},
        max_chunks_per_source=max_chunks_per_source,
        pool_size=pool_size,
    )


def _chunk_payload_by_id(candidates: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for item in candidates:
        chunk_id = item.get("id")
        if chunk_id is not None:
            mapping[int(chunk_id)] = item
    return mapping


def apply_coherence_curation(
    candidates: list[dict[str, Any]],
    curation: dict[str, Any] | None,
    *,
    min_keep: int = CURATED_EVIDENCE_MIN_KEEP,
    max_keep: int = CURATED_EVIDENCE_MAX_KEEP,
) -> list[dict[str, Any]]:
    """Resolve keep_chunk_ids from LLM; fallback to top similarity if too few."""
    if not candidates:
        return []

    by_id = _chunk_payload_by_id(candidates)
    keep_ids: list[int] = []
    for raw_id in (curation or {}).get("keep_chunk_ids") or (curation or {}).get("keepChunkIds") or []:
        try:
            keep_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    kept = [by_id[chunk_id] for chunk_id in keep_ids if chunk_id in by_id]
    seen: set[int] = {int(item["id"]) for item in kept if item.get("id") is not None}

    ranked = sorted(
        candidates,
        key=lambda item: float(item.get("similarityScore") or 0.0),
        reverse=True,
    )
    for item in ranked:
        chunk_id = item.get("id")
        if chunk_id is None or int(chunk_id) in seen:
            continue
        kept.append(item)
        seen.add(int(chunk_id))
        if len(kept) >= max(min_keep, max_keep):
            break

    if len(kept) > max_keep:
        kept = sorted(
            kept,
            key=lambda item: float(item.get("similarityScore") or 0.0),
            reverse=True,
        )[:max_keep]
    return kept


def format_candidates_for_coherence_prompt(candidates: list[dict[str, Any]], *, max_experience_chars: int = 320) -> str:
    lines = []
    for item in candidates:
        chunk_id = item.get("id")
        lines.append(f"- chunk_id={chunk_id} source_id={item.get('sourceId')} score={item.get('similarityScore', 'n/a')}")
        lines.append(f"  target_variables: {', '.join(item.get('targetVariables') or [])}")
        lines.append(f"  experience: {_clip_evidence_text(item.get('experienceText', ''), max_experience_chars)}")
        if item.get("summary"):
            lines.append(f"  summary: {_clip_evidence_text(item.get('summary', ''), 160)}")
    return "\n".join(lines)


def format_curated_bundle_for_prompt(
    *,
    keep_chunks: list[dict[str, Any]],
    dominant_axes: list[str] | None = None,
    persona_fit_notes: str | None = None,
    segment_alignment: str | None = None,
    max_experience_chars: int = 420,
    max_quote_chars: int = 240,
) -> str:
    if not keep_chunks:
        return ""
    lines = [
        "## Interview Evidence (curated bundle — one coherent behavioral profile)",
        "Use ALL kept chunks together as a single real-user behavior reference.",
        "Do NOT mix in conflicting habits (e.g. long-term loyalty vs frequent switching).",
        "Paraphrase for the fixed persona; do not copy participant names or identities.",
    ]
    if dominant_axes:
        lines.append("### Dominant behavioral axes")
        for axis in dominant_axes:
            text = str(axis).strip()
            if text:
                lines.append(f"- {text}")
    if segment_alignment:
        lines.append(f"- segment_alignment: {segment_alignment}")
    if persona_fit_notes:
        lines.append(f"- persona_fit_notes: {_clip_evidence_text(persona_fit_notes, 400)}")
    lines.append("### Curated experiences")
    for index, item in enumerate(keep_chunks, start=1):
        lines.append(
            f"#### Experience {index} (chunk_id={item.get('id')}, score={item.get('similarityScore', 'n/a')})"
        )
        lines.append(
            f"- experience: {_clip_evidence_text(item.get('experienceText', ''), max_experience_chars)}"
        )
        lines.append(f"- quote: {_clip_evidence_text(item.get('sourceQuote', ''), max_quote_chars)}")
        if item.get("summary"):
            lines.append(f"- summary: {_clip_evidence_text(item.get('summary', ''), 120)}")
        variables = item.get("targetVariables") or []
        if variables:
            lines.append(f"- signals: {', '.join(str(value) for value in variables[:6])}")
    return "\n".join(lines)


def empty_curated_evidence_bundle() -> dict[str, Any]:
    return {
        "mode": "curated_bundle",
        "enabled": False,
        "candidateCount": 0,
        "chunkCount": 0,
        "droppedCount": 0,
        "dominantAxes": [],
        "segmentAlignment": None,
        "personaFitNotes": "",
        "keepChunks": [],
        "promptText": "",
    }


def summarize_curated_evidence_bundle(bundle: dict[str, Any] | None) -> dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    keep_chunks = bundle.get("keepChunks") or bundle.get("keep_chunks") or []
    if not bundle.get("enabled") and not keep_chunks:
        return {"enabled": False, "mode": "curated_bundle", "chunkCount": 0, "candidateCount": 0}
    source_ids = sorted(
        {
            int(item.get("sourceId"))
            for item in keep_chunks
            if item.get("sourceId") is not None
        }
    )
    return {
        "enabled": True,
        "mode": "curated_bundle",
        "chunkCount": len(keep_chunks),
        "candidateCount": int(bundle.get("candidateCount") or bundle.get("candidate_count") or 0),
        "droppedCount": int(bundle.get("droppedCount") or bundle.get("dropped_count") or 0),
        "dominantAxes": bundle.get("dominantAxes") or bundle.get("dominant_axes") or [],
        "segmentAlignment": bundle.get("segmentAlignment") or bundle.get("segment_alignment"),
        "sourceIds": source_ids,
    }


def gather_interview_evidence_for_persona(
    *,
    vector_service,
    candidate_chunks: list,
    persona: dict,
    segment: dict,
    payload: dict,
    top_k: int = 3,
    max_chunks_per_source: int = 1,
) -> dict[str, list[dict[str, Any]]]:
    """Per persona: variable filter -> similarity(persona+segment+user input) -> diverse sources."""
    used_chunk_ids: set[int] = set()
    source_usage_counts: dict[int, int] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    for variable in TELECOM_EVIDENCE_VARIABLES:
        query = build_persona_evidence_query(
            persona=persona,
            segment=segment,
            payload=payload,
            target_variable=variable,
        )
        hits = search_interview_evidence_chunks(
            vector_service=vector_service,
            candidate_chunks=candidate_chunks,
            target_variable=variable,
            query_text=query,
            top_k=top_k,
            used_chunk_ids=used_chunk_ids,
            source_usage_counts=source_usage_counts,
            max_chunks_per_source=max_chunks_per_source,
        )
        if hits:
            evidence[variable] = hits
    return evidence


def gather_interview_evidence_by_variable(
    *,
    vector_service,
    candidate_chunks: list,
    persona_context: str,
    top_k: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """API/legacy helper when only a flat request context string is available."""
    pseudo_payload = {"serviceContext": persona_context}
    pseudo_persona: dict[str, Any] = {}
    pseudo_segment: dict[str, Any] = {}
    return gather_interview_evidence_for_persona(
        vector_service=vector_service,
        candidate_chunks=candidate_chunks,
        persona=pseudo_persona,
        segment=pseudo_segment,
        payload=pseudo_payload,
        top_k=top_k,
        max_chunks_per_source=1,
    )


def summarize_interview_evidence(evidence_by_variable: dict[str, list[dict[str, Any]]] | None) -> dict[str, Any]:
    if not evidence_by_variable:
        return {"enabled": False, "variableCount": 0, "chunkCount": 0, "variables": []}
    variables = []
    chunk_ids: list[int] = []
    for variable, hits in evidence_by_variable.items():
        refs = []
        for hit in hits:
            chunk_id = hit.get("id")
            if chunk_id is not None:
                chunk_ids.append(int(chunk_id))
            refs.append(
                {
                    "chunkId": chunk_id,
                    "externalChunkId": hit.get("externalChunkId"),
                    "sourceId": hit.get("sourceId"),
                    "similarityScore": hit.get("similarityScore"),
                }
            )
        variables.append({"variable": variable, "hits": refs})
    return {
        "enabled": True,
        "variableCount": len(variables),
        "chunkCount": len(set(chunk_ids)),
        "variables": variables,
    }


def count_evidence_chunks(evidence_by_variable: dict[str, list[dict[str, Any]]] | None) -> int:
    chunk_ids: set[int] = set()
    for hits in (evidence_by_variable or {}).values():
        for hit in hits:
            chunk_id = hit.get("id")
            if chunk_id is not None:
                chunk_ids.add(int(chunk_id))
    return len(chunk_ids)


def _clip_evidence_text(text: str, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 1].rstrip()}…"


def format_evidence_for_prompt(
    evidence_by_variable: dict[str, list[dict[str, Any]]],
    *,
    max_experience_chars: int = 450,
    max_quote_chars: int = 280,
) -> str:
    if not evidence_by_variable:
        return ""
    sections = []
    for variable, items in evidence_by_variable.items():
        lines = [f"### {variable}"]
        for index, item in enumerate(items, start=1):
            lines.append(f"- Evidence {index} (score={item.get('similarityScore', 'n/a')}):")
            lines.append(
                f"  - experience: {_clip_evidence_text(item.get('experienceText', ''), max_experience_chars)}"
            )
            lines.append(f"  - quote: {_clip_evidence_text(item.get('sourceQuote', ''), max_quote_chars)}")
            if item.get("summary"):
                lines.append(f"  - summary: {_clip_evidence_text(item.get('summary', ''), 120)}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


# Legacy length-based chunking kept for backward compatibility during migration.
def chunk_interview_source_text(text: str, *, max_chars: int = 900, overlap_chars: int = 120) -> list[str]:
    import re

    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return []
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。！？])\s+|(?<=[다요죠까음함됨])\.\s*", normalized)
        if sentence.strip()
    ]
    if not sentences:
        sentences = [normalized]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(sentence) <= max_chars:
            current = sentence
            continue
        for start in range(0, len(sentence), max_chars - overlap_chars):
            piece = sentence[start : start + max_chars].strip()
            if piece:
                chunks.append(piece)
        current = ""
    if current:
        chunks.append(current)
    return chunks


def build_interview_source_vector_records(source) -> tuple[list[str], list[str], list[dict[str, str | int | float | bool]]]:
    """Deprecated: length-based vectors from raw_text only."""
    import hashlib
    import re

    chunks = chunk_interview_source_text(source.raw_text)
    raw_hash = hashlib.sha256((source.raw_text or "").encode("utf-8")).hexdigest()[:16]
    target_variables = ",".join(TELECOM_EVIDENCE_VARIABLES)
    source_metadata = source.metadata_ if isinstance(source.metadata_, dict) else {}

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str | int | float | bool]] = []
    for index, chunk in enumerate(chunks):
        ids.append(f"persona_interview_source_{source.id}_chunk_{index}_{raw_hash}")
        documents.append(chunk)
        metadatas.append({
            "data_type": "persona_interview_evidence",
            "domain": "telecom",
            "source": f"persona_interview_source:{source.id}",
            "source_id": int(source.id),
            "source_title": source.title,
            "participant_code": source.participant_code or "",
            "language": source.language or "ko",
            "chunk_id": index,
            "chunk_count": len(chunks),
            "chunk_length": len(chunk),
            "target_variables": target_variables,
            "raw_hash": raw_hash,
            "source_metadata": _metadata_value(source_metadata),
        })
    return ids, documents, metadatas
