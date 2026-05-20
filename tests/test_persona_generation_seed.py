from pathlib import Path

from reopsai.domain.persona.generation import (
    DEFAULT_SEED_PATH,
    generate_seed_based_personas,
    load_seed_personas,
    select_nemotron_korea_seeds,
    validate_generation_payload,
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
    assert result["token_usage"] == {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "model": "gemini-2.5-pro"}
    assert all(persona["schemaVersion"] == 3 for persona in result["personas"])
    assert all("source_type" not in persona for persona in result["personas"])
    assert all("profile" not in persona for persona in result["personas"])


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
