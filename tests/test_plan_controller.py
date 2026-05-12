import sys
import types
from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class NoopThread:
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon

    def start(self):
        return None


class FakePlanService:
    def __init__(self):
        self.status = "ok"

    def db_ready(self):
        return True

    def create_oneshot_records(self, **kwargs):
        if self.status != "ok":
            return SimpleNamespace(status=self.status, data=None)
        return SimpleNamespace(
            status="ok",
            data={"study_id": 101, "study_slug": "slug", "artifact_id": 202, "project_keywords": ["ux"]},
        )

    def create_conversation_records(self, **kwargs):
        return self.create_oneshot_records(**kwargs)

    def cleanup_created_records(self, **kwargs):
        return SimpleNamespace(status="ok")


class FakePlanGenerationService:
    def __init__(self):
        self.background_calls = []

    def stream_study_helper_chat(self, *, data):
        yield 'data: {"content": "ok", "done": true}\n\n'

    @staticmethod
    def ledger_cards_to_context_text(ledger_cards, max_chars=12000):
        return "ledger"

    @staticmethod
    def extract_selected_methodologies_from_ledger(ledger_cards):
        return ["UT"]

    def generate_oneshot_plan_background(self, **kwargs):
        self.background_calls.append(("oneshot", kwargs))
        return SimpleNamespace(status="ok")

    def generate_conversation_plan_background(self, **kwargs):
        self.background_calls.append(("conversation", kwargs))
        return SimpleNamespace(status="ok")

    def build_conversation_recommendation(self, *, data):
        return SimpleNamespace(
            status="ok",
            data={
                "success": True,
                "draft_cards": [{"type": "research_goal", "title": "Goal"}],
                "missing_questions": [],
                "next_question": None,
                "message": "추천을 생성했습니다.",
                "step": int(data.get("step", 0)),
                "mode": data.get("mode", "recommend"),
            },
        )


def _install_plan_import_fakes():
    fake_openai = types.ModuleType("services.openai_service")
    fake_openai.openai_service = SimpleNamespace(generate_response=lambda *args, **kwargs: {"success": True, "content": "{}"})
    sys.modules.setdefault("services.openai_service", fake_openai)

    fake_gemini = types.ModuleType("services.gemini_service")
    fake_gemini.gemini_service = SimpleNamespace(generate_response=lambda *args, **kwargs: {"success": True, "content": "plan"})
    sys.modules.setdefault("services.gemini_service", fake_gemini)

    fake_vector = types.ModuleType("services.vector_service")
    fake_vector.vector_service = SimpleNamespace(
        improved_service=SimpleNamespace(
            hybrid_search=lambda **kwargs: {"principles": "", "examples": ""},
            context_optimization=lambda text, max_length=1000: text,
        )
    )
    sys.modules.setdefault("services.vector_service", fake_vector)


def _make_plan_client(monkeypatch):
    _install_plan_import_fakes()
    import routes.plan_routes as plan_module

    fake_service = FakePlanService()
    fake_generation_service = FakePlanGenerationService()
    monkeypatch.setattr(plan_module, "plan_service", fake_service)
    monkeypatch.setattr(plan_module, "plan_generation_service", fake_generation_service)
    monkeypatch.setattr(plan_module.threading, "Thread", NoopThread)
    monkeypatch.setattr(plan_module, "_build_llm_usage_context", lambda user_id, request_id: {})
    monkeypatch.setattr(plan_module, "run_with_llm_usage_context", lambda context, func: func())

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(plan_module.plan_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}", "X-User-ID": "10"}, fake_service, fake_generation_service


def test_plan_create_oneshot_controller_shape(monkeypatch):
    client, headers, service, _generation_service = _make_plan_client(monkeypatch)

    response = client.post(
        "/api/generator/create-plan-oneshot",
        headers=headers,
        json={
            "projectId": 1,
            "requestId": "plan-controller-create",
            "formData": {"studyName": "Study", "problemDefinition": "Problem", "methodologies": ["UT"]},
        },
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "study_id": 101,
        "study_slug": "slug",
        "artifact_id": 202,
        "request_id": "plan-controller-create",
        "message": "연구가 생성되었습니다. 계획서를 생성하고 있습니다...",
    }

    service.status = "forbidden"
    forbidden = client.post(
        "/api/generator/create-plan-oneshot",
        headers=headers,
        json={
            "projectId": 1,
            "requestId": "plan-controller-forbidden",
            "formData": {"studyName": "Study", "problemDefinition": "Problem"},
        },
    )
    assert forbidden.status_code == 403
    assert forbidden.get_json() == {"success": False, "error": "접근 권한이 없습니다."}


def test_plan_finalize_controller_shape(monkeypatch):
    client, headers, _service, _generation_service = _make_plan_client(monkeypatch)

    response = client.post(
        "/api/generator/conversation-maker/finalize-oneshot",
        headers=headers,
        json={
            "projectId": 1,
            "requestId": "plan-controller-finalize",
            "studyName": "Study",
            "ledger_cards": [{"type": "methodology_set", "fields": {"methods": ["UT"]}}],
        },
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "study_id": 101,
        "study_slug": "slug",
        "artifact_id": 202,
        "request_id": "plan-controller-finalize",
        "message": "연구가 생성되었습니다. 계획서를 생성하고 있습니다...",
    }

    invalid = client.post(
        "/api/generator/conversation-maker/finalize-oneshot",
        headers=headers,
        json={"projectId": 1, "studyName": "", "ledger_cards": []},
    )
    assert invalid.status_code == 400
    assert invalid.get_json() == {"success": False, "error": "studyName은 필수입니다."}


def test_plan_helper_and_conversation_controller_shape(monkeypatch):
    client, headers, _service, _generation_service = _make_plan_client(monkeypatch)

    helper = client.post(
        "/api/study-helper/chat",
        headers=headers,
        json={"message": "help", "context": {}},
    )
    assert helper.status_code == 200
    assert helper.data == b'data: {"content": "ok", "done": true}\n\n'

    conversation = client.post(
        "/api/conversation/message",
        headers=headers,
        json={"step": 1, "mode": "recommend", "conversation": [], "ledger_cards": []},
    )
    assert conversation.status_code == 200
    assert conversation.get_json() == {
        "success": True,
        "draft_cards": [{"type": "research_goal", "title": "Goal"}],
        "missing_questions": [],
        "next_question": None,
        "message": "추천을 생성했습니다.",
        "step": 1,
        "mode": "recommend",
    }
