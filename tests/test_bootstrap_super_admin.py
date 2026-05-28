from types import SimpleNamespace

from scripts.bootstrap_super_admin import ensure_super_admin, parse_super_admin_emails


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, existing_user=None):
        self.existing_user = existing_user
        self.added = None
        self.flushed = False
        self.refreshed = None

    def execute(self, _statement):
        return _ScalarResult(self.existing_user)

    def add(self, user):
        self.added = user

    def flush(self):
        self.flushed = True

    def refresh(self, user):
        self.refreshed = user


def test_parse_super_admin_emails_normalizes_and_deduplicates():
    emails = parse_super_admin_emails(" Owner@Example.com, owner@example.com, admin@example.com ,, ")

    assert emails == ["owner@example.com", "admin@example.com"]


def test_parse_super_admin_emails_handles_empty_values():
    assert parse_super_admin_emails(None) == []
    assert parse_super_admin_emails("") == []
    assert parse_super_admin_emails(" , ") == []


def test_ensure_super_admin_creates_missing_user_as_super():
    session = _FakeSession()

    user, created = ensure_super_admin(session, email=" Owner@Example.com ")

    assert created is True
    assert user is session.added
    assert user.email == "owner@example.com"
    assert user.tier == "super"
    assert user.account_type == "individual"
    assert user.password_reset_required is False
    assert session.flushed is True
    assert session.refreshed is user


def test_ensure_super_admin_promotes_existing_user():
    existing_user = SimpleNamespace(
        email="owner@example.com",
        name=None,
        tier="free",
        account_type="business",
        password_reset_required=True,
    )
    session = _FakeSession(existing_user=existing_user)

    user, created = ensure_super_admin(session, email="owner@example.com")

    assert created is False
    assert user is existing_user
    assert user.tier == "super"
    assert user.account_type == "individual"
    assert user.password_reset_required is False
    assert user.name == "Super Admin"
    assert session.added is None
