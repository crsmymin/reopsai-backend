from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeThread:
    def __init__(self, target):
        self.target = target

    def start(self):
        self.target()


class FakeSurveyService:
    def __init__(self):
        self.background_calls = []

    def diagnose_survey(self, **kwargs):
        return SimpleNamespace(status="ok", data=[{"check_item_key": "clarity", "pass": True}])

    def generate_draft(self, **kwargs):
        return SimpleNamespace(status="ok", data={"draft": ["fix"]})

    def polish_plan(self, **kwargs):
        return SimpleNamespace(status="ok", data={"polished": "ok"})

    def create_survey_generation(self, **kwargs):
        study_id = kwargs["study_id"]
        if study_id == 404:
            return SimpleNamespace(status="not_found", data=None)
        if study_id == 405:
            return SimpleNamespace(status="project_not_found", data=None)
        if study_id == 500:
            return SimpleNamespace(status="db_unavailable", data=None)
        if study_id == 501:
            return SimpleNamespace(status="ok", data={"artifact_id": None, "project_keywords": []})
        return SimpleNamespace(status="ok", data={"artifact_id": 100, "project_id": 20, "project_keywords": ["ux"]})

    def generate_survey_background(self, **kwargs):
        self.background_calls.append(kwargs)
        return SimpleNamespace(status="ok")


def _make_survey_client(monkeypatch):
    import routes.survey_routes as survey_module

    fake_service = FakeSurveyService()
    monkeypatch.setattr(survey_module, "survey_service", fake_service)
    monkeypatch.setattr(survey_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(survey_module, "build_llm_usage_context", lambda feature_key: {"feature_key": feature_key})
    monkeypatch.setattr(
        survey_module,
        "run_with_llm_usage_context",
        lambda context, func, **kwargs: func(**kwargs),
    )

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(survey_module.survey_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}"}, fake_service


def test_survey_diagnoser_controller_response_shapes(monkeypatch):
    client, headers, _service = _make_survey_client(monkeypatch)

    diagnosed = client.post("/api/survey-diagnoser/diagnose", headers=headers, json={"survey_text": "survey"})
    assert diagnosed.status_code == 200
    assert diagnosed.get_json() == {
        "success": True,
        "response": [{"check_item_key": "clarity", "pass": True}],
    }

    draft = client.post(
        "/api/survey-diagnoser/generate-draft",
        headers=headers,
        json={"survey_text": "survey", "item_to_fix": "clarity"},
    )
    assert draft.status_code == 200
    assert draft.get_json() == {"success": True, "response": {"draft": ["fix"]}}

    polished = client.post(
        "/api/survey-diagnoser/polish-plan",
        headers=headers,
        json={"survey_text": "survey", "confirmed_survey": {}},
    )
    assert polished.status_code == 200
    assert polished.get_json() == {"success": True, "response": {"polished": "ok"}}


def test_survey_create_and_generate_controller_shape(monkeypatch):
    client, headers, service = _make_survey_client(monkeypatch)

    created = client.post(
        "/api/survey/create-and-generate",
        headers=headers,
        json={"study_id": 1, "research_plan": "plan"},
    )
    assert created.status_code == 200
    assert created.get_json() == {"success": True, "artifact_id": 100}
    assert service.background_calls == [
        {"artifact_id": 100, "research_plan": "plan", "project_keywords": ["ux"]}
    ]


def test_survey_create_and_generate_error_mapping(monkeypatch):
    client, headers, _service = _make_survey_client(monkeypatch)

    invalid = client.post("/api/survey/create-and-generate", headers=headers, json={"study_id": "bad"})
    assert invalid.status_code == 400
    assert invalid.get_json() == {"success": False, "error": "유효하지 않은 study_id입니다."}

    missing = client.post("/api/survey/create-and-generate", headers=headers, json={"study_id": 404})
    assert missing.status_code == 404
    assert missing.get_json() == {"success": False, "error": "연구를 찾을 수 없습니다"}

    no_project = client.post("/api/survey/create-and-generate", headers=headers, json={"study_id": 405})
    assert no_project.status_code == 404
    assert no_project.get_json() == {"success": False, "error": "프로젝트 정보를 찾을 수 없습니다"}

    db_down = client.post("/api/survey/create-and-generate", headers=headers, json={"study_id": 500})
    assert db_down.status_code == 500
    assert db_down.get_json() == {"success": False, "error": "데이터베이스 연결 실패"}

    no_artifact = client.post("/api/survey/create-and-generate", headers=headers, json={"study_id": 501})
    assert no_artifact.status_code == 500
    assert no_artifact.get_json() == {"success": False, "error": "스크리너 저장소 생성에 실패했습니다."}
