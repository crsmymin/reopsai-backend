from contextlib import contextmanager
from types import SimpleNamespace

from reopsai_backend.application.admin_backoffice_service import AdminBackofficeService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace()


def make_user(user_id, email, *, tier="free", account_type="individual", company_id=None, name="User"):
    return SimpleNamespace(
        id=user_id,
        email=email,
        name=name,
        tier=tier,
        account_type=account_type,
        company_id=company_id,
        department=None,
        google_id=None,
        password_reset_required=False,
        created_at=None,
    )


def make_project(project_id=1, owner_id=20):
    return SimpleNamespace(
        id=project_id,
        owner_id=owner_id,
        name="Project",
        slug="project",
        product_url=None,
        keywords=[],
        created_at=None,
        updated_at=None,
    )


def make_study(study_id=2, project_id=1):
    return SimpleNamespace(
        id=study_id,
        project_id=project_id,
        name="Study",
        slug="study",
        initial_input=None,
        keywords=[],
        methodologies=[],
        participant_count=5,
        start_date=None,
        end_date=None,
        timeline=None,
        budget=None,
        target_audience=None,
        additional_requirements=None,
        created_at=None,
        updated_at=None,
    )


def make_feedback(feedback_id=1, user_id=20, comment=None):
    return SimpleNamespace(
        id=feedback_id,
        category="plan",
        vote="true",
        comment=comment,
        user_id=user_id,
        study_id=2,
        study_name="Study",
        created_at=None,
        updated_at=None,
    )


class FakeBackofficeRepository:
    users = {}
    companies = {}
    memberships = []
    deleted_user_ids = []
    feedback = {}

    @classmethod
    def reset(cls):
        cls.users = {
            10: make_user(10, "super@example.com", tier="super"),
            20: make_user(20, "member@example.com", company_id=100),
            30: make_user(30, "owner@example.com", tier="enterprise", account_type="business", company_id=100),
            40: make_user(40, "other-super@example.com", tier="super"),
            50: make_user(50, "empty@example.com"),
        }
        cls.companies = {100: SimpleNamespace(id=100, name="Acme", status="active")}
        cls.memberships = [
            SimpleNamespace(company_id=100, user_id=30, role="owner", joined_at=None),
            SimpleNamespace(company_id=100, user_id=20, role="member", joined_at=None),
        ]
        cls.deleted_user_ids = []
        cls.feedback = {1: make_feedback(1, user_id=20, comment="old")}

    @classmethod
    def get_user_by_id(cls, session, user_id):
        return cls.users.get(int(user_id))

    @classmethod
    def get_company(cls, session, company_id):
        return cls.companies.get(int(company_id)) if company_id else None

    @classmethod
    def get_company_name(cls, session, company_id, fallback=None):
        company = cls.get_company(session, company_id)
        return company.name if company else fallback

    @classmethod
    def owner_company_ids(cls, session, user_id):
        return [row.company_id for row in cls.memberships if row.user_id == int(user_id) and row.role == "owner"]

    @classmethod
    def is_company_member(cls, session, *, company_ids, user_id):
        return any(row.company_id in company_ids and row.user_id == int(user_id) for row in cls.memberships)

    @classmethod
    def count_owner_memberships(cls, session, user_id):
        return len([row for row in cls.memberships if row.user_id == int(user_id) and row.role == "owner"])

    @classmethod
    def count_company_memberships(cls, session, user_id):
        return len([row for row in cls.memberships if row.user_id == int(user_id)])

    @staticmethod
    def count_user_projects(session, user_id):
        return 1 if int(user_id) == 20 else 0

    @staticmethod
    def count_user_usage_events(session, user_id):
        return 2 if int(user_id) == 20 else 0

    @classmethod
    def delete_user(cls, session, user):
        cls.deleted_user_ids.append(user.id)
        cls.users.pop(user.id, None)

    @classmethod
    def list_non_admin_users(cls, session):
        return [cls.users[20], cls.users[30], cls.users[50]]

    @staticmethod
    def user_project_ids(session, user_id):
        return [1] if int(user_id) == 20 else []

    @staticmethod
    def study_ids_for_projects(session, project_ids):
        return [2] if project_ids else []

    @staticmethod
    def count_artifacts_by_type(session, study_ids, artifact_type):
        return {"plan": 1, "guideline": 2, "survey": 3}.get(artifact_type, 0) if study_ids else 0

    @classmethod
    def get_first_company_membership(cls, session, user_id):
        for row in cls.memberships:
            if row.user_id == int(user_id):
                return row
        return None

    @classmethod
    def get_owner_company_membership(cls, session, user_id):
        for row in cls.memberships:
            if row.user_id == int(user_id) and row.role == "owner":
                return row
        return None

    @classmethod
    def set_business_owner(cls, session, *, user, company_name, department):
        company = cls.companies[100]
        user.tier = "enterprise"
        user.account_type = "business"
        user.company_id = company.id
        user.password_reset_required = True
        if department is not None:
            user.department = department
        cls.memberships.append(SimpleNamespace(company_id=company.id, user_id=user.id, role="owner", joined_at=None))
        return company

    @classmethod
    def admin_stats(cls, session):
        return [(user.id, user.tier) for user in cls.users.values()], 3, 4

    @staticmethod
    def list_user_projects(session, user_id):
        return [make_project()] if int(user_id) == 20 else []

    @staticmethod
    def list_user_study_rows(session, user_id):
        return [(make_study(), "Project")] if int(user_id) == 20 else []

    @staticmethod
    def get_study(session, study_id):
        return make_study(study_id) if int(study_id) == 2 else None

    @staticmethod
    def list_study_artifacts(session, study_id):
        return [
            SimpleNamespace(
                id=9,
                study_id=study_id,
                owner_id=20,
                artifact_type="plan",
                content="content",
                status="completed",
                created_at=None,
                updated_at=None,
            )
        ]

    @classmethod
    def create_feedback(cls, session, **kwargs):
        feedback = make_feedback(2, user_id=kwargs["user_id"], comment=kwargs["comment"])
        feedback.category = kwargs["category"]
        feedback.vote = kwargs["vote"]
        feedback.study_id = kwargs["study_id"]
        feedback.study_name = kwargs["study_name"]
        cls.feedback[feedback.id] = feedback
        return feedback

    @classmethod
    def get_feedback_for_user(cls, session, *, feedback_id, user_id):
        feedback = cls.feedback.get(int(feedback_id))
        return feedback if feedback and feedback.user_id == int(user_id) else None

    @classmethod
    def list_feedback(cls, session, category=None):
        rows = list(cls.feedback.values())
        return [row for row in rows if not category or row.category == category]


def make_service():
    FakeBackofficeRepository.reset()
    return AdminBackofficeService(repository=FakeBackofficeRepository, session_factory=fake_session_factory)


def test_delete_user_permission_and_affected_payloads():
    service = make_service()

    assert service.delete_user(user_id=999, requester_id=10, requester_tier="super").status == "not_found"
    assert service.delete_user(user_id=20, requester_id=50, requester_tier="free").status == "forbidden"
    FakeBackofficeRepository.memberships.append(
        SimpleNamespace(company_id=100, user_id=40, role="member", joined_at=None)
    )
    assert service.delete_user(user_id=40, requester_id=30, requester_tier="enterprise").status == "target_super_forbidden"
    assert service.delete_user(user_id=30, requester_id=10, requester_tier="super").status == "ok"

    result = make_service().delete_user(user_id=20, requester_id=30, requester_tier="enterprise")
    assert result.status == "ok"
    assert result.data["deleted_user"]["email"] == "member@example.com"
    assert result.data["affected"] == {
        "company_memberships": 1,
        "owned_companies_released": 0,
        "owned_projects": 1,
        "usage_events_anonymized": 2,
    }


def test_user_tier_enterprise_stats_and_content_payloads():
    service = make_service()

    users = service.list_users()
    assert users.status == "ok"
    assert users.data["count"] == 3
    assert users.data["users"][0]["plan_count"] == 1

    tier = service.update_user_tier(user_id=20, tier="premium")
    assert tier.status == "ok"
    assert tier.data["tier"] == "premium"
    assert service.update_user_tier(user_id=999, tier="premium").status == "not_found"

    enterprise = service.get_user_enterprise_info(user_id=20)
    assert enterprise.status == "ok"
    assert enterprise.data["company"]["name"] == "Acme"

    existing = service.init_enterprise_team_for_user(user_id=30, company_name="", department=None)
    assert existing.status == "already_exists"
    created = service.init_enterprise_team_for_user(user_id=50, company_name="Acme", department="Research")
    assert created.status == "ok"
    assert created.data["user"]["account_type"] == "business"

    stats = service.get_admin_stats()
    assert stats.data["stats"]["total_projects"] == 3
    assert service.get_user_projects(user_id=20).data["count"] == 1
    assert service.get_user_studies(user_id=20).data["studies"][0]["projects"] == {"name": "Project"}
    assert service.get_study(study_id=2).status == "ok"
    assert service.get_study(study_id=404).status == "not_found"
    assert service.get_study_artifacts(study_id=2).data["artifacts"][0]["artifact_type"] == "plan"


def test_feedback_service_success_and_not_found():
    service = make_service()

    submitted = service.submit_feedback(
        category="plan",
        vote=True,
        comment="good",
        user_id=20,
        study_id=2,
        study_name="Study",
    )
    assert submitted.status == "ok"
    assert submitted.data["vote"] == "true"

    updated = service.update_feedback_comment(feedback_id=1, user_id=20, comment="new")
    assert updated.status == "ok"
    assert updated.data["comment"] == "new"
    assert service.update_feedback_comment(feedback_id=1, user_id=30, comment="bad").status == "not_found"

    listed = service.list_feedback(category="plan")
    assert listed.status == "ok"
    assert listed.data["category"] == "plan"
    assert listed.data["count"] >= 1
