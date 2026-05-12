from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager


class FakeDemoService:
    def __init__(self):
        self.status = "ok"

    def db_ready(self):
        return True

    def login(self, *, tier_type):
        if self.status == "account_failed":
            return SimpleNamespace(status="account_failed", data=None, error="Failed to get or create individual demo account")
        return SimpleNamespace(
            status="ok",
            data={
                "user_id": 10,
                "claims": {"tier": "free", "account_type": "individual", "password_reset_required": False},
                "user": {"id": 10, "email": "test@example.com", "tier": "free"},
            },
            error=None,
        )


def _make_demo_client(monkeypatch):
    import reopsai.api.demo as module

    fake_service = FakeDemoService()
    monkeypatch.setattr(module, "demo_service", fake_service)
    monkeypatch.setattr(module, "auth_response", lambda payload, access_token: (payload, 200))

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
    )
    JWTManager(app)
    app.register_blueprint(module.demo_bp)
    return app.test_client(), fake_service


def test_demo_verify_and_login_response_shape(monkeypatch):
    client, service = _make_demo_client(monkeypatch)

    verify = client.post("/api/demo/verify", json={"password": "pxd1105"})
    assert verify.status_code == 200
    assert verify.get_json() == {"success": True, "message": "Password verified"}

    invalid_password = client.post("/api/demo/login", json={"password": "bad", "tier_type": "individual"})
    assert invalid_password.status_code == 401
    assert invalid_password.get_json() == {"error": "Invalid password"}

    invalid_tier = client.post("/api/demo/login", json={"password": "pxd1105", "tier_type": "bad"})
    assert invalid_tier.status_code == 400
    assert invalid_tier.get_json() == {"error": "Invalid tier type"}

    login = client.post("/api/demo/login", json={"password": "pxd1105", "tier_type": "individual"})
    assert login.status_code == 200
    assert login.get_json()["success"] is True
    assert login.get_json()["token_type"] == "bearer"
    assert login.get_json()["user"] == {"id": 10, "email": "test@example.com", "tier": "free"}

    service.status = "account_failed"
    failed = client.post("/api/demo/login", json={"password": "pxd1105", "tier_type": "individual"})
    assert failed.status_code == 500
    assert failed.get_json() == {"error": "Failed to get or create individual demo account"}
