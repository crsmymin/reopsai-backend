from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeThread:
    def __init__(self, target):
        self.target = target

    def start(self):
        self.target()


class FakeGuidelineService:
    def __init__(self):
        self.background_calls = []

    def extract_methods(self, **kwargs):
        if kwargs["research_plan"] == "fail":
            return SimpleNamespace(status="llm_failed", data=None, error="LLM 응답 실패")
        return SimpleNamespace(status="ok", data={"methodologies": ["UT", "Interview"]})

    def create_guideline_generation(self, **kwargs):
        study_id = kwargs["study_id"]
        if study_id == 404:
            return SimpleNamespace(status="not_found", data=None)
        if study_id == 405:
            return SimpleNamespace(status="project_not_found", data=None)
        if study_id == 500:
            return SimpleNamespace(status="db_unavailable", data=None)
        return SimpleNamespace(
            status="ok",
            data={"artifact_id": 100, "project_id": 20, "project_keywords": ["ux"]},
        )

    def generate_guideline_background(self, **kwargs):
        self.background_calls.append(kwargs)
        return SimpleNamespace(status="ok")


def _make_guideline_client(monkeypatch):
    import reopsai.api.guideline as guideline_module

    fake_service = FakeGuidelineService()
    monkeypatch.setattr(guideline_module, "guideline_service", fake_service)
    monkeypatch.setattr(guideline_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(guideline_module, "build_llm_usage_context", lambda feature_key: {"feature_key": feature_key})
    monkeypatch.setattr(
        guideline_module,
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
    app.register_blueprint(guideline_module.guideline_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}"}, fake_service


def test_guideline_extract_routes_preserve_response_shapes(monkeypatch):
    client, headers, _service = _make_guideline_client(monkeypatch)

    first = client.post("/api/guideline/extract-methods", headers=headers, json={"research_plan": "plan"})
    assert first.status_code == 200
    assert first.get_json() == {"success": True, "methodologies": ["UT", "Interview"]}

    second = client.post("/api/extract-methodologies", headers=headers, json={"research_plan": "plan"})
    assert second.status_code == 200
    assert second.get_json() == {"success": True, "methodologies": ["UT", "Interview"]}

    empty = client.post("/api/extract-methodologies", headers=headers, json={"research_plan": ""})
    assert empty.status_code == 400
    assert empty.get_json() == {"success": False, "error": "계획서가 비어있습니다"}

    failed = client.post("/api/extract-methodologies", headers=headers, json={"research_plan": "fail"})
    assert failed.status_code == 500
    assert failed.get_json() == {"success": False, "error": "LLM 응답 실패"}


def test_guideline_create_and_generate_controller_shapes(monkeypatch):
    client, headers, service = _make_guideline_client(monkeypatch)

    created = client.post(
        "/api/guideline/create-and-generate",
        headers=headers,
        json={"study_id": 1, "research_plan": "plan", "methodologies": ["UT"]},
    )
    assert created.status_code == 200
    assert created.get_json() == {"success": True, "artifact_id": 100}
    assert service.background_calls == [
        {
            "artifact_id": 100,
            "research_plan": "plan",
            "methodologies": ["UT"],
            "project_keywords": ["ux"],
        }
    ]


def test_guideline_create_and_generate_error_mapping(monkeypatch):
    client, headers, _service = _make_guideline_client(monkeypatch)

    invalid = client.post("/api/guideline/create-and-generate", headers=headers, json={"study_id": "bad"})
    assert invalid.status_code == 400
    assert invalid.get_json() == {"success": False, "error": "유효하지 않은 study_id입니다."}

    missing = client.post("/api/guideline/create-and-generate", headers=headers, json={"study_id": 404})
    assert missing.status_code == 404
    assert missing.get_json() == {"success": False, "error": "연구를 찾을 수 없습니다"}

    no_project = client.post("/api/guideline/create-and-generate", headers=headers, json={"study_id": 405})
    assert no_project.status_code == 404
    assert no_project.get_json() == {"success": False, "error": "프로젝트 정보를 찾을 수 없습니다"}

    db_down = client.post("/api/guideline/create-and-generate", headers=headers, json={"study_id": 500})
    assert db_down.status_code == 500
    assert db_down.get_json() == {"success": False, "error": "데이터베이스 연결 실패"}
