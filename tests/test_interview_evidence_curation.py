from reopsai.domain.persona.generation import (
    _normalize_coherence_curation,
    build_curated_interview_evidence_bundle,
)


def test_build_curated_bundle_without_llm_coherence_fallback():
    class _Chunk:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    chunks = [
        _Chunk(
            id=1,
            source_id=10,
            external_chunk_id="P01-E01",
            experience_text="a" * 30,
            source_quote="b" * 12,
            summary="s1",
            target_variables=["brandRetentionTendency", "optimizationResourceInvestment"],
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
            target_variables=["informationExplorationStyle"],
            behavioral_signals=[],
            tags=[],
            evidence_strength="strong",
            confidence=0.8,
            embedding_vector_id=None,
            embedded_at=None,
        ),
    ]

    def failing_generator(_prompt):
        raise RuntimeError("llm unavailable")

    bundle = build_curated_interview_evidence_bundle(
        vector_service=None,
        candidate_chunks=chunks,
        persona={"name": "테스트", "personality": "비교형"},
        segment={"name": "절약형", "description": "요금 비교"},
        payload={"serviceDescription": "요금제 앱"},
        text_generator=failing_generator,
    )
    assert bundle["enabled"] is True
    assert bundle["chunkCount"] >= 2
    assert "curated bundle" in bundle["promptText"]
    assert bundle["mode"] == "curated_bundle"


def test_normalize_coherence_curation_accepts_camel_case():
    normalized = _normalize_coherence_curation(
        {
            "dominantAxes": ["유지"],
            "keepChunkIds": [1, 2],
            "segmentAlignment": "high",
            "personaFitNotes": "ok",
        }
    )
    assert normalized["dominant_axes"] == ["유지"]
    assert normalized["keep_chunk_ids"] == [1, 2]
