"""Repository exports for the layered package."""

from __future__ import annotations

from importlib import import_module


_admin = import_module("reopsai.infrastructure.persistence.repositories.admin_repository")
_admin_backoffice = import_module(
    "reopsai.infrastructure.persistence.repositories.admin_backoffice_repository"
)
_admin_usage = import_module(
    "reopsai.infrastructure.persistence.repositories.admin_usage_repository"
)
_artifact_ai = import_module(
    "reopsai.infrastructure.persistence.repositories.artifact_ai_repository"
)
_auth = import_module("reopsai.infrastructure.persistence.repositories.auth_repository")
_b2b = import_module("reopsai.infrastructure.persistence.repositories.b2b_repository")
_demo = import_module("reopsai.infrastructure.persistence.repositories.demo_repository")
_dev_evaluator = import_module(
    "reopsai.infrastructure.persistence.repositories.dev_evaluator_repository"
)
_guideline = import_module("reopsai.infrastructure.persistence.repositories.guideline_repository")
_plan = import_module("reopsai.infrastructure.persistence.repositories.plan_repository")
_screener = import_module("reopsai.infrastructure.persistence.repositories.screener_repository")
_study = import_module("reopsai.infrastructure.persistence.repositories.study_repository")
_survey = import_module("reopsai.infrastructure.persistence.repositories.survey_repository")
_workspace = import_module("reopsai.infrastructure.persistence.repositories.workspace_repository")

AdminRepository = _admin.AdminRepository
AdminBackofficeRepository = _admin_backoffice.AdminBackofficeRepository
AdminUsageRepository = _admin_usage.AdminUsageRepository
ArtifactAiRepository = _artifact_ai.ArtifactAiRepository
AuthRepository = _auth.AuthRepository
B2bRepository = _b2b.B2bRepository
DemoRepository = _demo.DemoRepository
DevEvaluatorRepository = _dev_evaluator.DevEvaluatorRepository
GuidelineRepository = _guideline.GuidelineRepository
PlanRepository = _plan.PlanRepository
ScreenerRepository = _screener.ScreenerRepository
StudyRepository = _study.StudyRepository
SurveyRepository = _survey.SurveyRepository
WorkspaceRepository = _workspace.WorkspaceRepository
model_to_dict = _workspace.model_to_dict

BUSINESS_ACCOUNT_TYPE = _auth.BUSINESS_ACCOUNT_TYPE
INDIVIDUAL_ACCOUNT_TYPE = _auth.INDIVIDUAL_ACCOUNT_TYPE
DEFAULT_BUSINESS_PASSWORD = _b2b.DEFAULT_BUSINESS_PASSWORD
DEFAULT_ENTERPRISE_PASSWORD = _admin.DEFAULT_ENTERPRISE_PASSWORD
DELETED_TEAM_STATUS = _admin.DELETED_TEAM_STATUS

__all__ = [
    "AdminRepository",
    "AdminBackofficeRepository",
    "AdminUsageRepository",
    "ArtifactAiRepository",
    "AuthRepository",
    "B2bRepository",
    "DemoRepository",
    "DevEvaluatorRepository",
    "GuidelineRepository",
    "PlanRepository",
    "ScreenerRepository",
    "StudyRepository",
    "SurveyRepository",
    "WorkspaceRepository",
    "model_to_dict",
    "BUSINESS_ACCOUNT_TYPE",
    "INDIVIDUAL_ACCOUNT_TYPE",
    "DEFAULT_BUSINESS_PASSWORD",
    "DEFAULT_ENTERPRISE_PASSWORD",
    "DELETED_TEAM_STATUS",
]
