from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

import json
import threading
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

from reopsai.application.persona_service import PersonaService, PersonaServiceResult
from reopsai.infrastructure.persona_figma_client import FIGMA_OAUTH_SCOPE, PersonaFigmaClient


class FakePersonaService:
    def list_folders(self, *, company_id, user_id):
        return PersonaServiceResult(status="ok", data={"data": [{"id": 1, "company_id": company_id, "name": "Default"}]})

    def create_persona(self, *, company_id, user_id, data):
        return PersonaServiceResult(
            status="ok",
            status_code=201,
            data={"data": {"id": 10, "company_id": company_id, "created_by_user_id": user_id, "name": data["name"]}},
        )

    def generate_personas(self, *, company_id, user_id, data):
        return PersonaServiceResult(
            status="ok",
            data={
                "sourceType": "service_based",
                "generationMode": "nemotron_seed_telecom_polished",
                "durationMs": 12,
                "personas": [{"schemaVersion": 3, "name": "Persona A"}],
                "segments": [],
                "telecomServiceUsageContextReferences": [],
                "generationMetadata": {"timingsMs": {"total": 12}},
                "tokenUsage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "model": "nemotron_seed"},
            },
        )

    def suggest_segments(self, *, company_id, user_id, data):
        return PersonaServiceResult(
            status="ok",
            data={
                "segments": [
                    {
                        "id": "segment-suggested-1",
                        "name": "가격 민감형",
                        "description": "통신비 절감을 우선합니다.",
                        "criteria": "프로모션 비교",
                        "targetCount": 1,
                    },
                    {
                        "id": "segment-suggested-2",
                        "name": "혜택 중시형",
                        "description": "멤버십과 부가 혜택을 중시합니다.",
                        "criteria": "VIP 혜택",
                        "targetCount": 1,
                    },
                ],
                "tokenUsage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30, "model": "gemini-2.5-pro"},
            },
        )

    def get_persona(self, *, company_id, user_id, persona_id):
        return PersonaServiceResult(
            status="ok",
            data={
                "persona": {
                    "id": persona_id,
                    "schemaVersion": 3,
                    "company_id": company_id,
                    "name": "Persona A",
                    "sourceType": "segment_based",
                    "sourceData": {"segmentInputs": []},
                    "locale": {"country": "KR", "language": "ko"},
                },
                "memorySettings": {
                    "id": 1,
                    "personaId": persona_id,
                    "enableMemory": True,
                    "memoryStrength": 70,
                    "applyToChat": True,
                    "applyToTests": True,
                },
                "activityStats": {"total": 0, "byType": {"ui_test": 0}, "validated": 0, "correct": 0, "incorrect": 0},
                "recentActivities": [],
                "recentTraits": [],
            },
        )

    def list_combined_tests(self, *, company_id, user_id):
        return PersonaServiceResult(status="ok", data={"data": [{"id": 1, "kind": "ui-test", "company_id": company_id}]})

    def update_ab_test(self, *, company_id, user_id, ab_test_id, data):
        return PersonaServiceResult(status="ok", data={"data": {"id": ab_test_id, "company_id": company_id, "updated_by_user_id": user_id, **data}})

    def delete_ab_test(self, *, company_id, user_id, ab_test_id):
        return PersonaServiceResult(status="ok")

    def generate_interview_questions(self, *, company_id, user_id, data):
        return PersonaServiceResult(
            status="ok",
            data={
                "data": {
                    "questions": {
                        "opening": ["질문"],
                        "tasks": [{"title": "핵심", "questions": ["후속 질문"]}],
                        "closing": ["마무리 질문"],
                        "followup_strategies": ["답변 근거를 더 묻습니다."],
                    }
                }
            },
        )

    def create_interview(self, *, company_id, user_id, data):
        return PersonaServiceResult(status="ok", status_code=201, data={"data": {"id": 7, "company_id": company_id, "name": data["name"], "goal": data["goal"], "results": []}})

    def get_interview(self, *, company_id, user_id, interview_id):
        return PersonaServiceResult(status="ok", data={"data": {"id": interview_id, "company_id": company_id, "results": []}, "results": []})

    def delete_interview(self, *, company_id, user_id, interview_id):
        return PersonaServiceResult(status="ok")

    def run_interview(self, *, company_id, user_id, interview_id, data):
        return PersonaServiceResult(status="ok", data={"data": {"id": interview_id, "status": "completed", "results": []}, "results": []})

    def list_interviews(self, *, company_id, user_id):
        return PersonaServiceResult(status="ok", data={"data": [{"id": 7, "company_id": company_id, "results": []}]})

    def list_interview_personas(self, *, company_id, user_id):
        return PersonaServiceResult(status="ok", data={"data": [{"id": 10, "company_id": company_id}]})

    def figma_connect_url(self, *, company_id, user_id, redirect_uri):
        class FigmaConfig:
            PERSONA_FIGMA_CLIENT_ID = "figma-client-id"

        url = PersonaFigmaClient(config=FigmaConfig).authorization_url(state=f"{company_id}:{user_id}:nonce", redirect_uri=redirect_uri)
        return PersonaServiceResult(status="ok", data={"url": url})

    def delete_figma_file(self, *, company_id, file_id):
        return PersonaServiceResult(status="ok", data={"deleted": {"company_id": company_id, "file_id": file_id}})


def _make_client(monkeypatch, *, claims):
    import reopsai.api.persona as persona_module

    monkeypatch.setattr(persona_module, "persona_service", FakePersonaService())
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(persona_module.persona_bp)
    with app.app_context():
        token = create_access_token(identity="10", additional_claims=claims)
    return app.test_client(), {"Authorization": f"Bearer {token}"}


def test_persona_routes_require_business_company_context(monkeypatch):
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "individual"},
    )

    response = client.get("/api/persona/folders", headers=headers)

    assert response.status_code == 403
    assert response.get_json()["error"] == "Business company context is required"


def test_persona_routes_preserve_success_shape(monkeypatch):
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    folders_response = client.get("/api/persona/folders", headers=headers)
    create_response = client.post(
        "/api/persona/personas",
        headers=headers,
        json={
            "sourceType": "service_based",
            "serviceDescription": "테스트 서비스 설명입니다.",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
        },
    )

    assert folders_response.status_code == 200
    assert folders_response.get_json() == {"success": True, "data": [{"id": 1, "company_id": 100, "name": "Default"}]}
    assert create_response.status_code == 200
    assert create_response.get_json()["personas"] == [{"schemaVersion": 3, "name": "Persona A"}]
    assert "data" not in create_response.get_json()


def test_persona_segment_suggestion_route_preserves_legacy_shape(monkeypatch):
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.post(
        "/api/persona/personas/segments",
        headers=headers,
        json={
            "context": "통신비 절감과 멤버십 혜택을 비교하는 서비스를 위한 세그먼트가 필요합니다.",
            "locale": {"country": "KR", "language": "ko"},
        },
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["segments"][0]["name"] == "가격 민감형"
    assert body["segments"][0]["targetCount"] == 1
    assert "data" not in body


def test_persona_detail_route_matches_legacy_detail_shape(monkeypatch):
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.get("/api/persona/personas/10", headers=headers)
    body = response.get_json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["persona"]["id"] == 10
    assert body["persona"]["sourceType"] == "segment_based"
    assert body["memorySettings"]["personaId"] == 10
    assert body["activityStats"] == {"total": 0, "byType": {"ui_test": 0}, "validated": 0, "correct": 0, "incorrect": 0}
    assert body["recentActivities"] == []
    assert body["recentTraits"] == []
    assert "data" not in body


def test_figma_connect_route_returns_granular_scope_and_backend_redirect(monkeypatch):
    import reopsai.api.persona as persona_module

    monkeypatch.setattr(persona_module.Config, "BACKEND_URL", "https://api.example.com/")
    monkeypatch.setattr(persona_module.Config, "PERSONA_FIGMA_REDIRECT_URI", None)
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.get("/api/persona/figma/connect", headers=headers)
    body = response.get_json()
    parsed = urlparse(body["url"])
    query = parse_qs(parsed.query)

    assert response.status_code == 200
    assert query["scope"] == [FIGMA_OAUTH_SCOPE]
    assert "files%3Aread" not in body["url"]
    assert "file_content%3Aread" in body["url"]
    assert "current_user%3Aread" in body["url"]
    assert query["redirect_uri"] == ["https://api.example.com/api/persona/figma/callback"]


def test_figma_connect_route_does_not_duplicate_api_path(monkeypatch):
    import reopsai.api.persona as persona_module

    monkeypatch.setattr(persona_module.Config, "BACKEND_URL", "https://api.example.com/api")
    monkeypatch.setattr(persona_module.Config, "PERSONA_FIGMA_REDIRECT_URI", None)
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.get("/api/persona/figma/connect", headers=headers)
    redirect_uri = parse_qs(urlparse(response.get_json()["url"]).query)["redirect_uri"][0]

    assert response.status_code == 200
    assert redirect_uri == "https://api.example.com/api/persona/figma/callback"


def test_figma_connect_route_uses_explicit_redirect_uri(monkeypatch):
    import reopsai.api.persona as persona_module

    monkeypatch.setattr(persona_module.Config, "BACKEND_URL", "https://api.example.com")
    monkeypatch.setattr(
        persona_module.Config,
        "PERSONA_FIGMA_REDIRECT_URI",
        "https://oauth.example.com/custom/figma/callback",
    )
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.get("/api/persona/figma/connect", headers=headers)
    redirect_uri = parse_qs(urlparse(response.get_json()["url"]).query)["redirect_uri"][0]

    assert response.status_code == 200
    assert redirect_uri == "https://oauth.example.com/custom/figma/callback"


def test_figma_connect_route_expands_origin_only_explicit_redirect_uri(monkeypatch):
    import reopsai.api.persona as persona_module

    monkeypatch.setattr(persona_module.Config, "BACKEND_URL", "https://api.example.com")
    monkeypatch.setattr(persona_module.Config, "PERSONA_FIGMA_REDIRECT_URI", "http://localhost:5001")
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.get("/api/persona/figma/connect", headers=headers)
    redirect_uri = parse_qs(urlparse(response.get_json()["url"]).query)["redirect_uri"][0]

    assert response.status_code == 200
    assert redirect_uri == "http://localhost:5001/api/persona/figma/callback"


def test_figma_file_delete_route_uses_business_company_context(monkeypatch):
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    response = client.delete("/api/persona/figma/files/42", headers=headers)
    body = response.get_json()

    assert response.status_code == 200
    assert body == {"success": True, "deleted": {"company_id": 100, "file_id": 42}}


def test_persona_test_routes_include_combined_ab_delete_and_interviews(monkeypatch):
    client, headers = _make_client(
        monkeypatch,
        claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
    )

    combined = client.get("/api/persona/tests/combined", headers=headers)
    ab_patch = client.patch("/api/persona/ab-tests/4", headers=headers, json={"status": "draft"})
    ab_delete = client.delete("/api/persona/ab-tests/4", headers=headers)
    questions = client.post("/api/persona/interviews/questions", headers=headers, json={"goal": "신규 요금제 반응"})
    created = client.post("/api/persona/interviews", headers=headers, json={"name": "인터뷰", "goal": "신규 요금제 반응"})
    loaded = client.get("/api/persona/interviews/7", headers=headers)
    run = client.post("/api/persona/interviews/7/run", headers=headers, json={"persona_ids": [10]})

    assert combined.status_code == 200
    assert combined.get_json()["data"][0]["kind"] == "ui-test"
    assert ab_patch.status_code == 200
    assert ab_patch.get_json()["data"]["status"] == "draft"
    assert ab_delete.status_code == 200
    assert questions.get_json()["data"]["questions"]["opening"][0] == "질문"
    assert created.status_code == 201
    assert created.get_json()["data"]["results"] == []
    assert loaded.get_json()["data"]["results"] == []
    assert loaded.get_json()["results"] == []
    assert run.get_json()["data"]["results"] == []
    assert run.get_json()["results"] == []
    assert run.get_json()["data"]["status"] == "completed"


def _stage_usage():
    return {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


def _segmentation_response(*, segments=None, profiles=None):
    return {
        "segments": segments
        or [
            {
                "id": "service_based",
                "name": "Service based",
                "description": "통신 요금제 추천 서비스",
                "target_count": 1,
                "characteristics": {
                    "key_traits": ["요금 비교"],
                    "age_range_hint": "20대",
                    "occupation_hint": ["직장인"],
                },
            }
        ],
        "profiles": profiles
        or [
            {
                "segment_id": "service_based",
                "name": "Persona A",
                "title": "직장인",
                "age": 29,
                "gender": "여자",
                "generation": "millennial",
                "current_city": "서울",
                "current_country": "KR",
                "sector": "IT",
                "role_area": "서비스 기획",
            }
        ],
    }


def _narrative_response(name="Persona A"):
    return {
        "persona": {
            "schemaVersion": 3,
            "name": name,
            "attitudes": "요금제 추천 결과를 바로 믿기보다 근거를 확인하려는 태도가 강합니다.",
            "biography": "충분히 구체적인 생성 결과입니다.",
            "demeanour": "차분하게 비교하고 결정하는 편입니다.",
            "interests": "생활비 관리와 모바일 앱 혜택 비교에 관심이 많습니다.",
            "behaviours": "혜택과 요금 조건을 여러 번 비교한 뒤 변경 여부를 결정합니다.",
            "motivation": "통신비를 줄이면서도 필요한 데이터 사용량은 안정적으로 확보하려 합니다.",
            "upbringing": "실용적인 소비 습관을 중요하게 여기는 환경에서 성장했습니다.",
            "personality": "꼼꼼하고 실용적인 판단을 선호하는 성향입니다.",
            "preferences": "복잡한 설명보다 현재 요금 대비 절감액을 먼저 확인하길 원합니다.",
            "socialContext": "동료와 가족에게 요금제 정보를 자주 공유합니다.",
            "culturalBackground": "모바일 앱으로 생활비를 관리하는 도심 직장인 맥락을 갖고 있습니다.",
            "quote": "추천은 좋지만 왜 그런지 근거가 먼저 보여야 해요.",
            "imagePrompt": "realistic Korean office worker portrait",
            "imageUrl": None,
        }
    }


def _telecom_dimensions_response():
    return {
        "telecom_behavior_dimensions": {
            "brandRetention": {
                "brandRetentionTendency": "낮은 편입니다.",
                "premiumInfraBenefitOrientation": "혜택이 명확하면 고려합니다.",
            },
            "optimizationResource": {
                "optimizationResourceInvestment": "비교에 시간을 쓰는 편입니다.",
                "paymentResistanceLine": "월 납부액이 오르면 즉시 재검토합니다.",
            },
            "informationControl": {
                "informationExplorationStyle": "비교표를 확인합니다.",
                "problemSolvingAutonomy": "스스로 후보를 좁힙니다.",
            },
            "digitalAiOpenness": {
                "aiProviderTrust": "근거가 있으면 신뢰합니다.",
                "personalizationDataSharingScope": "사용량 데이터까지 허용합니다.",
            },
            "telecomLifeCharacteristics": {
                "householdDecisionLeadership": "본인 회선을 직접 결정합니다.",
                "productServiceUnderstanding": "상품 구조를 대략 이해합니다.",
                "telecomServiceUsageContext": "앱에서 청구액과 사용량을 정기적으로 확인합니다.",
            },
        },
    }


def _telecom_scores_response():
    return {
        "telecom_behavior_scores": [
            {
                "key": "brandRetention",
                "label": "브랜드 유지 성향",
                "score": 2,
                "maxScore": 5,
                "rationale": "통신사를 유지하기보다 혜택 조건을 먼저 확인합니다.",
                "evidence": ["낮은 편입니다.", "혜택이 명확하면 고려합니다."],
            },
            {
                "key": "optimizationResource",
                "label": "최적화 리소스 투입",
                "score": 4,
                "maxScore": 5,
                "rationale": "요금제 비교와 재검토에 시간을 쓰는 편입니다.",
                "evidence": ["비교에 시간을 쓰는 편입니다.", "월 납부액이 오르면 즉시 재검토합니다."],
            },
            {
                "key": "informationControl",
                "label": "정보탐색 및 통제 욕구",
                "score": 4,
                "maxScore": 5,
                "rationale": "비교표를 보고 스스로 후보를 좁힙니다.",
                "evidence": ["비교표를 확인합니다.", "스스로 후보를 좁힙니다."],
            },
            {
                "key": "digitalAiOpenness",
                "label": "디지털 및 AI 개방성",
                "score": 4,
                "maxScore": 5,
                "rationale": "근거 기반 AI 추천과 사용량 데이터 제공을 수용합니다.",
                "evidence": ["근거가 있으면 신뢰합니다.", "사용량 데이터까지 허용합니다."],
            },
        ],
    }


class FakeLlmAdapter:
    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "STAGE: segmentation_identity" in prompt:
            content = _segmentation_response()
        elif "STAGE: narrative_polish" in prompt:
            content = _narrative_response()
        elif "STAGE: telecom_dimensions" in prompt:
            content = _telecom_dimensions_response()
        elif "STAGE: telecom_scores" in prompt:
            content = _telecom_scores_response()
        else:
            content = {}
        return {
            "success": True,
            "content": json.dumps(content, ensure_ascii=False),
            "usage": _stage_usage(),
        }


class ConcurrentPersonaGenerationAdapter(FakeLlmAdapter):
    def __init__(self):
        self._lock = threading.Lock()
        self._active_narratives = 0
        self.max_active_narratives = 0

    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "STAGE: segmentation_identity" in prompt:
            content = _segmentation_response(
                segments=[
                    {
                        "id": "service_based",
                        "name": "Service based",
                        "description": "통신 요금제 추천 서비스",
                        "target_count": 3,
                        "characteristics": {
                            "key_traits": ["요금 비교"],
                            "age_range_hint": "20대",
                            "occupation_hint": ["직장인"],
                        },
                    }
                ],
                profiles=[
                    {
                        "segment_id": "service_based",
                        "name": f"Persona {index}",
                        "title": "직장인",
                        "age": 29 + index,
                        "gender": "여자",
                        "generation": "millennial",
                        "current_city": "서울",
                        "current_country": "KR",
                        "sector": "IT",
                        "role_area": "서비스 기획",
                    }
                    for index in range(3)
                ],
            )
        elif "STAGE: narrative_polish" in prompt:
            with self._lock:
                self._active_narratives += 1
                self.max_active_narratives = max(self.max_active_narratives, self._active_narratives)
            try:
                time.sleep(0.05)
                persona = _narrative_response()["persona"]
                persona.pop("name", None)
                content = {"persona": persona}
            finally:
                with self._lock:
                    self._active_narratives -= 1
        elif "STAGE: telecom_dimensions" in prompt:
            content = _telecom_dimensions_response()
        elif "STAGE: telecom_scores" in prompt:
            content = _telecom_scores_response()
        else:
            content = {}
        return {
            "success": True,
            "content": json.dumps(content, ensure_ascii=False),
            "usage": _stage_usage(),
        }


def test_persona_generation_service_matches_legacy_preview_contract():
    service = PersonaService(
        llm_adapter=FakeLlmAdapter(),
        image_generator=lambda persona: "data:image/png;base64,aW1hZ2U=",
    )

    result = service.generate_personas(
        company_id=100,
        user_id=10,
        data={
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "20대 직장인",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": True,
            "skipExistingPersonas": True,
        },
    )
    body = result.data

    assert result.status_code == 200
    assert body["sourceType"] == "service_based"
    assert body["generationMode"] == "nemotron_seed_telecom_polished"
    assert isinstance(body["durationMs"], int)
    assert len(body["personas"]) == 1
    assert body["personas"][0]["schemaVersion"] == 3
    assert body["personas"][0]["imageUrl"].startswith("data:image/png;base64,")
    assert body["personas"][0]["telecomBehaviorScores"][0]["key"] == "brandRetention"
    assert body["personas"][0]["telecomBehaviorScores"][0]["rationale"]
    assert body["personas"][0]["telecomBehaviorDimensions"]["telecomLifeCharacteristics"]["telecomServiceUsageContext"].startswith("앱에서")
    assert "segments" in body
    assert "generationMetadata" in body
    assert body["tokenUsage"] == {"inputTokens": 40, "outputTokens": 80, "totalTokens": 120, "model": "gemini-2.5-flash"}
    assert "data" not in body


def test_persona_generation_processes_persona_stages_concurrently(monkeypatch):
    monkeypatch.setenv("PERSONA_GENERATION_MAX_CONCURRENCY", "3")
    adapter = ConcurrentPersonaGenerationAdapter()
    service = PersonaService(llm_adapter=adapter, image_generator=lambda persona: None)

    result = service.generate_personas(
        company_id=100,
        user_id=10,
        data={
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "20대 직장인",
            "totalCount": 3,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": False,
            "skipExistingPersonas": True,
        },
    )

    assert result.status_code == 200
    assert len(result.data["personas"]) == 3
    assert adapter.max_active_narratives > 1


class SparseThenRichLlmAdapter:
    def __init__(self):
        self.calls = 0
        self.narrative_calls = 0

    def generate_response(self, prompt, generation_config=None, model_name=None):
        self.calls += 1
        if "STAGE: segmentation_identity" in prompt:
            content = _segmentation_response()
        elif "STAGE: narrative_polish" in prompt:
            self.narrative_calls += 1
            if self.narrative_calls == 1:
                return {
                    "success": True,
                    "content": "{not valid json",
                    "usage": _stage_usage(),
                }
            else:
                content = _narrative_response()
        elif "STAGE: telecom_dimensions" in prompt:
            content = _telecom_dimensions_response()
        elif "STAGE: telecom_scores" in prompt:
            content = _telecom_scores_response()
        else:
            content = {}
        return {
            "success": True,
            "content": json.dumps(content, ensure_ascii=False),
            "usage": _stage_usage(),
        }


def test_persona_generation_retries_sparse_llm_response_before_success():
    adapter = SparseThenRichLlmAdapter()
    service = PersonaService(llm_adapter=adapter, image_generator=lambda persona: None)

    result = service.generate_personas(
        company_id=100,
        user_id=10,
        data={
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "20대 직장인",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": True,
            "skipExistingPersonas": True,
        },
    )

    assert adapter.narrative_calls == 2
    assert result.status_code == 200
    assert result.data["personas"][0]["behaviours"]


def test_persona_generation_skips_images_when_requested():
    service = PersonaService(
        llm_adapter=FakeLlmAdapter(),
        image_generator=lambda persona: (_ for _ in ()).throw(AssertionError("image generator should not run")),
    )

    result = service.generate_personas(
        company_id=100,
        user_id=10,
        data={
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "20대 직장인",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": False,
            "skipExistingPersonas": True,
        },
    )

    assert result.status_code == 200
    assert result.data["personas"][0]["imageUrl"] is None


class SegmentBasedLlmAdapter(FakeLlmAdapter):
    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "STAGE: segmentation_identity" in prompt:
            content = _segmentation_response(
                segments=[
                    {
                        "id": "segment-1",
                        "name": "가격 민감형",
                        "description": "통신비 절감을 우선합니다.",
                        "target_count": 2,
                        "characteristics": {
                            "key_traits": ["절약"],
                            "age_range_hint": "20-40",
                            "occupation_hint": ["직장인"],
                        },
                    }
                ],
                profiles=[
                    {
                        "segment_id": "segment-1",
                        "name": "Persona A",
                        "title": "직장인",
                        "age": 29,
                        "gender": "여자",
                        "current_city": "서울",
                        "role_area": "서비스 기획",
                    },
                    {
                        "segment_id": "segment-1",
                        "name": "Persona B",
                        "title": "마케터",
                        "age": 36,
                        "gender": "남자",
                        "current_city": "부산",
                        "role_area": "마케팅",
                    },
                ],
            )
            return {"success": True, "content": json.dumps(content, ensure_ascii=False), "usage": _stage_usage()}
        return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)


def test_persona_generation_preserves_segment_based_source_and_counts():
    service = PersonaService(llm_adapter=SegmentBasedLlmAdapter(), image_generator=lambda persona: None)

    result = service.generate_personas(
        company_id=100,
        user_id=10,
        data={
            "sourceType": "segment_based",
            "segmentInputs": [
                {
                    "id": "segment-1",
                    "name": "가격 민감형",
                    "description": "통신비 절감을 우선하는 사용자",
                    "criteria": "월 통신비를 자주 비교함",
                    "targetCount": 2,
                }
            ],
            "totalCount": 2,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": False,
            "skipExistingPersonas": True,
        },
    )

    assert result.status_code == 200
    assert result.data["sourceType"] == "segment_based"
    assert result.data["segments"][0]["id"] == "segment-1"
    assert result.data["segments"][0]["targetCount"] == 2
    assert len(result.data["personas"]) == 2


class ExistingSummaryRepository:
    calls = []

    @staticmethod
    def list_existing_persona_summaries(session, *, company_id, user_id=None, limit=None):
        ExistingSummaryRepository.calls.append({"company_id": company_id, "user_id": user_id, "limit": limit})
        return [
            {
                "name": "Persona A",
                "age": 30,
                "generation": "millennial",
                "title": "기획자",
                "roleArea": "기획",
                "personality": "신중함",
            }
        ]


@contextmanager
def fake_session_factory():
    yield object()


def test_persona_generation_uses_visible_existing_summary_by_default():
    ExistingSummaryRepository.calls = []
    service = PersonaService(
        repository=ExistingSummaryRepository,
        session_factory=fake_session_factory,
        llm_adapter=FakeLlmAdapter(),
        image_generator=lambda persona: None,
    )

    result = service.generate_personas(
        company_id=200,
        user_id=10,
        data={
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "20대 직장인",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": False,
            "skipExistingPersonas": False,
        },
    )

    assert result.status_code == 200
    assert ExistingSummaryRepository.calls == [{"company_id": 200, "user_id": 10, "limit": None}]


def test_persona_generation_skips_db_summary_when_requested():
    ExistingSummaryRepository.calls = []
    service = PersonaService(
        repository=ExistingSummaryRepository,
        session_factory=fake_session_factory,
        llm_adapter=FakeLlmAdapter(),
        image_generator=lambda persona: None,
    )

    result = service.generate_personas(
        company_id=200,
        user_id=10,
        data={
            "sourceType": "service_based",
            "serviceDescription": "통신 요금제 추천 서비스",
            "targetAudience": "20대 직장인",
            "totalCount": 1,
            "locale": {"country": "KR", "language": "ko"},
            "includeImages": False,
            "skipExistingPersonas": True,
            "existingPersonas": [{"name": "Payload Persona", "age": 32}],
        },
    )

    assert result.status_code == 200
    assert ExistingSummaryRepository.calls == []
