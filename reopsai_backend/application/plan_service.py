from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from api_logger import log_error
from db.repositories.plan_repository import PlanRepository
from utils.keyword_utils import fetch_project_keywords


@dataclass(frozen=True)
class PlanResult:
    status: str
    data: Any = None
    error: str | None = None


def parse_plan_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except Exception:
            return None


class PlanService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY, project_keyword_fetcher=None):
        if repository is None:
            repository = PlanRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory
        self.project_keyword_fetcher = project_keyword_fetcher or fetch_project_keywords

    def db_ready(self):
        return self.session_factory is not None

    def _validate_project_access_and_quota(self, db_session, *, project_id, user_id, tier):
        owner_id = self.repository.get_project_owner_id(db_session, project_id)
        if owner_id is None:
            return PlanResult("project_not_found")
        if int(owner_id) != int(user_id):
            return PlanResult("forbidden")
        if tier == "free":
            owned_project_ids = self.repository.list_owned_project_ids(db_session, user_id)
            study_count = self.repository.count_studies_for_projects(db_session, owned_project_ids)
            plan_count = self.repository.count_user_plan_artifacts(db_session, user_id)
            if study_count >= 1:
                return PlanResult("study_quota_exceeded")
            if plan_count >= 1:
                return PlanResult("plan_quota_exceeded")
        return PlanResult("ok", {"owner_id": owner_id})

    def create_oneshot_records(self, *, project_id, user_id, tier, form_data) -> PlanResult:
        if not self.db_ready():
            return PlanResult("db_unavailable")
        with self.session_factory() as db_session:
            access = self._validate_project_access_and_quota(
                db_session,
                project_id=project_id,
                user_id=user_id,
                tier=tier,
            )
            if access.status != "ok":
                return access

            study = self.repository.create_study(
                db_session,
                project_id=int(project_id),
                name=(form_data.get("studyName") or "").strip(),
                initial_input=(form_data.get("problemDefinition") or "").strip(),
                keywords=form_data.get("methodologies") or [],
                methodologies=form_data.get("methodologies") or [],
                participant_count=int(form_data.get("participantCount")) if form_data.get("participantCount") else None,
                start_date=parse_plan_date(form_data.get("startDate")),
                end_date=parse_plan_date(form_data.get("endDate")),
                timeline=form_data.get("timeline") or None,
                budget=form_data.get("budget") or None,
                target_audience=form_data.get("targetAudience") or None,
                additional_requirements=form_data.get("additionalRequirements") or None,
            )
            artifact = self.repository.create_plan_artifact(
                db_session,
                study_id=study.id,
                owner_id=access.data["owner_id"],
            )
            project_keywords = self.project_keyword_fetcher(project_id)
            return PlanResult(
                "ok",
                {
                    "study_id": study.id,
                    "study_slug": study.slug or "",
                    "artifact_id": artifact.id,
                    "project_keywords": project_keywords,
                },
            )

    def create_conversation_records(self, *, project_id, user_id, tier, study_name, ledger_text, selected_methods, ledger_cards) -> PlanResult:
        if not self.db_ready():
            return PlanResult("db_unavailable")
        try:
            ledger_json = json.dumps(ledger_cards, ensure_ascii=False)
        except Exception:
            ledger_json = "[]"
        with self.session_factory() as db_session:
            access = self._validate_project_access_and_quota(
                db_session,
                project_id=project_id,
                user_id=user_id,
                tier=tier,
            )
            if access.status != "ok":
                return access

            study = self.repository.create_study(
                db_session,
                project_id=int(project_id),
                name=study_name,
                initial_input=(ledger_text[:800] + "…") if len(ledger_text) > 800 else ledger_text,
                keywords=selected_methods,
                methodologies=selected_methods,
                additional_requirements=f"[CONTEXT_PACK_JSON]\n{ledger_json}",
            )
            artifact = self.repository.create_plan_artifact(
                db_session,
                study_id=study.id,
                owner_id=access.data["owner_id"],
            )
            project_keywords = self.project_keyword_fetcher(project_id)
            return PlanResult(
                "ok",
                {
                    "study_id": study.id,
                    "study_slug": study.slug or "",
                    "artifact_id": artifact.id,
                    "project_keywords": project_keywords,
                },
            )

    def cleanup_created_records(self, *, study_id=None, artifact_id=None) -> PlanResult:
        if not self.db_ready():
            return PlanResult("db_unavailable")
        try:
            with self.session_factory() as db_session:
                if artifact_id:
                    self.repository.delete_artifact(db_session, artifact_id)
                if study_id:
                    self.repository.delete_study(db_session, study_id)
            return PlanResult("ok")
        except Exception as exc:
            log_error(exc, "생성 실패 후 정리 작업 실패")
            return PlanResult("failed", error=str(exc))

    def complete_artifact(self, *, artifact_id, content) -> PlanResult:
        if not self.db_ready():
            return PlanResult("db_unavailable")
        with self.session_factory() as db_session:
            artifact = self.repository.complete_artifact(db_session, artifact_id=artifact_id, content=content)
            return PlanResult("ok" if artifact else "not_found")

    def delete_artifact(self, *, artifact_id) -> PlanResult:
        if not self.db_ready():
            return PlanResult("db_unavailable")
        with self.session_factory() as db_session:
            artifact = self.repository.delete_artifact(db_session, artifact_id)
            return PlanResult("ok" if artifact else "not_found")

    def fail_artifact(self, *, artifact_id, message) -> PlanResult:
        if not self.db_ready():
            return PlanResult("db_unavailable")
        with self.session_factory() as db_session:
            artifact = self.repository.fail_artifact(db_session, artifact_id=artifact_id, message=message)
            return PlanResult("ok" if artifact else "not_found")


plan_service = PlanService()
