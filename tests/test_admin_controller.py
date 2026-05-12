from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeAdminService:
    def list_enterprise_accounts(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "accounts": [{"id": 1, "email": "owner@example.com"}],
                "total_count": 1,
                "total_pages": 1,
                "current_page": kwargs["page"],
            },
        )

    def create_enterprise_account(self, **kwargs):
        return SimpleNamespace(status="ok", data={"id": 2, "email": kwargs["email"], "account_type": "business"})

    def list_admin_teams(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "teams": [{"id": 10, "team_name": "Research"}],
                "total_count": 1,
                "total_pages": 1,
                "current_page": kwargs["page"],
            },
        )

    def create_admin_team(self, **kwargs):
        return SimpleNamespace(status="ok", data={"id": 10, "team_name": kwargs["team_name"]})

    def list_admin_companies(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "companies": [{"id": 100, "name": "Acme"}],
                "total_count": 1,
                "page": kwargs["page"],
                "per_page": kwargs["per_page"],
                "total_pages": 1,
            },
        )

    def update_team_plan_code(self, **kwargs):
        return SimpleNamespace(status="ok", data=None)

    def create_enterprise_user(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "user": {"id": 20, "email": kwargs["email"]},
                "company": {"id": kwargs["company_id"], "name": "Acme", "role": kwargs["role"]},
                "temporary_password": "0000",
            },
        )


def _make_admin_client(monkeypatch):
    import routes.admin as admin_module

    monkeypatch.setattr(admin_module, "admin_service", FakeAdminService())

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


def test_admin_account_team_company_routes_preserve_response_shapes(monkeypatch):
    client, headers = _make_admin_client(monkeypatch)

    accounts = client.get("/api/admin/enterprise/accounts?page=1&per_page=10", headers=headers)
    assert accounts.status_code == 200
    assert accounts.get_json() == {
        "accounts": [{"id": 1, "email": "owner@example.com"}],
        "total_count": 1,
        "total_pages": 1,
        "current_page": 1,
    }

    account = client.post(
        "/api/admin/enterprise/accounts",
        headers=headers,
        json={"email": "new@example.com", "name": "New", "company_name": "Acme"},
    )
    assert account.status_code == 201
    assert account.get_json() == {
        "success": True,
        "account": {"id": 2, "email": "new@example.com", "account_type": "business"},
    }

    teams = client.get("/api/admin/teams", headers=headers)
    assert teams.status_code == 200
    assert teams.get_json()["teams"] == [{"id": 10, "team_name": "Research"}]

    team = client.post(
        "/api/admin/teams",
        headers=headers,
        json={"enterprise_account_id": 1, "team_name": "Research"},
    )
    assert team.status_code == 201
    assert team.get_json() == {"success": True, "team": {"id": 10, "team_name": "Research"}}

    companies = client.get("/api/admin/companies", headers=headers)
    assert companies.status_code == 200
    assert companies.get_json() == {
        "success": True,
        "companies": [{"id": 100, "name": "Acme"}],
        "total_count": 1,
        "page": 1,
        "per_page": 20,
        "total_pages": 1,
    }


def test_admin_controller_validation_and_enterprise_user_shape(monkeypatch):
    client, headers = _make_admin_client(monkeypatch)

    invalid_plan = client.get("/api/admin/teams?plan_code=bad", headers=headers)
    assert invalid_plan.status_code == 400
    assert invalid_plan.get_json() == {
        "success": False,
        "error": "유효하지 않은 plan_code입니다: ['enterprise_plus', 'pro', 'starter']",
    }

    invalid_status = client.get("/api/admin/teams?status=paused", headers=headers)
    assert invalid_status.status_code == 400
    assert invalid_status.get_json() == {
        "success": False,
        "error": "status는 active, deleted, all 중 하나여야 합니다.",
    }

    plan = client.put(
        "/api/admin/teams/10/plan",
        headers=headers,
        json={"plan_code": "pro"},
    )
    assert plan.status_code == 200
    assert plan.get_json() == {"success": True, "team_id": 10, "plan_code": "pro"}

    created = client.post(
        "/api/admin/enterprise/users",
        headers=headers,
        json={"email": "member@example.com", "company_id": 100, "role": "member"},
    )
    assert created.status_code == 201
    assert created.get_json() == {
        "success": True,
        "user": {"id": 20, "email": "member@example.com"},
        "company": {"id": 100, "name": "Acme", "role": "member"},
        "temporary_password": "0000",
    }
