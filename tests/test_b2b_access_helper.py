from importlib import import_module

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token, verify_jwt_in_request

from reopsai.shared import b2b_access


def _make_app():
    app = Flask(__name__)
    app.config.update(
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    return app


def test_owner_ids_for_non_business_request():
    app = _make_app()
    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"account_type": "personal"})

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        assert b2b_access.get_owner_ids_for_request("10") == (["10"], None)


def test_business_request_falls_back_when_session_scope_missing(monkeypatch):
    app = _make_app()
    with app.app_context():
        token = create_access_token(
            identity="10",
            additional_claims={"account_type": "business", "company_id": 20},
        )

    monkeypatch.setattr(b2b_access, "session_scope", None)

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        assert b2b_access.get_owner_ids_for_request("10") == (["10"], None)


def test_root_b2b_access_wrapper_reexports_absorbed_helpers():
    legacy_b2b_access = import_module("utils.b2b_access")

    assert legacy_b2b_access.get_owner_ids_for_request is b2b_access.get_owner_ids_for_request
    assert legacy_b2b_access._to_int_or_raw is b2b_access._to_int_or_raw
