from reopsai.domain.persona.interview_evidence import (
    TELECOM_DOMAIN_ANCHOR,
    TELECOM_EVIDENCE_VARIABLES,
    apply_coherence_curation,
    build_global_evidence_query,
    build_persona_evidence_query,
    chunk_vector_id,
    count_evidence_chunks,
    format_curated_bundle_for_prompt,
    format_evidence_for_prompt,
    normalize_chunk_row_data,
    search_global_interview_evidence_chunks,
    search_interview_evidence_chunks,
    summarize_curated_evidence_bundle,
)


class _Chunk:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_normalize_chunk_row_data_accepts_camel_case():
    row = normalize_chunk_row_data(
        {
            "chunkId": "P01-E01",
            "experienceText": "참여자는 통신사를 오래 유지했다. 번호 변경 부담 때문에 전환을 미뤘다.",
            "sourceQuote": "20년 넘게 같은 통신사를 썼다.",
            "targetVariables": ["brandRetentionTendency"],
        }
    )
    assert row is not None
    assert row["external_chunk_id"] == "P01-E01"
    assert "brandRetentionTendency" in row["target_variables"]


def test_search_without_vector_service_returns_sql_filtered_chunks():
    chunks = [
        _Chunk(
            id=1,
            source_id=1,
            external_chunk_id="P01-E01",
            experience_text="a" * 30,
            source_quote="b" * 12,
            summary="summary",
            target_variables=["brandRetentionTendency"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="strong",
            confidence=0.9,
            embedding_vector_id=None,
            embedded_at=None,
        ),
        _Chunk(
            id=2,
            source_id=1,
            external_chunk_id="P01-E02",
            experience_text="c" * 30,
            source_quote="d" * 12,
            summary="summary",
            target_variables=["aiProviderTrust"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="medium",
            confidence=0.5,
            embedding_vector_id=None,
            embedded_at=None,
        ),
    ]
    hits = search_interview_evidence_chunks(
        vector_service=None,
        candidate_chunks=chunks,
        target_variable="brandRetentionTendency",
        query_text="통신사 유지",
        top_k=3,
    )
    assert len(hits) == 1
    assert hits[0]["externalChunkId"] == "P01-E01"


def test_chunk_vector_id_is_stable():
    assert chunk_vector_id(42) == "persona_interview_chunk_42"
    assert len(TELECOM_EVIDENCE_VARIABLES) == 11


def test_format_evidence_for_prompt_clips_long_text():
    long_text = "가" * 600
    rendered = format_evidence_for_prompt(
        {
            "brandRetentionTendency": [
                {
                    "similarityScore": 0.91,
                    "experienceText": long_text,
                    "sourceQuote": long_text,
                    "summary": long_text,
                }
            ]
        }
    )
    assert "…" in rendered
    assert len(rendered) < len(long_text) * 3


def test_count_evidence_chunks_deduplicates_across_variables():
    assert count_evidence_chunks(
        {
            "brandRetentionTendency": [{"id": 1}],
            "aiProviderTrust": [{"id": 1}, {"id": 2}],
        }
    ) == 2


def test_build_global_evidence_query_includes_segment_and_domain_anchor():
    query = build_global_evidence_query(
        persona={"personality": "앱 UX에 민감", "biography": "30대"},
        segment={"name": "알뜰 절약형", "description": "가격 비교"},
        payload={"serviceDescription": "5G 요금제 UX"},
    )
    assert "알뜰 절약형" in query
    assert "앱 UX에 민감" in query
    assert "5G 요금제" in query
    assert TELECOM_DOMAIN_ANCHOR.split()[0] in query


def test_search_global_returns_chunks_without_variable_filter():
    chunks = [
        _Chunk(
            id=1,
            source_id=10,
            external_chunk_id="P01-E01",
            experience_text="a" * 30,
            source_quote="b" * 12,
            summary="summary",
            target_variables=["brandRetentionTendency"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="strong",
            confidence=0.9,
            embedding_vector_id=None,
            embedded_at=None,
        ),
        _Chunk(
            id=2,
            source_id=11,
            external_chunk_id="P02-E01",
            experience_text="c" * 30,
            source_quote="d" * 12,
            summary="summary2",
            target_variables=["aiProviderTrust"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="medium",
            confidence=0.5,
            embedding_vector_id=None,
            embedded_at=None,
        ),
    ]
    hits = search_global_interview_evidence_chunks(
        vector_service=None,
        candidate_chunks=chunks,
        persona={"personality": "디지털"},
        segment={"name": "세그"},
        payload={},
        top_k=5,
    )
    assert len(hits) == 2


def test_apply_coherence_curation_falls_back_when_keep_ids_empty():
    candidates = [
        {"id": 1, "similarityScore": 0.2, "experienceText": "low"},
        {"id": 2, "similarityScore": 0.9, "experienceText": "high"},
        {"id": 3, "similarityScore": 0.5, "experienceText": "mid"},
    ]
    kept = apply_coherence_curation(candidates, {"keep_chunk_ids": []}, min_keep=2, max_keep=2)
    assert [item["id"] for item in kept] == [2, 3]


def test_format_curated_bundle_for_prompt_uses_axes_not_per_variable():
    text = format_curated_bundle_for_prompt(
        keep_chunks=[
            {
                "id": 7,
                "similarityScore": 0.88,
                "experienceText": "x" * 40,
                "sourceQuote": "y" * 12,
                "targetVariables": ["brandRetentionTendency"],
            }
        ],
        dominant_axes=["이동·보상 최적화"],
    )
    assert "curated bundle" in text
    assert "Dominant behavioral axes" in text
    assert "이동·보상 최적화" in text
    assert "### brandRetentionTendency" not in text


def test_summarize_curated_evidence_bundle():
    summary = summarize_curated_evidence_bundle(
        {
            "enabled": True,
            "candidateCount": 10,
            "keepChunks": [{"id": 1, "sourceId": 5}, {"id": 2, "sourceId": 5}],
            "dominantAxes": ["가족 결합"],
        }
    )
    assert summary["enabled"] is True
    assert summary["chunkCount"] == 2
    assert summary["sourceIds"] == [5]


def test_build_persona_evidence_query_uses_persona_not_only_variable_keywords():
    query = build_persona_evidence_query(
        persona={
            "personality": "꼼꼼하고 비용에 민감함",
            "behaviours": "요금제를 직접 비교함",
            "biography": "30대 직장인",
        },
        segment={"name": "알뜰 절약형", "description": "가격 비교에 시간을 쓰는 세그"},
        payload={"serviceContext": "5G 요금제 변경 UX"},
        target_variable="brandRetentionTendency",
    )
    assert "알뜰 절약형" in query
    assert "꼼꼼하고 비용에 민감함" in query
    assert "5G 요금제" in query


def test_search_applies_diversity_across_chunks():
    chunks = [
        _Chunk(
            id=1,
            source_id=10,
            external_chunk_id="P01-E01",
            experience_text="a" * 30,
            source_quote="b" * 12,
            summary="s1",
            target_variables=["brandRetentionTendency"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="strong",
            confidence=0.9,
            embedding_vector_id=None,
            embedded_at=None,
        ),
        _Chunk(
            id=2,
            source_id=10,
            external_chunk_id="P01-E02",
            experience_text="c" * 30,
            source_quote="d" * 12,
            summary="s2",
            target_variables=["brandRetentionTendency"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="strong",
            confidence=0.8,
            embedding_vector_id=None,
            embedded_at=None,
        ),
        _Chunk(
            id=3,
            source_id=11,
            external_chunk_id="P02-E01",
            experience_text="e" * 30,
            source_quote="f" * 12,
            summary="s3",
            target_variables=["brandRetentionTendency"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="medium",
            confidence=0.7,
            embedding_vector_id=None,
            embedded_at=None,
        ),
    ]
    used_chunk_ids: set[int] = set()
    source_usage_counts: dict[int, int] = {}
    first = search_interview_evidence_chunks(
        vector_service=None,
        candidate_chunks=chunks,
        target_variable="brandRetentionTendency",
        query_text="통신사 유지",
        top_k=1,
        used_chunk_ids=used_chunk_ids,
        source_usage_counts=source_usage_counts,
        max_chunks_per_source=1,
    )
    second = search_interview_evidence_chunks(
        vector_service=None,
        candidate_chunks=chunks,
        target_variable="brandRetentionTendency",
        query_text="통신사 유지",
        top_k=1,
        used_chunk_ids=used_chunk_ids,
        source_usage_counts=source_usage_counts,
        max_chunks_per_source=1,
    )
    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]
