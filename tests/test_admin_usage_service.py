from contextlib import contextmanager
from types import SimpleNamespace

from reopsai.application.admin_usage_service import AdminUsageService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace(flush=lambda: None)


def make_usage_rows():
    totals = SimpleNamespace(
        request_count=3,
        prompt_tokens=10,
        completion_tokens=5,
        cached_input_tokens=1,
        reasoning_tokens=2,
        total_tokens=15,
        billable_weighted_tokens=20,
        estimated_cost_usd=0.12,
    )
    return {
        "totals": totals,
        "by_period_rows": [SimpleNamespace(period="2026-05-12", **totals.__dict__)],
        "by_user_rows": [
            SimpleNamespace(
                user_id=10,
                email="user@example.com",
                name="User",
                request_count=3,
                total_tokens=15,
                billable_weighted_tokens=20,
                estimated_cost_usd=0.12,
                last_used_at=None,
            )
        ],
        "by_team_rows": [
            SimpleNamespace(team_id=20, request_count=3, total_tokens=15, billable_weighted_tokens=20, estimated_cost_usd=0.12)
        ],
        "by_model_rows": [
            SimpleNamespace(
                provider="openai",
                model="gpt-test",
                request_count=3,
                prompt_tokens=10,
                completion_tokens=5,
                cached_input_tokens=1,
                reasoning_tokens=2,
                total_tokens=15,
                billable_weighted_tokens=20,
                estimated_cost_usd=0.12,
            )
        ],
    }


class FakeAdminUsageRepository:
    @staticmethod
    def get_user(session, user_id):
        if int(user_id) == 404:
            return None
        return SimpleNamespace(id=user_id, email="user@example.com", company_id=100)

    @staticmethod
    def get_company(session, company_id):
        if int(company_id) == 404:
            return None
        return SimpleNamespace(id=company_id, name="Acme", status="active")

    @staticmethod
    def get_team(session, team_id):
        if int(team_id) == 404:
            return None
        return SimpleNamespace(id=team_id, name="Research", status="active", plan_code="pro", owner_id=10)

    @staticmethod
    def get_legacy_team_usage(session, *, team_id, start_at=None, end_at=None):
        team = FakeAdminUsageRepository.get_team(session, team_id)
        if not team:
            return None
        return {
            "entity": team,
            "totals": (2, 10, 5, 15),
            "by_feature_rows": [SimpleNamespace(feature_key="plan", request_count=2, total_tokens=15)],
            "by_user_rows": [SimpleNamespace(user_id=10, request_count=2, total_tokens=15)],
        }

    @staticmethod
    def get_legacy_company_usage(session, *, company_id, start_at=None, end_at=None):
        company = FakeAdminUsageRepository.get_company(session, company_id)
        if not company:
            return None
        return {
            "entity": company,
            "totals": (4, 20, 10, 30),
            "by_feature_rows": [SimpleNamespace(feature_key="survey", request_count=4, total_tokens=30)],
            "by_user_rows": [SimpleNamespace(user_id=10, request_count=4, total_tokens=30)],
        }

    @staticmethod
    def get_llm_usage(session, **kwargs):
        return make_usage_rows()

    @staticmethod
    def ensure_company_grant(session, company_id, created_by=None):
        return None

    @staticmethod
    def company_token_balance(session, company_id):
        return 900

    @staticmethod
    def company_token_totals(session, company_id):
        return 1000, 100

    @staticmethod
    def create_token_topup(session, *, company_id, weighted_tokens, created_by=None, note=None):
        return SimpleNamespace(
            id=1,
            company_id=company_id,
            delta_weighted_tokens=weighted_tokens,
            reason="top_up",
            created_by=created_by,
            note=note,
            created_at=None,
        )

    @staticmethod
    def list_model_prices(session, *, provider="", active_only=True):
        return [
            SimpleNamespace(
                id=1,
                provider=provider or "openai",
                model="gpt-test",
                effective_from=None,
                effective_to=None,
                currency="USD",
                input_per_1m=1,
                cached_input_per_1m=0.1,
                output_per_1m=2,
                reasoning_policy=None,
                source_url=None,
            )
        ]

    @staticmethod
    def cleanup_expired_usage_events(*, retention_days):
        return 7


def make_service():
    return AdminUsageService(repository=FakeAdminUsageRepository, session_factory=fake_session_factory)


def test_admin_usage_service_legacy_usage_and_llm_usage_shapes():
    service = make_service()

    assert service.get_team_usage(team_id=404).status == "not_found"
    team_usage = service.get_team_usage(team_id=20)
    assert team_usage.status == "ok"
    assert team_usage.data["team"]["plan_code"] == "pro"
    assert team_usage.data["totals"]["total_tokens"] == 15
    assert team_usage.data["by_feature"][0]["feature_key"] == "plan"

    company_usage = service.get_company_usage(company_id=100)
    assert company_usage.status == "ok"
    assert company_usage.data["company"]["name"] == "Acme"
    assert company_usage.data["by_user"][0]["total_tokens"] == 30

    assert service.get_user_llm_usage(user_id=404, period="daily").status == "not_found"
    user_llm = service.get_user_llm_usage(user_id=10, period="daily")
    assert user_llm.status == "ok"
    assert user_llm.data["period"] == "daily"
    assert user_llm.data["totals"]["billable_weighted_tokens"] == 20
    assert user_llm.data["by_model"][0]["provider"] == "openai"


def test_admin_usage_service_token_price_and_cleanup_shapes():
    service = make_service()

    balance = service.get_company_token_balance(company_id=100)
    assert balance.status == "ok"
    assert balance.data["granted_weighted_tokens"] == 1000
    assert balance.data["used_weighted_tokens"] == 100
    assert balance.data["remaining_weighted_tokens"] == 900

    assert service.create_company_token_topup(company_id=404, weighted_tokens=10).status == "not_found"
    topup = service.create_company_token_topup(
        company_id=100,
        weighted_tokens=50,
        created_by=10,
        note="manual",
    )
    assert topup.status == "ok"
    assert topup.data["ledger"]["delta_weighted_tokens"] == 50

    prices = service.list_model_prices(provider="openai", active_only=True)
    assert prices.status == "ok"
    assert prices.data["prices"][0]["model"] == "gpt-test"

    cleanup = service.delete_expired_llm_usage_events(retention_days=90)
    assert cleanup.status == "ok"
    assert cleanup.data == {"retention_days": 90, "deleted_count": 7}
