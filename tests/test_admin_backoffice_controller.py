from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeAdminBackofficeService:
    def delete_user(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "deleted_user": {"id": kwargs["user_id"], "email": "member@example.com"},
                "affected": {
                    "company_memberships": 1,
                    "owned_companies_released": 0,
                    "owned_projects": 2,
                    "usage_events_anonymized": 3,
                },
            },
        )

    def list_users(self):
        return SimpleNamespace(status="ok", data={"users": [{"id": 20, "email": "member@example.com"}], "count": 1})

    def update_user_tier(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={"id": kwargs["user_id"], "email": "member@example.com", "tier": kwargs["tier"], "created_at": None, "google_id": None},
        )

    def get_user_enterprise_info(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "user": {"id": kwargs["user_id"], "email": "member@example.com"},
                "tier": "enterprise",
                "company": {"id": 100, "name": "Acme", "status": "active", "role": "member", "joined_at": None},
            },
        )

    def init_enterprise_team_for_user(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "user": {"id": kwargs["user_id"], "email": "member@example.com", "account_type": "business"},
                "company": {"id": 100, "name": kwargs["company_name"] or "Acme"},
            },
        )

    def get_admin_stats(self):
        return SimpleNamespace(
            status="ok",
            data={"stats": {"total_users": 2, "tier_counts": {"free": 1, "super": 1}, "total_projects": 3, "total_studies": 4}},
        )

    def get_user_projects(self, **kwargs):
        return SimpleNamespace(status="ok", data={"projects": [{"id": 1, "owner_id": kwargs["user_id"], "name": "Project"}], "count": 1})

    def get_user_studies(self, **kwargs):
        return SimpleNamespace(status="ok", data={"studies": [{"id": 2, "project_id": 1, "name": "Study", "projects": {"name": "Project"}}], "count": 1})

    def get_study(self, **kwargs):
        return SimpleNamespace(status="ok", data={"id": kwargs["study_id"], "project_id": 1, "name": "Study"})

    def get_study_artifacts(self, **kwargs):
        return SimpleNamespace(status="ok", data={"artifacts": [{"id": 9, "study_id": kwargs["study_id"], "artifact_type": "plan"}]})

    def submit_feedback(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "id": 1,
                "category": kwargs["category"],
                "vote": "true",
                "comment": kwargs["comment"],
                "user_id": kwargs["user_id"],
                "study_id": kwargs["study_id"],
                "study_name": kwargs["study_name"],
                "created_at": None,
                "updated_at": None,
            },
        )

    def update_feedback_comment(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={"id": kwargs["feedback_id"], "category": "plan", "vote": "true", "comment": kwargs["comment"], "user_id": kwargs["user_id"]},
        )

    def list_feedback(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={"feedback": [{"id": 1, "category": kwargs["category"] or "plan"}], "count": 1, "category": kwargs["category"] or "all"},
        )


def _make_admin_backoffice_client(monkeypatch, claims=None):
    import routes.admin as admin_module

    monkeypatch.setattr(admin_module, "admin_backoffice_service", FakeAdminBackofficeService())

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
            additional_claims=claims or {"tier": "super", "password_reset_required": False},
        )
    return app.test_client(), {"Authorization": f"Bearer {token}"}


def test_admin_backoffice_controller_response_shapes(monkeypatch):
    client, headers = _make_admin_backoffice_client(monkeypatch)

    users = client.get("/api/admin/users", headers=headers)
    assert users.status_code == 200
    assert users.get_json() == {"success": True, "users": [{"id": 20, "email": "member@example.com"}], "count": 1}

    deleted = client.delete("/api/admin/users/20", headers=headers)
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted_user"] == {"id": 20, "email": "member@example.com"}
    assert deleted.get_json()["affected"]["owned_projects"] == 2

    tier = client.put("/api/admin/users/20/tier", headers=headers, json={"tier": "premium"})
    assert tier.status_code == 200
    assert tier.get_json()["message"] == "사용자 tier가 premium로 변경되었습니다."

    enterprise = client.get("/api/admin/users/20/enterprise", headers=headers)
    assert enterprise.status_code == 200
    assert enterprise.get_json()["company"]["name"] == "Acme"

    init = client.post("/api/admin/users/20/enterprise/init-team", headers=headers, json={"company_name": "Acme"})
    assert init.status_code == 200
    assert init.get_json()["message"] == "business 회사가 설정되고 사용자가 owner로 등록되었습니다."

    stats = client.get("/api/admin/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.get_json()["stats"]["total_projects"] == 3


def test_admin_content_and_feedback_controller_shapes(monkeypatch):
    client, headers = _make_admin_backoffice_client(monkeypatch)

    projects = client.get("/api/admin/users/20/projects", headers=headers)
    assert projects.status_code == 200
    assert projects.get_json() == {"success": True, "projects": [{"id": 1, "owner_id": 20, "name": "Project"}], "count": 1}

    studies = client.get("/api/admin/users/20/studies", headers=headers)
    assert studies.status_code == 200
    assert studies.get_json()["studies"][0]["projects"] == {"name": "Project"}

    study = client.get("/api/admin/studies/2", headers=headers)
    assert study.status_code == 200
    assert study.get_json() == {"id": 2, "project_id": 1, "name": "Study"}

    artifacts = client.get("/api/admin/studies/2/artifacts", headers=headers)
    assert artifacts.status_code == 200
    assert artifacts.get_json() == {"success": True, "artifacts": [{"id": 9, "study_id": 2, "artifact_type": "plan"}]}

    submitted = client.post(
        "/api/feedback",
        headers=headers,
        json={"category": "plan", "vote": True, "comment": "good", "study_id": 2, "study_name": "Study"},
    )
    assert submitted.status_code == 200
    assert submitted.get_json()["message"] == "피드백이 저장되었습니다."

    updated = client.patch("/api/feedback/1", headers=headers, json={"comment": "new"})
    assert updated.status_code == 200
    assert updated.get_json()["feedback"]["comment"] == "new"

    listed = client.get("/api/admin/feedback?category=plan", headers=headers)
    assert listed.status_code == 200
    assert listed.get_json() == {"success": True, "feedback": [{"id": 1, "category": "plan"}], "count": 1, "category": "plan"}


def test_admin_backoffice_controller_validation_errors(monkeypatch):
    client, headers = _make_admin_backoffice_client(monkeypatch)

    invalid_tier = client.put("/api/admin/users/20/tier", headers=headers, json={"tier": "starter"})
    assert invalid_tier.status_code == 400
    assert invalid_tier.get_json() == {
        "success": False,
        "error": "유효하지 않은 tier입니다. 가능한 값: ['free', 'basic', 'premium', 'enterprise', 'super']",
    }

    invalid_user = client.get("/api/admin/users/not-an-int/projects", headers=headers)
    assert invalid_user.status_code == 400
    assert invalid_user.get_json() == {"success": False, "error": "유효하지 않은 사용자 ID입니다."}

    invalid_feedback = client.post("/api/feedback", headers=headers, json={"category": "bad", "vote": True})
    assert invalid_feedback.status_code == 400
    assert invalid_feedback.get_json() == {
        "success": False,
        "error": "유효하지 않은 category입니다. 가능한 값: ['plan', 'screener', 'guide', 'participants']",
    }

    invalid_feedback_list = client.get("/api/admin/feedback?category=bad", headers=headers)
    assert invalid_feedback_list.status_code == 400
    assert invalid_feedback_list.get_json() == {
        "success": False,
        "error": "유효하지 않은 category입니다. 가능한 값: ['plan', 'screener', 'guide', 'participants']",
    }
