from contextlib import contextmanager
from types import SimpleNamespace

from werkzeug.security import generate_password_hash

from reopsai.application.auth_service import AuthService, build_auth_context, build_user_payload


@contextmanager
def fake_session_factory():
    yield object()


def user(
    user_id=1,
    email="user@example.com",
    name="User",
    *,
    google_id=None,
    tier="free",
    account_type="individual",
    company_id=None,
    department=None,
    password_hash=None,
    password_reset_required=False,
):
    return SimpleNamespace(
        id=user_id,
        email=email,
        name=name,
        google_id=google_id,
        tier=tier,
        account_type=account_type,
        company_id=company_id,
        department=department,
        password_hash=password_hash,
        password_reset_required=password_reset_required,
        created_at=None,
    )


class FakeAuthRepository:
    users = {
        "user@example.com": user(),
        "business@example.com": user(
            2,
            "business@example.com",
            "Business",
            tier="enterprise",
            account_type="business",
            company_id=10,
            department="Research",
            password_hash=generate_password_hash("old-password"),
            password_reset_required=True,
        ),
    }

    @classmethod
    def get_user_by_email(cls, session, email):
        return cls.users.get(email)

    @classmethod
    def get_user_by_email_lower(cls, session, email):
        return cls.users.get(email.lower())

    @classmethod
    def get_user_for_google_login(cls, session, *, email, google_id=None):
        found = cls.users.get(email)
        if found and google_id and found.google_id != google_id:
            return None
        return found

    @classmethod
    def get_user_by_id(cls, session, user_id):
        for row in cls.users.values():
            if int(row.id) == int(user_id):
                return row
        return None

    @staticmethod
    def get_company_name(session, company_id):
        return "Acme" if company_id == 10 else None

    @classmethod
    def create_user(cls, session, *, email, name, google_id=None):
        created = user(3, email, name, google_id=google_id)
        cls.users[email] = created
        return created

    @classmethod
    def set_google_id(cls, session, *, user_id, google_id):
        found = cls.get_user_by_id(session, user_id)
        found.google_id = google_id
        return found

    @classmethod
    def list_users(cls, session):
        return list(cls.users.values())

    @classmethod
    def update_business_password(cls, session, *, user_id, new_password):
        found = cls.get_user_by_id(session, user_id)
        found.password_hash = generate_password_hash(new_password)
        found.password_reset_required = False
        return found

    @classmethod
    def update_business_profile(cls, session, *, user_id, name=None, department=None, update_department=False):
        found = cls.get_user_by_id(session, user_id)
        if name is not None:
            found.name = name
        if update_department:
            found.department = department
        return found

    @staticmethod
    def get_database_test_payload(session):
        return 2, FakeAuthRepository.users["user@example.com"]

    @staticmethod
    def get_or_create_dev_user(session, *, email, name):
        return user(9, email, name), True

    @staticmethod
    def delete_account_payload(session, *, user_id):
        return {"deleted_projects": 2, "deleted_studies": 3, "deleted_artifacts": 4}


def make_service():
    FakeAuthRepository.users = {
        "user@example.com": user(),
        "business@example.com": user(
            2,
            "business@example.com",
            "Business",
            tier="enterprise",
            account_type="business",
            company_id=10,
            department="Research",
            password_hash=generate_password_hash("old-password"),
            password_reset_required=True,
        ),
    }
    return AuthService(repository=FakeAuthRepository, session_factory=fake_session_factory)


def test_auth_payload_builders_preserve_claim_and_user_shape():
    business_user = FakeAuthRepository.users["business@example.com"]

    claims = build_auth_context(business_user)
    payload = build_user_payload(business_user, company_id=10, company_name="Acme")

    assert claims == {
        "tier": "enterprise",
        "account_type": "business",
        "password_reset_required": True,
        "company_id": 10,
        "department": "Research",
    }
    assert payload["company_name"] == "Acme"
    assert payload["password_reset_required"] is True


def test_auth_service_user_lookup_and_register_statuses():
    service = make_service()

    assert service.check_user(email="missing@example.com").status == "not_found"
    existing = service.check_user(email="user@example.com")
    assert existing.status == "ok"
    assert existing.data["email"] == "user@example.com"

    assert service.register_user(email="user@example.com", name="Duplicate").status == "duplicate"
    created = service.register_user(email="new@example.com", name="New")
    assert created.status == "ok"
    assert created.data["email"] == "new@example.com"


def test_auth_service_login_statuses():
    service = make_service()

    assert service.login_user(email="missing@example.com").status == "not_found"
    assert service.login_user(email="business@example.com").status == "business_forbidden"
    login = service.login_user(email="user@example.com")
    assert login.status == "ok"
    assert login.data["name"] == "User"

    assert service.enterprise_login(email="missing@example.com", password="old-password").status == "not_found"
    assert service.enterprise_login(email="user@example.com", password="old-password").status == "individual_forbidden"
    assert service.enterprise_login(email="business@example.com", password="bad").status == "invalid_password"
    business_login = service.enterprise_login(email="business@example.com", password="old-password")
    assert business_login.status == "ok"
    assert business_login.data["user"]["company_name"] == "Acme"


def test_auth_service_business_password_profile_and_delete():
    service = make_service()

    assert service.change_business_password(
        user_id=2,
        current_password="bad",
        new_password="new-password",
    ).status == "invalid_current_password"
    changed = service.change_business_password(
        user_id=2,
        current_password="old-password",
        new_password="new-password",
    )
    assert changed.status == "ok"
    assert changed.data["user"]["password_reset_required"] is False

    assert service.update_business_profile(user_id=2, data={"bad": True}).status == "unknown_fields"
    assert service.update_business_profile(user_id=2, data={"name": ""}).status == "empty_name"
    updated = service.update_business_profile(user_id=2, data={"name": "Updated", "department": ""})
    assert updated.status == "ok"
    assert updated.data["user"]["name"] == "Updated"
    assert updated.data["user"]["department"] is None

    deleted = service.delete_account(user_id=2)
    assert deleted.status == "ok"
    assert deleted.data == {"deleted_projects": 2, "deleted_studies": 3, "deleted_artifacts": 4}


def test_auth_service_db_unavailable_status():
    service = AuthService(repository=FakeAuthRepository, session_factory=None)

    assert service.check_user(email="user@example.com").status == "db_unavailable"
    assert service.test_connection().status == "db_unavailable"
