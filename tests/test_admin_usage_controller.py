from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeAdminUsageService:
    def get_user_llm_usage(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "user": {"id": kwargs["user_id"], "email": "user@example.com", "company_id": 100},
                "period": kwargs["period"],
                "window": {"start_date": None, "end_date": None},
                "totals": {"request_count": 1},
                "by_period": [],
                "by_user": [],
                "by_team": [],
                "by_model": [],
            },
        )

    def create_company_token_topup(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "company": {"id": kwargs["company_id"], "name": "Acme", "status": "active"},
                "ledger": {
                    "id": 1,
                    "company_id": kwargs["company_id"],
                    "delta_weighted_tokens": kwargs["weighted_tokens"],
                    "reason": "top_up",
                    "created_by": kwargs["created_by"],
                    "note": kwargs["note"],
                    "created_at": None,
                },
                "remaining_weighted_tokens": 900,
            },
        )

    def list_model_prices(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "prices": [
                    {
                        "id": 1,
                        "provider": kwargs["provider"] or "openai",
                        "model": "gpt-test",
                        "effective_from": None,
                        "effective_to": None,
                        "currency": "USD",
                        "input_per_1m": 1.0,
                        "cached_input_per_1m": 0.1,
                        "output_per_1m": 2.0,
                        "reasoning_policy": None,
                        "source_url": None,
                    }
                ]
            },
        )

    def delete_expired_llm_usage_events(self, **kwargs):
        return SimpleNamespace(status="ok", data={"retention_days": kwargs["retention_days"], "deleted_count": 7})


def _make_admin_usage_client(monkeypatch):
    import reopsai_backend.api.admin as admin_module

    monkeypatch.setattr(admin_module, "admin_usage_service", FakeAdminUsageService())

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(admin_module.admin_bp)

    with app.app_context():
        token = create_access_token(
            identity="10",
            additional_claims={"tier": "super", "password_reset_required": False},
        )
    return app.test_client(), {"Authorization": f"Bearer {token}"}


def test_admin_usage_controller_response_shapes(monkeypatch):
    client, headers = _make_admin_usage_client(monkeypatch)

    usage = client.get("/api/admin/users/10/llm-usage", headers=headers)
    assert usage.status_code == 200
    assert usage.get_json() == {
        "success": True,
        "user": {"id": 10, "email": "user@example.com", "company_id": 100},
        "period": "daily",
        "window": {"start_date": None, "end_date": None},
        "totals": {"request_count": 1},
        "by_period": [],
        "by_user": [],
        "by_team": [],
        "by_model": [],
    }

    topup = client.post(
        "/api/admin/companies/100/token-topups",
        headers=headers,
        json={"weighted_tokens": 50, "note": "manual"},
    )
    assert topup.status_code == 201
    assert topup.get_json()["ledger"]["delta_weighted_tokens"] == 50
    assert topup.get_json()["remaining_weighted_tokens"] == 900

    prices = client.get("/api/admin/llm-model-prices?provider=openai", headers=headers)
    assert prices.status_code == 200
    assert prices.get_json()["prices"][0]["model"] == "gpt-test"

    cleanup = client.delete("/api/admin/llm-usage-events/expired?retention_days=30", headers=headers)
    assert cleanup.status_code == 200
    assert cleanup.get_json() == {"success": True, "retention_days": 30, "deleted_count": 7}


def test_admin_usage_controller_validation_errors(monkeypatch):
    client, headers = _make_admin_usage_client(monkeypatch)

    invalid_period = client.get("/api/admin/users/10/llm-usage?period=weekly", headers=headers)
    assert invalid_period.status_code == 400
    assert invalid_period.get_json() == {"success": False, "error": "period는 daily 또는 monthly여야 합니다."}

    invalid_date = client.get("/api/admin/users/10/llm-usage?start_date=bad", headers=headers)
    assert invalid_date.status_code == 400
    assert invalid_date.get_json() == {"success": False, "error": "start_date는 YYYY-MM-DD 형식이어야 합니다."}

    invalid_topup = client.post(
        "/api/admin/companies/100/token-topups",
        headers=headers,
        json={"weighted_tokens": 0},
    )
    assert invalid_topup.status_code == 400
    assert invalid_topup.get_json() == {"success": False, "error": "weighted_tokens는 1 이상의 정수여야 합니다."}

    invalid_retention = client.delete("/api/admin/llm-usage-events/expired?retention_days=-1", headers=headers)
    assert invalid_retention.status_code == 400
    assert invalid_retention.get_json() == {"success": False, "error": "retention_days는 1 이상의 정수여야 합니다."}
