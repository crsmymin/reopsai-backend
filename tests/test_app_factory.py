from reopsai.api import app_factory


class TestConfig:
    TESTING = True
    JWT_SECRET_KEY = "test-secret-key-with-at-least-32-bytes"
    JWT_TOKEN_LOCATION = ["headers", "cookies"]
    JWT_ACCESS_COOKIE_NAME = "access_token_cookie"
    JWT_ACCESS_COOKIE_PATH = "/"
    JWT_COOKIE_SECURE = False
    JWT_COOKIE_HTTPONLY = True
    JWT_COOKIE_SAMESITE = "Lax"
    JWT_COOKIE_CSRF_PROTECT = False
    ALLOWED_ORIGINS = ["http://localhost:3000"]
    DATABASE_URL = ""


def test_create_app_registers_health_and_security_headers(monkeypatch):
    monkeypatch.setattr(app_factory, "_init_database", lambda config_object: False)
    monkeypatch.setattr(app_factory, "register_blueprints", lambda app: None)

    app = app_factory.create_app(TestConfig)

    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert app.config["SQLA_ENABLED"] is False
