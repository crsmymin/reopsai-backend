from contextlib import contextmanager
from types import SimpleNamespace

from reopsai.application.b2b_service import B2bService


@contextmanager
def fake_session_factory():
    yield object()


class FakeB2bRepository:
    @staticmethod
    def get_my_company_id(session, user_id_int, company_id_claim=None):
        if user_id_int == 404:
            return None
        return company_id_claim or 100

    @staticmethod
    def require_owner(session, company_id, user_id):
        if user_id == 403:
            return None
        return SimpleNamespace(role="owner")

    @staticmethod
    def get_active_company(session, company_id):
        if company_id == 404:
            return None
        return SimpleNamespace(id=company_id, name="Acme", status="active", created_at=None)

    @staticmethod
    def get_company_members(session, company_id):
        members = [
            SimpleNamespace(user_id=10, role="owner", joined_at=None),
            SimpleNamespace(user_id=11, role="member", joined_at=None),
        ]
        users = {
            10: SimpleNamespace(
                id=10,
                email="owner@example.com",
                name="Owner",
                department="Ops",
                tier="enterprise",
                account_type="business",
                password_reset_required=False,
            ),
            11: SimpleNamespace(
                id=11,
                email="member@example.com",
                name="Member",
                department=None,
                tier="enterprise",
                account_type="business",
                password_reset_required=True,
            ),
        }
        return members, users

    @staticmethod
    def get_membership(session, company_id, user_id):
        if user_id == 404:
            return None
        role = "owner" if user_id == 10 else "member"
        return SimpleNamespace(user_id=user_id, role=role, joined_at=None)

    @staticmethod
    def get_user_by_id(session, user_id):
        if user_id == 404:
            return None
        tier = "super" if user_id == 99 else "enterprise"
        account_type = "individual" if user_id == 88 else "business"
        return SimpleNamespace(
            id=user_id,
            email=f"user{user_id}@example.com",
            name="User",
            department=None,
            tier=tier,
            account_type=account_type,
            password_reset_required=False,
        )

    @staticmethod
    def add_or_update_member(session, *, company_id, email, role, department):
        if email == "missing@example.com":
            return None, "not_found"
        return (SimpleNamespace(id=12), SimpleNamespace(role=role)), "ok"

    @staticmethod
    def update_member_profile(
        session, *, member_user_id, name=None, department_value=None, update_department=False
    ):
        return SimpleNamespace(
            id=member_user_id,
            email=f"user{member_user_id}@example.com",
            name=name or "User",
            department=department_value if update_department else None,
            tier="enterprise",
            account_type="business",
            password_reset_required=False,
        )

    @staticmethod
    def reset_member_password(session, *, member_user_id):
        return SimpleNamespace(
            id=member_user_id,
            email=f"user{member_user_id}@example.com",
            name="User",
            department=None,
            tier="enterprise",
            account_type="business",
            password_reset_required=True,
        )

    @staticmethod
    def remove_membership(session, membership):
        return None

    @staticmethod
    def promote_member_to_owner(session, *, company_id, member_user_id):
        if member_user_id == 404:
            return None
        return SimpleNamespace(user_id=member_user_id, role="owner", joined_at=None)


def make_service():
    return B2bService(repository=FakeB2bRepository, session_factory=fake_session_factory)


def test_b2b_team_service_statuses():
    service = make_service()

    assert service.get_my_team(user_id=404, company_id_claim=None).status == "no_company"
    assert service.get_my_team(user_id=10, company_id_claim=404).status == "company_not_found"

    result = service.get_my_team(user_id=10, company_id_claim=100)
    assert result.status == "ok"
    assert result.data["company"]["name"] == "Acme"
    assert result.data["members"][0]["role"] == "owner"


def test_b2b_add_update_reset_remove_role_service_statuses():
    service = make_service()

    assert service.add_team_member(
        user_id=404,
        company_id_claim=None,
        email="member@example.com",
        role="member",
        department=None,
    ).status == "no_company"
    assert service.add_team_member(
        user_id=403,
        company_id_claim=100,
        email="member@example.com",
        role="member",
        department=None,
    ).status == "forbidden"
    assert service.add_team_member(
        user_id=10,
        company_id_claim=100,
        email="missing@example.com",
        role="member",
        department=None,
    ).status == "user_not_found"
    assert service.add_team_member(
        user_id=10,
        company_id_claim=100,
        email="member@example.com",
        role="member",
        department="Research",
    ).status == "ok"

    assert service.update_team_member(user_id=10, company_id_claim=100, member_user_id=10, data={"name": "Me"}).status == "self_update"
    assert service.update_team_member(user_id=10, company_id_claim=100, member_user_id=11, data={"bad": True}).status == "unknown_fields"
    assert service.update_team_member(user_id=10, company_id_claim=100, member_user_id=11, data={}).status == "empty_update"
    assert service.update_team_member(user_id=10, company_id_claim=100, member_user_id=404, data={"name": "Missing"}).status == "not_same_company"
    assert service.update_team_member(user_id=10, company_id_claim=100, member_user_id=88, data={"name": "Individual"}).status == "not_business"
    assert service.update_team_member(user_id=10, company_id_claim=100, member_user_id=11, data={"name": ""}).status == "empty_name"
    updated = service.update_team_member(user_id=10, company_id_claim=100, member_user_id=11, data={"name": "Updated"})
    assert updated.status == "ok"
    assert updated.data["name"] == "Updated"
    cleared_department = service.update_team_member(
        user_id=10,
        company_id_claim=100,
        member_user_id=11,
        data={"department": ""},
    )
    assert cleared_department.status == "ok"
    assert cleared_department.data["department"] is None

    assert service.reset_team_member_password(user_id=10, company_id_claim=100, member_user_id=10).status == "self_reset"
    assert service.reset_team_member_password(user_id=10, company_id_claim=100, member_user_id=99).status == "super_account"
    assert service.reset_team_member_password(user_id=10, company_id_claim=100, member_user_id=11).status == "ok"

    assert service.remove_team_member(user_id=10, company_id_claim=100, member_user_id=10).status == "self_remove"
    assert service.remove_team_member(user_id=10, company_id_claim=100, member_user_id=11).status == "ok"

    assert service.change_team_member_role(user_id=10, company_id_claim=100, member_user_id=11, new_role="member").status == "unsupported_role"
    assert service.change_team_member_role(user_id=10, company_id_claim=100, member_user_id=10, new_role="owner").status == "self_role_change"
    assert service.change_team_member_role(user_id=10, company_id_claim=100, member_user_id=404, new_role="owner").status == "not_same_company"
    assert service.change_team_member_role(user_id=10, company_id_claim=100, member_user_id=11, new_role="owner").status == "ok"
