from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeStudyService:
    def __init__(self):
        self.status = "ok"

    def db_ready(self):
        return True

    def get_study_by_slug(self, *, slug, owner_ids):
        if self.status != "ok":
            return SimpleNamespace(status=self.status, data=None)
        return SimpleNamespace(status="ok", data={"id": 1, "slug": slug, "projects": {"owner_id": owner_ids[0]}})

    def get_project_by_slug(self, *, slug, owner_ids):
        if self.status != "ok":
            return SimpleNamespace(status=self.status, data=None)
        return SimpleNamespace(status="ok", data={"id": 2, "slug": slug, "owner_id": owner_ids[0]})


def _make_study_client(monkeypatch):
    import reopsai_backend.api.study as module

    fake_service = FakeStudyService()
    monkeypatch.setattr(module, "study_service", fake_service)

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(module.study_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}", "X-User-ID": "10"}, fake_service


def test_study_slug_controller_response_shape(monkeypatch):
    client, headers, service = _make_study_client(monkeypatch)

    response = client.get("/api/studies/by-slug/study-slug", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {"id": 1, "slug": "study-slug", "projects": {"owner_id": 10}}

    service.status = "forbidden"
    forbidden = client.get("/api/studies/by-slug/study-slug", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.get_json() == {"error": "접근 권한이 없습니다."}


def test_project_slug_controller_response_shape(monkeypatch):
    client, headers, service = _make_study_client(monkeypatch)

    response = client.get("/api/projects/by-slug/project-slug", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {"id": 2, "slug": "project-slug", "owner_id": 10}

    service.status = "not_found"
    missing = client.get("/api/projects/by-slug/project-slug", headers=headers)
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "프로젝트를 찾을 수 없거나 접근 권한이 없습니다."}
