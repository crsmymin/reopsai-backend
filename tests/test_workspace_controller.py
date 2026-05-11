import sys
import types
from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


def _install_route_import_fakes():
    fake_openai_module = types.ModuleType("services.openai_service")
    fake_openai_module.openai_service = object()
    sys.modules.setdefault("services.openai_service", fake_openai_module)

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


def _make_workspace_client(monkeypatch):
    _install_route_import_fakes()
    import routes.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "workspace_service", FakeWorkspaceService())

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
