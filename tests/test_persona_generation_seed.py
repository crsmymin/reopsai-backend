from pathlib import Path

import pytest

from reopsai.domain.persona.generation import (
    DEFAULT_SEED_PATH,
    _json_extract,
    generate_segment_suggestions_pipeline,
    generate_seed_based_personas,
    load_seed_personas,
    select_nemotron_korea_seeds,
    stage_nemotron_telecom_dimensions,
    stage_nemotron_seed_narrative_polish,
    validate_generation_payload,
    validate_segment_suggestion_payload,
)


def test_nemotron_seed_file_is_packaged_with_backend():
    assert DEFAULT_SEED_PATH == Path("data/nemotron-personas-korea-sample.jsonl").resolve()
    assert DEFAULT_SEED_PATH.exists()
    assert DEFAULT_SEED_PATH.stat().st_size > 1_000_000


def test_persona_generation_uses_packaged_nemotron_seed():
    seeds = load_seed_personas(limit=3)
    payload, errors = validate_generation_payload(
        {
            "sourceType": "service_based",
            "serviceDescription": "테스트 서비스 설명입니다.",
            "totalCount": 2,
            "locale": {"country": "KR", "language": "ko"},
        }
    )
    result = generate_seed_based_personas({**payload, "seed": 1})

    assert errors == []
    assert len(seeds) == 3
    assert result["seed_count"] >= 2
    assert len(result["personas"]) == 2
    assert result["generation_mode"] == "nemotron_seed_telecom_polished"
    assert result["token_usage"] == {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "model": "gemini-2.5-flash"}
    assert all(persona["schemaVersion"] == 3 for persona in result["personas"])
    assert all("source_type" not in persona for persona in result["personas"])
    assert all("profile" not in persona for persona in result["personas"])


def test_json_extract_repairs_missing_comma_between_object_fields():
    parsed = _json_extract(
        """
{
  "telecom_behavior_dimensions": {
    "brandRetention": {
      "brandRetentionTendency": "높음"
    }
    "optimizationResource": {
      "optimizationResourceInvestment": "보통"
    }
  },
  "telecom_behavior_scores": []
}
"""
    )

    assert parsed["telecom_behavior_dimensions"]["brandRetention"]["brandRetentionTendency"] == "높음"
    assert parsed["telecom_behavior_dimensions"]["optimizationResource"]["optimizationResourceInvestment"] == "보통"


def test_json_extract_repairs_missing_comma_between_array_objects():
    parsed = _json_extract(
        """
{
  "telecom_behavior_scores": [
    {
      "key": "brandRetention",
      "score": 4
    }
    {
      "key": "optimizationResource",
      "score": 2
    }
  ]
}
"""
    )

    assert parsed["telecom_behavior_scores"][0]["key"] == "brandRetention"
    assert parsed["telecom_behavior_scores"][1]["key"] == "optimizationResource"


def test_json_extract_inserts_missing_comma_at_parser_error_position():
    parsed = _json_extract(
        """
{
  "telecom_behavior_dimensions": {
    "brandRetention": {"brandRetentionTendency": "높음", "premiumInfraBenefitOrientation": "보통"} "optimizationResource": {"optimizationResourceInvestment": "높음", "paymentResistanceLine": "월 7만원"}
  },
  "telecom_behavior_scores": [{"key": "brandRetention", "score": 4} {"key": "optimizationResource", "score": 3}]
}
"""
    )

    assert parsed["telecom_behavior_dimensions"]["optimizationResource"]["paymentResistanceLine"] == "월 7만원"
    assert parsed["telecom_behavior_scores"][1]["key"] == "optimizationResource"


def test_telecom_dimensions_raises_when_model_keeps_returning_invalid_json():
    from reopsai.domain.persona.generation import PersonaGenerationQualityError

    persona = {
        "name": "김민수",
        "biography": "가족 결합과 요금제 혜택을 주기적으로 확인하는 직장인입니다.",
        "attitudes": "통신비와 멤버십 혜택을 실용적으로 비교합니다.",
        "behaviours": "공식 앱과 커뮤니티 후기를 함께 확인합니다.",
        "motivation": "불필요한 통신비를 줄이고 안정적인 서비스를 유지하고 싶습니다.",
    }
    payload = {
        "locale": {"language": "ko", "country": "KR"},
        "serviceDescription": "통신 요금제 추천 서비스",
    }
    segment = {"name": "실용 관리형", "description": "가족 통신비를 직접 관리합니다."}
    seed = {"notes": "앱 추천과 결합 혜택을 확인함"}
    calls = {"count": 0}

    def invalid_text_generator(prompt):
        calls["count"] += 1
        return '{"telecom_behavior_dimensions": {"brandRetention": {"brandRetentionTendency": "높음"}', {
            "inputTokens": 1,
            "outputTokens": 1,
            "totalTokens": 2,
            "model": "gemini-2.5-flash",
        }

    with pytest.raises(PersonaGenerationQualityError):
        stage_nemotron_telecom_dimensions(persona, payload, segment, seed, invalid_text_generator)

    assert calls["count"] == 2


def test_generation_payload_normalizes_source_and_nemotron_options():
    payload, errors = validate_generation_payload(
        {
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "totalCount": 1,
            "locale": {"country": "kr", "language": "KO"},
            "includeImages": False,
            "skipExistingPersonas": True,
            "existingPersonas": [{"name": "Existing", "age": 30, "extra": "ignored"}],
            "nemotronSeedOptions": {"candidateMultiplier": 100, "sampleLimit": 5},
        }
    )

    assert errors == []
    assert payload["sourceType"] == "service_based"
    assert payload["includeImages"] is False
    assert payload["skipExistingPersonas"] is True
    assert payload["locale"] == {"country": "KR", "language": "ko"}
    assert payload["existingPersonas"] == [
        {
            "name": "Existing",
            "age": 30,
            "generation": None,
            "title": None,
            "roleArea": None,
            "personality": None,
        }
    ]
    assert payload["nemotronSeedOptions"] == {"candidateMultiplier": 50, "sampleLimit": 100}


def test_generation_payload_rejects_invalid_source_type():
    payload, errors = validate_generation_payload(
        {
            "sourceType": "invalid",
            "serviceDescription": "통신 요금제 추천 서비스",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
        }
    )

    assert payload is None
    assert "sourceType must be service_based or segment_based" in errors


def test_generation_payload_rejects_short_service_description():
    payload, errors = validate_generation_payload(
        {
            "sourceType": "service_based",
            "serviceDescription": "짧음",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
        }
    )

    assert payload is None
    assert "serviceDescription must be at least 10 characters" in errors


def test_generation_payload_rejects_sparse_segment_inputs():
    payload, errors = validate_generation_payload(
        {
            "sourceType": "segment_based",
            "segmentInputs": [
                {
                    "id": "segment-1",
                    "name": "A",
                    "description": "짧음",
                    "targetCount": 1,
                },
            ],
            "totalCount": 2,
            "locale": {"country": "KR", "language": "ko"},
        }
    )

    assert payload is None
    assert "segmentInputs[0].name must be at least 2 characters" in errors
    assert "segmentInputs[0].description must be at least 10 characters" in errors
    assert "totalCount must match the sum of segmentInputs.targetCount" in errors


def test_segment_suggestion_payload_validation_and_pipeline():
    payload, errors = validate_segment_suggestion_payload(
        {
            "context": "해외여행을 앞두고 로밍 요금제와 도시락 와이파이를 비교하는 사용자를 리서치합니다.",
            "locale": {"country": "kr", "language": "KO"},
            "maxSegments": 3,
        }
    )

    def fake_text_generator(prompt):
        assert "Generate between 2 and 3 segments" in prompt
        return (
            """
            {
              "segments": [
                {
                  "name": "로밍 안정성 중시형",
                  "description": "해외에서도 통신 품질과 고객지원을 우선합니다.",
                  "criteria": "출장, 장기 여행",
                  "target_count": 2
                },
                {
                  "name": "비용 절감형",
                  "description": "도시락 와이파이와 eSIM을 비교해 가장 싼 옵션을 찾습니다.",
                  "criteria": "가격 비교",
                  "target_count": 1
                }
              ]
            }
            """,
            {"inputTokens": 11, "outputTokens": 22, "totalTokens": 33, "model": "gemini-test"},
        )

    segments, usage = generate_segment_suggestions_pipeline(payload, fake_text_generator)

    assert errors == []
    assert payload["locale"] == {"country": "KR", "language": "ko"}
    assert segments == [
        {
            "id": "segment-suggested-1",
            "name": "로밍 안정성 중시형",
            "description": "해외에서도 통신 품질과 고객지원을 우선합니다.",
            "criteria": "출장, 장기 여행",
            "targetCount": 2,
        },
        {
            "id": "segment-suggested-2",
            "name": "비용 절감형",
            "description": "도시락 와이파이와 eSIM을 비교해 가장 싼 옵션을 찾습니다.",
            "criteria": "가격 비교",
            "targetCount": 1,
        },
    ]
    assert usage["totalTokens"] == 33


def test_narrative_polish_keeps_seed_fallback_when_model_returns_blank_required_field():
    base_persona = {
        "schemaVersion": 3,
        "name": "김민수",
        "attitudes": "통신 혜택을 꼼꼼하게 비교합니다.",
        "biography": "통신 요금제를 직접 비교해 온 직장인입니다.",
        "demeanour": "차분하고 분석적인 태도로 의사결정합니다.",
        "interests": "앱 혜택과 요금제 비교에 관심이 많습니다.",
        "behaviours": "요금제 변경 전에 커뮤니티 후기를 확인합니다.",
        "motivation": "매달 통신비를 합리적으로 줄이고 싶어합니다.",
        "upbringing": "서울에서 모바일 서비스를 익숙하게 사용했습니다.",
        "personality": "신중하고 실용적인 성향이 강합니다.",
        "preferences": "명확한 가격표와 장기 이용 혜택을 선호합니다.",
        "socialContext": "가족 통신비도 함께 챙기는 편입니다.",
        "culturalBackground": "도시 생활과 모바일 앱 사용에 익숙합니다.",
        "quote": "혜택은 좋지만 조건이 명확해야 선택합니다.",
        "imagePrompt": "Photorealistic Korean user persona portrait",
    }

    def fake_text_generator(prompt):
        assert "narrative_polish" in prompt
        return ('{"persona":{"preferences":""}}', {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3, "model": "test"})

    persona, usage = stage_nemotron_seed_narrative_polish(
        base_persona,
        {"sourceType": "service_based", "totalCount": 1, "locale": {"country": "KR", "language": "ko"}},
        {"id": "service_based", "name": "Service based", "description": "통신 요금제 추천 서비스"},
        {},
        fake_text_generator,
    )

    assert persona["preferences"] == base_persona["preferences"]
    assert usage["totalTokens"] == 3


def _write_seed_fixture(tmp_path):
    seed_path = tmp_path / "seeds.jsonl"
    seed_path.write_text(
        "\n".join(
            [
                '{"uuid":"seed-1","persona":"김민수 씨는 통신 요금제를 꼼꼼히 비교하는 30대 직장인입니다.","professional_persona":"김민수 씨는 IT 서비스 기획자로 일합니다.","family_persona":"혼자 거주하며 통신비를 직접 관리합니다.","cultural_background":"서울에서 모바일 앱 사용에 익숙합니다.","hobbies_and_interests":"앱 혜택 비교를 즐깁니다.","career_goals_and_ambitions":"생활비를 효율적으로 관리하고 싶어합니다.","sex":"남자","age":34,"occupation":"서비스 기획자","district":"서울-마포구","province":"서울","country":"대한민국","family_type":"혼자 거주","housing_type":"아파트","education_level":"4년제 대학교"}',
                '{"uuid":"seed-2","persona":"이서연 씨는 가족 결합 요금제를 관리하는 사용자입니다.","professional_persona":"이서연 씨는 마케터로 일합니다.","family_persona":"가족 통신비를 챙깁니다.","cultural_background":"부산에서 생활합니다.","hobbies_and_interests":"멤버십 혜택을 확인합니다.","career_goals_and_ambitions":"가계 지출을 줄이고 싶어합니다.","sex":"여자","age":39,"occupation":"마케터","district":"부산-해운대구","province":"부산","country":"대한민국","family_type":"배우자와 거주","housing_type":"아파트","education_level":"4년제 대학교"}',
                '{"uuid":"seed-3","persona":"박지훈 씨는 알뜰폰 프로모션을 자주 살피는 사용자입니다.","professional_persona":"박지훈 씨는 대학생입니다.","family_persona":"가족과 함께 거주합니다.","cultural_background":"대전에서 생활합니다.","hobbies_and_interests":"커뮤니티 정보를 봅니다.","career_goals_and_ambitions":"저렴한 통신비를 원합니다.","sex":"남자","age":23,"occupation":"학생","district":"대전-서구","province":"대전","country":"대한민국","family_type":"부모와 거주","housing_type":"아파트","education_level":"대학교 재학"}',
            ]
        ),
        encoding="utf-8",
    )
    return seed_path


def test_seed_selection_records_score_rank_and_avoids_duplicate_names(tmp_path):
    seed_path = _write_seed_fixture(tmp_path)
    payload, errors = validate_generation_payload(
        {
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "30대 직장인",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "nemotronSeedOptions": {"sampleLimit": 100, "candidateMultiplier": 3},
        }
    )

    result = generate_seed_based_personas(payload, [{"name": "김민수"}], seed_path=seed_path)
    reference = result["generation_metadata"]["nemotronSeedReferences"][0]

    assert errors == []
    assert result["personas"][0]["name"] == "김민수 (2)"
    assert result["personas"][0]["title"] == "서비스 기획자"
    assert result["personas"][0]["sector"] == "경영/사무"
    assert result["personas"][0]["roleArea"] == "기획/운영 관리"
    assert result["personas"][0]["organisation"] is None
    assert result["personas"][0]["roleLevel"] is None
    assert result["personas"][0]["income"] == "52,000,000원"
    assert reference["seedUuid"] == "seed-1"
    assert isinstance(reference["score"], int)
    assert reference["rank"] == 1
    assert reference["familyType"] == "혼자 거주"
    assert reference["housingType"] == "아파트"
    assert reference["educationLevel"] == "4년제 대학교"


def test_seed_mapping_clears_life_role_metadata_and_income(tmp_path):
    seed_path = _write_seed_fixture(tmp_path)
    payload, errors = validate_generation_payload(
        {
            "sourceType": "service_based",
            "serviceDescription": "알뜰폰 프로모션 비교 서비스",
            "targetAudience": "대학생",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "nemotronSeedOptions": {"sampleLimit": 100, "candidateMultiplier": 3},
        }
    )

    result = generate_seed_based_personas(payload, seed_path=seed_path)
    persona = result["personas"][0]

    assert errors == []
    assert persona["title"] == "학생"
    assert persona["income"] is None
    assert persona["sector"] is None
    assert persona["roleArea"] is None
    assert persona["organisation"] is None
    assert persona["roleLevel"] is None


def test_segment_based_seed_generation_preserves_target_counts(tmp_path):
    seed_path = _write_seed_fixture(tmp_path)
    payload, errors = validate_generation_payload(
        {
            "sourceType": "segment_based",
            "segmentInputs": [
                {
                    "id": "segment-1",
                    "name": "가격 민감형",
                    "description": "통신비 절감을 우선합니다.",
                    "targetCount": 2,
                },
                {
                    "id": "segment-2",
                    "name": "프리미엄 혜택형",
                    "description": "부가 혜택과 안정성을 중시합니다.",
                    "targetCount": 1,
                },
            ],
            "totalCount": 3,
            "locale": {"country": "KR", "language": "ko"},
        }
    )

    result = generate_seed_based_personas(payload, seed_path=seed_path)

    assert errors == []
    assert [segment["targetCount"] for segment in result["segments"]] == [2, 1]
    assert len(result["personas"]) == 3


def test_select_seed_applies_candidate_multiplier_and_sample_limit(tmp_path):
    seed_path = _write_seed_fixture(tmp_path)
    payload, errors = validate_generation_payload(
        {
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "nemotronSeedOptions": {"candidateMultiplier": 3, "sampleLimit": 100},
        }
    )
    segments = [
        {
            "id": "service_based",
            "name": "Service based",
            "description": "통신 요금제 추천 서비스",
            "targetCount": 1,
            "characteristics": {"keyTraits": ["요금제"], "ageRangeHint": "30대", "occupationHint": ["기획자"]},
        }
    ]
    profiles = [{"segmentId": "service_based", "title": "서비스 기획자", "age": 34, "gender": "남자", "currentCity": "서울"}]

    selected = select_nemotron_korea_seeds(
        payload=payload,
        segments=segments,
        profiles=profiles,
        existing_personas=[],
        seed_path=seed_path,
    )

    assert errors == []
    assert selected[0]["seed"]["uuid"] == "seed-1"
    assert selected[0]["score"] > 0
    assert selected[0]["rank"] == 1
