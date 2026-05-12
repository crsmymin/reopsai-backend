from contextlib import contextmanager
from types import SimpleNamespace

from reopsai_backend.application.admin_service import AdminService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace(flush=lambda: None, refresh=lambda _obj: None)


def make_user(
    user_id,
    email,
    *,
    name="User",
    tier="free",
    account_type="individual",
    company_id=None,
    department=None,
    google_id=None,
):
    return SimpleNamespace(
        id=user_id,
        email=email,
        name=name,
        tier=tier,
        account_type=account_type,
        company_id=company_id,
        department=department,
        google_id=google_id,
        password_reset_required=False,
        created_at=None,
    )


def make_company(company_id=100, name="Acme", status="active"):
    return SimpleNamespace(id=company_id, name=name, status=status, created_at=None, updated_at=None)


def make_team(team_id=200, owner_id=1, status="active", plan_code="starter"):
    return SimpleNamespace(
        id=team_id,
        owner_id=owner_id,
        name="Research",
        description=None,
        status=status,
        plan_code=plan_code,
        created_at=None,
        updated_at=None,
    )


class FakeAdminRepository:
    users = {}
    companies = {}
    teams = {}
    owner_allowed = True

    @classmethod
    def reset(cls):
        cls.users = {
            1: make_user(1, "owner@example.com", name="Owner", tier="basic", company_id=100),
            2: make_user(2, "business@example.com", name="Biz", tier="enterprise", account_type="business", company_id=100),
            3: make_user(3, "google@example.com", name="Google", tier="free", google_id="gid"),
        }
        cls.companies = {100: make_company()}
        cls.teams = {200: make_team()}
        cls.owner_allowed = True

    @classmethod
    def get_user_by_id(cls, session, user_id):
        return cls.users.get(int(user_id))

    @classmethod
    def get_user_by_email_lower(cls, session, email):
        for user in cls.users.values():
            if user.email.lower() == email.lower():
                return user
        return None

    @classmethod
    def get_company(cls, session, company_id):
        return cls.companies.get(int(company_id)) if company_id else None

    @classmethod
    def get_active_company(cls, session, company_id):
        company = cls.get_company(session, company_id)
        return company if company and company.status != "deleted" else None

    @classmethod
    def get_or_create_company(cls, session, name):
        for company in cls.companies.values():
            if company.name.lower() == name.lower():
                return company
        company = make_company(max(cls.companies) + 1, name)
        cls.companies[company.id] = company
        return company

    @classmethod
    def set_company_for_user(cls, session, user, name):
        company = cls.get_or_create_company(session, name)
        user.company_id = company.id
        return company

    @staticmethod
    def get_first_company_membership(session, user_id):
        if int(user_id) == 2:
            return SimpleNamespace(company_id=100, role="owner", joined_at=None)
        if int(user_id) == 3:
            return SimpleNamespace(company_id=100, role="member", joined_at=None)
        return None

    @staticmethod
    def get_company_membership(session, company_id, user_id):
        return SimpleNamespace(company_id=company_id, user_id=user_id, role="member", joined_at=None)

    @staticmethod
    def ensure_company_membership(session, *, company_id, user_id, role):
        return SimpleNamespace(company_id=company_id, user_id=user_id, role=role, joined_at=None)

    @classmethod
    def list_account_users(cls, session, *, search="", account_type=""):
        users = list(cls.users.values())
        if account_type == "business":
            users = [user for user in users if user.account_type == "business"]
        return users

    @classmethod
    def create_enterprise_account(cls, session, *, email, name, company_name, department):
        if cls.get_user_by_email_lower(session, email):
            return None, "duplicate"
        company = cls.get_or_create_company(session, company_name)
        user = make_user(
            10,
            email,
            name=name,
            tier="enterprise",
            account_type="business",
            company_id=company.id,
            department=department,
        )
        user.password_reset_required = True
        cls.users[user.id] = user
        return (user, company), "ok"

    @staticmethod
    def reset_enterprise_password(session, user):
        user.password_reset_required = True

    @classmethod
    def list_teams(cls, session, *, search="", plan_code=None, enterprise_account_id=None, status="active"):
        teams = list(cls.teams.values())
        if status != "all":
            teams = [team for team in teams if team.status == status]
        if plan_code:
            teams = [team for team in teams if team.plan_code == plan_code]
        return teams

    @classmethod
    def get_team(cls, session, team_id):
        return cls.teams.get(int(team_id))

    @classmethod
    def get_active_team(cls, session, team_id):
        team = cls.get_team(session, team_id)
        return team if team and team.status != "deleted" else None

    @staticmethod
    def get_inherited_team_plan(session, owner_id):
        return None

    @classmethod
    def create_team(cls, session, *, owner_id, team_name, description, plan_code):
        team = make_team(201, owner_id=owner_id, plan_code=plan_code)
        team.name = team_name
        team.description = description
        cls.teams[team.id] = team
        return team

    @staticmethod
    def count_team_members(session, team_id):
        return 1

    @staticmethod
    def team_owner_member_exists(session, team_id, owner_id):
        return True

    @staticmethod
    def count_team_usage_events(session, team_id):
        return 2

    @staticmethod
    def soft_delete_team(session, team):
        was_deleted = team.status == "deleted"
        team.status = "deleted"
        return was_deleted

    @classmethod
    def list_companies(cls, session, *, search="", status="", offset=0, limit=20):
        companies = list(cls.companies.values())
        return companies[offset : offset + limit], len(companies)

    @staticmethod
    def company_member_counts(session, company_ids):
        return ({100: 2}, {100: 1})

    @staticmethod
    def company_usage_summary(session, company_id):
        return {"request_count": 0, "total_tokens": 0, "billable_weighted_tokens": 0, "estimated_cost_usd": 0.0, "usage_limit": None, "remaining_weighted_tokens": 0}

    @classmethod
    def get_company_members(cls, session, company_id):
        memberships = [SimpleNamespace(user_id=2, role="owner", joined_at=None)]
        return memberships, {2: cls.users[2]}

    @staticmethod
    def company_member_legacy_usage(session, company_id, user_ids):
        return {}

    @staticmethod
    def company_member_llm_usage(session, company_id, user_ids):
        return {}

    @classmethod
    def is_company_owner(cls, session, *, company_id, user_id):
        return cls.owner_allowed

    @classmethod
    def upsert_enterprise_user(cls, session, *, company, email, name, department, role):
        user = cls.get_user_by_email_lower(session, email) or make_user(20, email)
        user.name = name
        user.company_id = company.id
        user.department = department
        user.tier = "enterprise"
        user.account_type = "business"
        user.password_reset_required = True
        cls.users[user.id] = user
        return user


def make_service():
    FakeAdminRepository.reset()
    return AdminService(repository=FakeAdminRepository, session_factory=fake_session_factory)


def test_admin_account_service_statuses():
    service = make_service()

    listed = service.list_enterprise_accounts(page=1, per_page=2)
    assert listed.status == "ok"
    assert listed.data["total_count"] == 3
    assert len(listed.data["accounts"]) == 2

    assert service.create_enterprise_account(
        email="owner@example.com",
        name="Owner",
        company_name="Acme",
        department=None,
    ).status == "duplicate"
    created = service.create_enterprise_account(
        email="new@example.com",
        name="New",
        company_name="Acme",
        department="Research",
    )
    assert created.status == "ok"
    assert created.data["account_type"] == "business"

    assert service.update_enterprise_account(account_id=2, data={"plan_code": "basic"}).status == "business_plan_forbidden"
    updated = service.update_enterprise_account(account_id=1, data={"plan_code": "pro", "company_name": "NewCo"})
    assert updated.status == "ok"
    assert updated.data["plan_code"] == "basic"


def test_admin_team_company_and_enterprise_user_service_statuses():
    service = make_service()

    assert service.create_admin_team(
        enterprise_account_id=404,
        team_name="Team",
        description="",
        requested_plan=None,
    ).status == "owner_not_found"
    assert service.create_admin_team(
        enterprise_account_id=2,
        team_name="Team",
        description="",
        requested_plan=None,
    ).status == "business_owner_forbidden"
    created_team = service.create_admin_team(
        enterprise_account_id=1,
        team_name="Team",
        description="Desc",
        requested_plan="pro",
    )
    assert created_team.status == "ok"
    assert created_team.data["plan_code"] == "pro"

    assert service.update_team_plan_code(team_id=404, plan_code="starter").status == "not_found"
    assert service.update_team_plan_code(team_id=200, plan_code="enterprise_plus").status == "ok"
    assert service.soft_delete_admin_team(team_id=200).data["affected"]["usage_events_preserved"] == 2

    companies = service.list_admin_companies(page=1, per_page=20)
    assert companies.status == "ok"
    assert companies.data["companies"][0]["name"] == "Acme"
    detail = service.get_admin_company_detail(company_id=100)
    assert detail.status == "ok"
    assert detail.data["company"]["member_count"] == 1
    assert service.update_admin_company(company_id=404, status="inactive").status == "not_found"
    assert service.update_admin_company(company_id=100, status="inactive").data["status"] == "inactive"

    FakeAdminRepository.owner_allowed = False
    assert service.create_enterprise_user(
        email="member@example.com",
        name="Member",
        company_id=100,
        department=None,
        role="member",
        requester_id=99,
        requester_tier="basic",
    ).status == "forbidden"
    created_user = service.create_enterprise_user(
        email="member@example.com",
        name="Member",
        company_id=100,
        department="Ops",
        role="member",
        requester_id=99,
        requester_tier="super",
    )
    assert created_user.status == "ok"
    assert created_user.data["temporary_password"] == "0000"
