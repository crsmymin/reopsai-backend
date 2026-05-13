from flask import Flask, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required

from reopsai.application import keywords as keyword_module
from reopsai.application.keywords import _refine_extracted_keywords
from reopsai.shared.idempotency import (
    _complete_idempotency_entry,
    _fail_idempotency_entry,
    _idempotency_cache,
    _reserve_idempotency_entry,
    _respond_from_entry,
)
from reopsai.shared.llm import _safe_parse_json_object, parse_llm_json_response
from reopsai.shared.request import _extract_request_user_id


def test_llm_json_helpers_parse_fenced_and_repair_escaped_json():
    fenced = parse_llm_json_response({"content": '```json\n{"name": "Study"}\n```'})
    assert fenced == {"name": "Study"}

    repaired = parse_llm_json_response({"content": '{"pattern": "\\s+"}'})
    assert repaired == {"pattern": "\\s+"}

    assert _safe_parse_json_object('prefix ```json\n{"ok": true}\n``` suffix') == {"ok": True}
    assert _safe_parse_json_object("no json") is None


def test_keyword_refinement_removes_stopwords_duplicates_and_preserves_order():
    assert _refine_extracted_keywords(
        ["연구", "Checkout", "checkout", "UX"],
        ["survey", "UX"],
    ) == ["Checkout", "UX", "survey"]


def test_fetch_project_keywords_uses_infrastructure_repository_facade(monkeypatch):
    calls = []
    fake_session = object()

    class FakeSessionScope:
        def __enter__(self):
            return fake_session

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeWorkspaceRepository:
        @staticmethod
        def get_project_by_id(session, project_id):
            assert session is fake_session
            assert project_id == 123
            return {"keywords": ["alpha", 7, " ", None]}

    class FakeEngineModule:
        @staticmethod
        def session_scope():
            return FakeSessionScope()

    class FakeRepositoriesModule:
        WorkspaceRepository = FakeWorkspaceRepository

    def fake_import_module(name):
        calls.append(name)
        if name == "reopsai.infrastructure.persistence.engine":
            return FakeEngineModule
        if name == "reopsai.infrastructure.repositories":
            return FakeRepositoriesModule
        if name == "db.engine":
            raise AssertionError("legacy engine path should not be imported")
        if name == "db.repositories.workspace_repository":
            raise AssertionError("legacy repository path should not be imported")
        raise ImportError(name)

    monkeypatch.setattr(keyword_module, "import_module", fake_import_module)

    assert keyword_module.fetch_project_keywords("123") == ["alpha", "7"]
    assert "reopsai.infrastructure.persistence.engine" in calls
    assert "reopsai.infrastructure.repositories" in calls
    assert "db.engine" not in calls
    assert "db.repositories.workspace_repository" not in calls


def test_fetch_project_keywords_returns_empty_when_lazy_import_fails(monkeypatch):
    def fake_import_module(name):
        if name == "reopsai.infrastructure.persistence.engine":
            return object()
        raise ImportError(name)

    monkeypatch.setattr(keyword_module, "import_module", fake_import_module)

    assert keyword_module.fetch_project_keywords("123") == []


def test_idempotency_helpers_reserve_complete_fail_and_respond():
    _idempotency_cache.clear()
    app = Flask(__name__)

    with app.app_context():
        entry, is_new = _reserve_idempotency_entry("key-1")
        assert is_new is True

        same_entry, is_new = _reserve_idempotency_entry("key-1")
        assert same_entry is entry
        assert is_new is False

        _complete_idempotency_entry("key-1", {"success": True}, 201)
        response, status = _respond_from_entry(entry)
        assert status == 201
        assert response.get_json() == {"success": True}

        failed_entry, _ = _reserve_idempotency_entry("key-2")
        _fail_idempotency_entry("key-2", {"success": False, "error": "boom"}, 503)
        response, status = _respond_from_entry(failed_entry)
        assert status == 503
        assert response.get_json() == {"success": False, "error": "boom"}


def test_request_user_id_helper_uses_header_then_jwt_identity():
    app = Flask(__name__)
    app.config.update(
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)

    @app.get("/header")
    def header_id():
        user_id, err_body, err_status = _extract_request_user_id()
        return jsonify({"user_id": user_id, "has_error": err_body is not None, "err_status": err_status})

    @app.get("/jwt")
    @jwt_required()
    def jwt_id():
        user_id, err_body, err_status = _extract_request_user_id()
        return jsonify({"user_id": user_id, "has_error": err_body is not None, "err_status": err_status})

    client = app.test_client()
    assert client.get("/header", headers={"X-User-ID": "42"}).get_json() == {
        "user_id": 42,
        "has_error": False,
        "err_status": None,
    }

    with app.app_context():
        token = create_access_token(identity="43")
    assert client.get("/jwt", headers={"Authorization": f"Bearer {token}"}).get_json() == {
        "user_id": 43,
        "has_error": False,
        "err_status": None,
    }


def test_root_utils_wrappers_reexport_absorbed_helpers():
    from reopsai.application import keywords as absorbed_keywords
    from reopsai.shared import idempotency as absorbed_idempotency
    from reopsai.shared import llm as absorbed_llm
    from reopsai.shared import request as absorbed_request
    from utils import idempotency as legacy_idempotency
    from utils import keyword_utils as legacy_keywords
    from utils import llm_utils as legacy_llm
    from utils import request_utils as legacy_request

    assert legacy_request._extract_request_user_id is absorbed_request._extract_request_user_id
    assert legacy_idempotency._reserve_idempotency_entry is absorbed_idempotency._reserve_idempotency_entry
    assert legacy_llm.parse_llm_json_response is absorbed_llm.parse_llm_json_response
    assert legacy_keywords._refine_extracted_keywords is absorbed_keywords._refine_extracted_keywords


def test_root_repository_wrappers_reexport_moved_repositories():
    from db.repositories import admin_backoffice_repository as legacy_admin_backoffice
    from db.repositories import admin_repository as legacy_admin
    from db.repositories import admin_usage_repository as legacy_admin_usage
    from db.repositories import artifact_ai_repository as legacy_artifact_ai
    from db.repositories import auth_repository as legacy_auth
    from db.repositories import b2b_repository as legacy_b2b
    from db.repositories import demo_repository as legacy_demo
    from db.repositories import dev_evaluator_repository as legacy_dev_evaluator
    from db.repositories import guideline_repository as legacy_guideline
    from db.repositories import plan_repository as legacy_plan
    from db.repositories import screener_repository as legacy_screener
    from db.repositories import study_repository as legacy_study
    from db.repositories import survey_repository as legacy_survey
    from db.repositories import workspace_repository as legacy_workspace
    from reopsai.infrastructure.persistence.repositories import (
        admin_backoffice_repository as moved_admin_backoffice,
    )
    from reopsai.infrastructure.persistence.repositories import admin_repository as moved_admin
    from reopsai.infrastructure.persistence.repositories import (
        admin_usage_repository as moved_admin_usage,
    )
    from reopsai.infrastructure.persistence.repositories import (
        artifact_ai_repository as moved_artifact_ai,
    )
    from reopsai.infrastructure.persistence.repositories import auth_repository as moved_auth
    from reopsai.infrastructure.persistence.repositories import b2b_repository as moved_b2b
    from reopsai.infrastructure.persistence.repositories import demo_repository as moved_demo
    from reopsai.infrastructure.persistence.repositories import (
        dev_evaluator_repository as moved_dev_evaluator,
    )
    from reopsai.infrastructure.persistence.repositories import (
        guideline_repository as moved_guideline,
    )
    from reopsai.infrastructure.persistence.repositories import plan_repository as moved_plan
    from reopsai.infrastructure.persistence.repositories import (
        screener_repository as moved_screener,
    )
    from reopsai.infrastructure.persistence.repositories import study_repository as moved_study
    from reopsai.infrastructure.persistence.repositories import survey_repository as moved_survey
    from reopsai.infrastructure.persistence.repositories import (
        workspace_repository as moved_workspace,
    )
    from reopsai.infrastructure import repositories as repository_facade

    assert legacy_admin.AdminRepository is moved_admin.AdminRepository
    assert legacy_admin.DEFAULT_ENTERPRISE_PASSWORD == moved_admin.DEFAULT_ENTERPRISE_PASSWORD
    assert legacy_admin.DELETED_TEAM_STATUS == moved_admin.DELETED_TEAM_STATUS
    assert legacy_admin_usage.AdminUsageRepository is moved_admin_usage.AdminUsageRepository
    assert legacy_admin_backoffice.AdminBackofficeRepository is moved_admin_backoffice.AdminBackofficeRepository
    assert legacy_admin_backoffice.DEFAULT_ENTERPRISE_PASSWORD == moved_admin_backoffice.DEFAULT_ENTERPRISE_PASSWORD
    assert legacy_artifact_ai.ArtifactAiRepository is moved_artifact_ai.ArtifactAiRepository
    assert legacy_auth.AuthRepository is moved_auth.AuthRepository
    assert legacy_auth.BUSINESS_ACCOUNT_TYPE == moved_auth.BUSINESS_ACCOUNT_TYPE
    assert legacy_auth.INDIVIDUAL_ACCOUNT_TYPE == moved_auth.INDIVIDUAL_ACCOUNT_TYPE
    assert legacy_b2b.B2bRepository is moved_b2b.B2bRepository
    assert legacy_b2b.DEFAULT_BUSINESS_PASSWORD == moved_b2b.DEFAULT_BUSINESS_PASSWORD
    assert legacy_demo.DemoRepository is moved_demo.DemoRepository
    assert legacy_dev_evaluator.DevEvaluatorRepository is moved_dev_evaluator.DevEvaluatorRepository
    assert legacy_guideline.GuidelineRepository is moved_guideline.GuidelineRepository
    assert legacy_plan.PlanRepository is moved_plan.PlanRepository
    assert legacy_screener.ScreenerRepository is moved_screener.ScreenerRepository
    assert legacy_study.StudyRepository is moved_study.StudyRepository
    assert legacy_survey.SurveyRepository is moved_survey.SurveyRepository
    assert legacy_workspace.WorkspaceRepository is moved_workspace.WorkspaceRepository
    assert legacy_workspace.model_to_dict is moved_workspace.model_to_dict
    assert repository_facade.AdminRepository is moved_admin.AdminRepository
    assert repository_facade.AdminUsageRepository is moved_admin_usage.AdminUsageRepository
    assert repository_facade.AdminBackofficeRepository is moved_admin_backoffice.AdminBackofficeRepository
    assert repository_facade.DEFAULT_ENTERPRISE_PASSWORD == moved_admin.DEFAULT_ENTERPRISE_PASSWORD
    assert repository_facade.DELETED_TEAM_STATUS == moved_admin.DELETED_TEAM_STATUS
    assert repository_facade.ArtifactAiRepository is moved_artifact_ai.ArtifactAiRepository
    assert repository_facade.AuthRepository is moved_auth.AuthRepository
    assert repository_facade.BUSINESS_ACCOUNT_TYPE == moved_auth.BUSINESS_ACCOUNT_TYPE
    assert repository_facade.INDIVIDUAL_ACCOUNT_TYPE == moved_auth.INDIVIDUAL_ACCOUNT_TYPE
    assert repository_facade.B2bRepository is moved_b2b.B2bRepository
    assert repository_facade.DEFAULT_BUSINESS_PASSWORD == moved_b2b.DEFAULT_BUSINESS_PASSWORD
    assert repository_facade.DemoRepository is moved_demo.DemoRepository
    assert repository_facade.DevEvaluatorRepository is moved_dev_evaluator.DevEvaluatorRepository
    assert repository_facade.GuidelineRepository is moved_guideline.GuidelineRepository
    assert repository_facade.PlanRepository is moved_plan.PlanRepository
    assert repository_facade.ScreenerRepository is moved_screener.ScreenerRepository
    assert repository_facade.StudyRepository is moved_study.StudyRepository
    assert repository_facade.SurveyRepository is moved_survey.SurveyRepository
    assert repository_facade.WorkspaceRepository is moved_workspace.WorkspaceRepository
    assert repository_facade.model_to_dict is moved_workspace.model_to_dict


def test_root_db_wrappers_reexport_moved_persistence_symbols():
    from db import base as legacy_base
    from db import engine as legacy_engine
    from db.models import core as legacy_models
    from reopsai.infrastructure.persistence import base as moved_base
    from reopsai.infrastructure.persistence import engine as moved_engine
    from reopsai.infrastructure.persistence.models import core as moved_models

    assert legacy_base.Base is moved_base.Base
    assert legacy_engine.init_engine is moved_engine.init_engine
    assert legacy_engine.get_engine is moved_engine.get_engine
    assert legacy_engine.get_session_factory is moved_engine.get_session_factory
    assert legacy_engine.session_scope is moved_engine.session_scope
    assert legacy_models.User is moved_models.User
    assert legacy_models.Project is moved_models.Project
    assert legacy_models.LlmUsageDailyAggregate is moved_models.LlmUsageDailyAggregate
