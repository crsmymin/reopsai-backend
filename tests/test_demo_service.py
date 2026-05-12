from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

from reopsai_backend.application.demo_service import DemoService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace(flush=lambda: None)


class FakeDemoRepository:
    INDIVIDUAL_DEMO_EMAIL = "test@example.com"
    ENTERPRISE_DEMO_EMAIL = "demo-enterprise@test.com"
    users = {}
    company = None
    membership = None

    @classmethod
    def reset(cls):
        cls.users = {}
        cls.company = None
        cls.membership = None

    @classmethod
    def get_user_by_email_lower(cls, session, email):
        return cls.users.get(email.lower())

    @classmethod
    def create_demo_user(cls, session, *, email, tier, account_type):
        user = SimpleNamespace(
            id=len(cls.users) + 1,
            email=email,
            google_id=f"dev_{email}",
            tier=tier,
            account_type=account_type,
            password_reset_required=False,
            company_id=None,
            department=None,
            name=None,
            created_at=datetime(2026, 5, 1, 12, 0, 0),
        )
        cls.users[email.lower()] = user
        return user

    @classmethod
    def get_company_by_name_lower(cls, session, name):
        return cls.company

    @classmethod
    def create_company(cls, session, *, name, status):
        cls.company = SimpleNamespace(id=100, name=name, status=status)
        return cls.company

    @classmethod
    def get_company_member(cls, session, *, company_id, user_id):
        return cls.membership

    @classmethod
    def create_company_member(cls, session, *, company_id, user_id, role):
        cls.membership = SimpleNamespace(company_id=company_id, user_id=user_id, role=role)
        return cls.membership


def make_service():
    FakeDemoRepository.reset()
    return DemoService(
        repository=FakeDemoRepository,
        session_factory=fake_session_factory,
        initial_grant_ensurer=lambda session, company_id: None,
    )


def test_demo_service_individual_login_payload():
    service = make_service()

    result = service.login(tier_type="individual")

    assert result.status == "ok"
    assert result.data["user_id"] == 1
    assert result.data["claims"]["tier"] == "free"
    assert result.data["claims"]["account_type"] == "individual"
    assert result.data["user"]["email"] == "test@example.com"


def test_demo_service_enterprise_login_creates_company_membership():
    service = make_service()

    result = service.login(tier_type="enterprise")

    assert result.status == "ok"
    assert result.data["claims"]["tier"] == "enterprise"
    assert result.data["claims"]["account_type"] == "business"
    assert result.data["claims"]["company_id"] == 100
    assert result.data["user"]["company_id"] == 100
    assert FakeDemoRepository.membership.role == "owner"


def test_demo_service_existing_enterprise_user_is_normalized():
    service = make_service()
    existing = FakeDemoRepository.create_demo_user(
        None,
        email=FakeDemoRepository.ENTERPRISE_DEMO_EMAIL,
        tier="free",
        account_type="individual",
    )

    result = service.login(tier_type="enterprise")

    assert result.status == "ok"
    assert existing.tier == "enterprise"
    assert existing.account_type == "business"
