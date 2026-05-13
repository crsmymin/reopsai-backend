import sys
import types
from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


def _install_route_import_fakes():
    fake_openai_module = types.ModuleType("services.openai_service")
    fake_openai_module.openai_service = object()
    sys.modules.setdefault("services.openai_service", fake_openai_module)

    fake_gemini_module = types.ModuleType("services.gemini_service")
    fake_gemini_module.gemini_service = object()
    sys.modules.setdefault("services.gemini_service", fake_gemini_module)

    fake_vector_module = types.ModuleType("services.vector_service")
    fake_vector_module.vector_service = object()
    sys.modules.setdefault("services.vector_service", fake_vector_module)

    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda *args, **kwargs: None
    sys.modules.setdefault("requests", fake_requests)

    fake_bs4 = types.ModuleType("bs4")
    fake_bs4.BeautifulSoup = lambda *args, **kwargs: None
    sys.modules.setdefault("bs4", fake_bs4)


class FakeWorkspaceService:
    def list_projects(self, owner_ids):
        return [{"id": 1, "owner_id": owner_ids[0], "name": "Project"}]

    def create_project(self, *, owner_id, name, product_url="", tags=None):
        return {"id": 2, "owner_id": owner_id, "name": name, "product_url": product_url, "keywords": tags}

    def update_project(self, *, project_id, owner_id, data):
        if not data:
            return SimpleNamespace(status="empty_update", data=None)
        return SimpleNamespace(status="ok", data={"id": project_id, "owner_id": owner_id, **data})

    def get_project(self, *, project_id, owner_ids):
        return SimpleNamespace(status="ok", data={"id": project_id, "owner_id": owner_ids[0], "name": "Project"})


class FakeWorkspaceAiService:
    def generate_project_name(self, *, study_name, problem_definition):
        return SimpleNamespace(status="ok", data={"projectName": "Generated Project", "tags": ["ux"]}, error=None)

    def generate_study_name(self, *, problem_definition):
        return SimpleNamespace(status="ok", data={"studyName": "Generated Study"}, error=None)

    def stream_tags(self, *, project_title, product_url):
        yield 'data: {"tags": ["ux"], "done": true}\n\n'


def _make_workspace_client(monkeypatch):
    _install_route_import_fakes()
    import reopsai.api.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "workspace_service", FakeWorkspaceService())
    monkeypatch.setattr(workspace_module, "workspace_ai_service", FakeWorkspaceAiService())

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(workspace_module.workspace_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}", "X-User-ID": "10"}


def test_workspace_blueprint_preserves_route_map():
    _install_route_import_fakes()
    import reopsai.api.workspace as workspace_module

    app = Flask(__name__)
    app.register_blueprint(workspace_module.workspace_bp)

    expected_routes = {
        ("/api/workspace/projects", "GET"),
        ("/api/workspace/projects-with-studies", "GET"),
        ("/api/workspace/projects", "POST"),
        ("/api/workspace/projects/<int:project_id>", "DELETE"),
        ("/api/workspace/projects/<int:project_id>", "PUT"),
        ("/api/workspace/generate-project-name", "POST"),
        ("/api/workspace/generate-study-name", "POST"),
        ("/api/workspace/generate-tags", "POST"),
        ("/api/studies/<int:study_id>", "GET"),
        ("/api/projects/<int:project_id>", "GET"),
        ("/api/projects/<int:project_id>/studies", "GET"),
        ("/api/studies/<int:study_id>/schedule", "GET"),
        ("/api/artifacts/<int:artifact_id>", "PUT"),
        ("/api/studies/<int:study_id>/artifacts", "GET"),
        ("/api/studies/<int:study_id>/survey/deployments", "GET"),
        ("/api/artifacts/<int:artifact_id>/stream", "GET"),
        ("/api/studies/<int:study_id>", "DELETE"),
        ("/api/artifacts/<int:artifact_id>", "DELETE"),
        ("/api/studies/<int:study_id>/regenerate-plan", "POST"),
        ("/api/studies/<int:study_id>", "PUT"),
    }
    actual_routes = {
        (str(rule.rule), method)
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("workspace.")
        for method in rule.methods
        if method not in {"HEAD", "OPTIONS"}
    }

    assert expected_routes <= actual_routes


def test_workspace_project_routes_preserve_response_shape(monkeypatch):
    client, headers = _make_workspace_client(monkeypatch)

    list_response = client.get("/api/workspace/projects", headers=headers)
    assert list_response.status_code == 200
    assert list_response.get_json() == {
        "success": True,
        "projects": [{"id": 1, "owner_id": 10, "name": "Project"}],
    }

    create_response = client.post(
        "/api/workspace/projects",
        headers=headers,
        json={"name": "New Project", "productUrl": "https://example.com", "tags": ["ux"]},
    )
    assert create_response.status_code == 200
    assert create_response.get_json()["project"]["name"] == "New Project"

    update_response = client.put(
        "/api/workspace/projects/2",
        headers=headers,
        json={"name": "Updated"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json() == {
        "success": True,
        "message": "프로젝트 정보가 업데이트되었습니다.",
        "data": {"id": 2, "owner_id": 10, "name": "Updated"},
    }

    project_response = client.get("/api/projects/2", headers=headers)
    assert project_response.status_code == 200
    assert project_response.get_json() == {"id": 2, "owner_id": 10, "name": "Project"}


def test_workspace_ai_routes_preserve_response_shape(monkeypatch):
    client, headers = _make_workspace_client(monkeypatch)

    project_name = client.post(
        "/api/workspace/generate-project-name",
        headers=headers,
        json={"studyName": "Study", "problemDefinition": "Problem"},
    )
    assert project_name.status_code == 200
    assert project_name.get_json() == {
        "success": True,
        "projectName": "Generated Project",
        "tags": ["ux"],
    }

    study_name = client.post(
        "/api/workspace/generate-study-name",
        headers=headers,
        json={"problemDefinition": "충분히 긴 문제 정의입니다."},
    )
    assert study_name.status_code == 200
    assert study_name.get_json() == {"success": True, "studyName": "Generated Study"}

    tags = client.post(
        "/api/workspace/generate-tags",
        headers=headers,
        json={"project_title": "Project"},
    )
    assert tags.status_code == 200
    assert tags.data == b'data: {"tags": ["ux"], "done": true}\n\n'
