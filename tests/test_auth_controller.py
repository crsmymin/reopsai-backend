from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeAuthService:
    def get_profile(self, *, user_id, jwt_claims):
        return SimpleNamespace(
            status="ok",
            data={
                "user": {
                    "id": int(user_id),
                    "tier": jwt_claims["tier"],
                    "account_type": jwt_claims["account_type"],
                    "company_id": jwt_claims.get("company_id"),
                    "password_reset_required": False,
                }
            },
        )

    def check_user(self, *, email):
        if email == "missing@example.com":
            return SimpleNamespace(status="not_found", data=None)
        return SimpleNamespace(
            status="ok",
            data={"id": 10, "email": email, "name": "Tester", "created_at": None},
        )

    def login_user(self, *, email, google_id=None):
        if email == "business@example.com":
            return SimpleNamespace(status="business_forbidden", data=None)
        return SimpleNamespace(
            status="ok",
            data={"id": 10, "email": email, "name": "Tester", "created_at": None},
        )

    def delete_account(self, *, user_id):
        return SimpleNamespace(
            status="ok",
            data={"deleted_projects": 1, "deleted_studies": 2, "deleted_artifacts": 3},
        )


def _make_auth_client(monkeypatch):
    import reopsai.api.auth as auth_module

    monkeypatch.setattr(auth_module, "auth_service", FakeAuthService())
    monkeypatch.setattr(auth_module.Config, "ALLOWED_ORIGINS", ["https://frontend.example.com"])
    monkeypatch.setattr(auth_module.Config, "FRONTEND_URL", "https://frontend.example.com")

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(auth_module.auth_bp)

    with app.app_context():
        token = create_access_token(
            identity="10",
            additional_claims={
                "tier": "enterprise",
                "account_type": "business",
                "company_id": 100,
                "password_reset_required": False,
            },
        )
    return app.test_client(), {"Authorization": f"Bearer {token}"}


def test_auth_blueprint_preserves_route_map():
    import reopsai.api.auth as auth_module

    app = Flask(__name__)
    app.register_blueprint(auth_module.auth_bp)

    expected_routes = {
        ("/api/login", "POST"),
        ("/api/profile", "GET"),
        ("/api/auth/logout", "POST"),
        ("/api/premium-feature", "GET"),
        ("/api/auth/test", "GET"),
        ("/api/auth/check-user", "POST"),
        ("/api/auth/register", "POST"),
        ("/api/auth/login", "POST"),
        ("/api/auth/users", "GET"),
        ("/api/auth/google/verify", "POST"),
        ("/api/auth/google/config", "GET"),
        ("/api/auth/enterprise/login", "POST"),
        ("/api/auth/business/login", "POST"),
        ("/api/auth/enterprise/change-password", "POST"),
        ("/api/auth/business/change-password", "POST"),
        ("/api/auth/enterprise/profile", "PUT"),
        ("/api/auth/business/profile", "PUT"),
        ("/api/auth/dev-login", "POST"),
        ("/api/auth/account", "DELETE"),
    }
    actual_routes = {
        (str(rule.rule), method)
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("auth.")
        for method in rule.methods
        if method not in {"HEAD", "OPTIONS"}
    }

    assert expected_routes <= actual_routes


def test_auth_profile_logout_check_and_login_response_shapes(monkeypatch):
    client, headers = _make_auth_client(monkeypatch)

    profile = client.get("/api/profile", headers=headers)
    assert profile.status_code == 200
    assert profile.get_json() == {
        "success": True,
        "user": {
            "id": 10,
            "tier": "enterprise",
            "account_type": "business",
            "company_id": 100,
            "password_reset_required": False,
        },
    }

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert logout.get_json() == {"success": True, "message": "로그아웃되었습니다."}

    check = client.post("/api/auth/check-user", json={"email": "tester@example.com"})
    assert check.status_code == 200
    assert check.get_json() == {
        "success": True,
        "exists": True,
        "user": {"id": 10, "email": "tester@example.com", "name": "Tester", "created_at": None},
    }

    missing = client.post("/api/auth/check-user", json={"email": "missing@example.com"})
    assert missing.status_code == 200
    assert missing.get_json() == {
        "success": True,
        "exists": False,
        "message": "사용자가 존재하지 않습니다.",
    }

    login = client.post("/api/auth/login", json={"email": "tester@example.com"})
    assert login.status_code == 200
    assert login.get_json() == {
        "success": True,
        "message": "로그인 성공!",
        "user": {"id": 10, "email": "tester@example.com", "name": "Tester", "created_at": None},
    }


def test_auth_account_delete_cors_and_shape(monkeypatch):
    client, headers = _make_auth_client(monkeypatch)

    options = client.open(
        "/api/auth/account",
        method="OPTIONS",
        headers={"Origin": "https://frontend.example.com"},
    )
    assert options.status_code == 200
    assert options.headers["Access-Control-Allow-Origin"] == "https://frontend.example.com"
    assert options.headers["Access-Control-Allow-Credentials"] == "true"

    deleted = client.delete(
        "/api/auth/account",
        headers={**headers, "Origin": "https://frontend.example.com"},
    )
    assert deleted.status_code == 200
    assert deleted.get_json() == {
        "success": True,
        "message": "계정이 성공적으로 삭제되었습니다.",
        "deleted_projects": 1,
        "deleted_studies": 2,
        "deleted_artifacts": 3,
    }
    assert deleted.headers["Access-Control-Allow-Origin"] == "https://frontend.example.com"
