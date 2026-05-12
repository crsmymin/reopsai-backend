from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeB2bService:
    def get_my_team(self, *, user_id, company_id_claim):
        return SimpleNamespace(
            status="ok",
            data={
                "company": {"id": company_id_claim, "name": "Acme", "status": "active", "created_at": None},
                "members": [{"user_id": user_id, "email": "owner@example.com", "role": "owner"}],
            },
        )

    def add_team_member(self, *, user_id, company_id_claim, email, role, department):
        return SimpleNamespace(status="ok", data=None)


def _make_b2b_client(monkeypatch):
    import reopsai.api.b2b as b2b_module

    monkeypatch.setattr(b2b_module, "b2b_service", FakeB2bService())

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(b2b_module.b2b_bp)

    with app.app_context():
        token = create_access_token(
            identity="10",
            additional_claims={"tier": "enterprise", "account_type": "business", "company_id": 100},
        )
    return app.test_client(), {"Authorization": f"Bearer {token}"}


def test_b2b_team_routes_preserve_response_shape(monkeypatch):
    client, headers = _make_b2b_client(monkeypatch)

    team_response = client.get("/api/b2b/team", headers=headers)
    assert team_response.status_code == 200
    assert team_response.get_json() == {
        "success": True,
        "company": {"id": 100, "name": "Acme", "status": "active", "created_at": None},
        "members": [{"user_id": 10, "email": "owner@example.com", "role": "owner"}],
    }

    add_response = client.post(
        "/api/b2b/team/members",
        headers=headers,
        json={"email": "member@example.com", "role": "member"},
    )
    assert add_response.status_code == 200
    assert add_response.get_json() == {"success": True}
