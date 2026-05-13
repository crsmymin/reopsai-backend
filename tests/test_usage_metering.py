from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from reopsai.shared import usage_metering


def test_provider_usage_extraction_shapes():
    openai_usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=40,
        total_tokens=140,
        prompt_tokens_details=SimpleNamespace(cached_tokens=25),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=7),
    )
    assert usage_metering.extract_openai_usage(openai_usage) == {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "cached_input_tokens": 25,
        "reasoning_tokens": 7,
    }

    gemini_usage = SimpleNamespace(
        promptTokenCount=10,
        candidatesTokenCount=5,
        totalTokenCount=15,
        cachedContentTokenCount=3,
        thoughtsTokenCount=2,
    )
    assert usage_metering.extract_gemini_usage(gemini_usage) == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_input_tokens": 3,
        "reasoning_tokens": 2,
    }


def test_feature_classification_covers_hidden_llm_routes():
    assert usage_metering.classify_feature_key("/api/conversation/message") == "plan_generation"
    assert usage_metering.classify_feature_key("/api/study-helper/chat") == "plan_generation"
    assert usage_metering.classify_feature_key("/api/workspace/generate-plan") == "workspace_ai"
    assert usage_metering.classify_feature_key("/api/unknown") is None


def test_usage_context_propagates_without_request_context():
    context = {
        "account_type": None,
        "company_id": 10,
        "team_id": 20,
        "user_id": 30,
        "feature_key": "plan_generation",
        "endpoint": "/api/conversation/message",
        "request_id": "req-1",
    }

    assert usage_metering.get_llm_usage_context() == {}

    def read_context():
        return usage_metering.build_llm_usage_context()

    assert usage_metering.run_with_llm_usage_context(context, read_context) == context
    assert usage_metering.get_llm_usage_context() == {}

    def read_context_stream():
        yield usage_metering.get_llm_usage_context()

    stream = usage_metering.stream_with_llm_usage_context(context, read_context_stream())
    assert list(stream) == [context]
    assert usage_metering.get_llm_usage_context() == {}


def test_build_llm_usage_context_uses_request_claims_and_explicit_values():
    app = Flask(__name__)
    app.config.update(
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)

    with app.app_context():
        token = create_access_token(
            identity="30",
            additional_claims={"company_id": 10, "account_type": "business"},
        )

    with app.test_request_context(
        "/api/study-helper/chat",
        headers={"Authorization": f"Bearer {token}", "X-User-ID": "30"},
    ):
        context = usage_metering.build_llm_usage_context(team_id=20, request_id="req-2")

    assert context == {
        "company_id": 10,
        "team_id": 20,
        "user_id": 30,
        "account_type": "business",
        "endpoint": "/api/study-helper/chat",
        "feature_key": "plan_generation",
        "request_id": "req-2",
    }


def test_cost_and_weighted_tokens_preserve_existing_formula():
    price = SimpleNamespace(
        input_per_1m=Decimal("0.30"),
        cached_input_per_1m=Decimal("0.03"),
        output_per_1m=Decimal("0.60"),
    )
    estimated_cost, weighted_tokens = usage_metering.calculate_cost_and_weighted_tokens(
        price=price,
        prompt_tokens=1000,
        completion_tokens=500,
        cached_input_tokens=200,
    )

    assert estimated_cost == Decimal("0.000546")
    assert weighted_tokens == 3640


def test_root_usage_metering_wrapper_reexports_absorbed_helpers():
    legacy_usage_metering = import_module("utils.usage_metering")

    assert legacy_usage_metering.record_llm_call is usage_metering.record_llm_call
    assert legacy_usage_metering.extract_openai_usage is usage_metering.extract_openai_usage
    assert legacy_usage_metering.FEATURE_PREFIXES is usage_metering.FEATURE_PREFIXES
