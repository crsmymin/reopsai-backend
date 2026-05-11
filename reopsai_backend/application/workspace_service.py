"""Workspace use cases.

This service starts the migration of route-level workspace behavior into the
application layer while preserving the current repository implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class WorkspaceSummary:
    projects: list[dict]
    all_studies: list[dict]
    recent_artifacts: list[dict]


@dataclass(frozen=True)
class WorkspaceResult:
    status: str
    data: Any = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def normalize_tags(raw_tags: Any) -> list:
    if isinstance(raw_tags, list):
        return raw_tags
    if isinstance(raw_tags, str):
        return [raw_tags]
    return []


def build_project_update_data(data: dict) -> dict:
    update_data = {}
    if "name" in data:
        update_data["name"] = data["name"]
    if "productUrl" in data:
        update_data["product_url"] = data["productUrl"]
    if "tags" in data:
        update_data["keywords"] = normalize_tags(data["tags"])
    return update_data


def build_study_update_data(data: dict) -> dict:
    allowed_fields = {
        "initial_input",
        "name",
        "methodologies",
        "target_audience",
        "participant_count",
        "start_date",
        "end_date",
        "timeline",
        "budget",
        "additional_requirements",
    }
    return {key: value for key, value in data.items() if key in allowed_fields}


class WorkspaceService:
    def __init__(self, repository=None, session_factory=None):
        if repository is None:
            from reopsai_backend.infrastructure.repositories import WorkspaceRepository

            repository = WorkspaceRepository
        if session_factory is None:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope

        self.repository = repository
        self.session_factory = session_factory

    def list_projects(self, owner_ids: Sequence[int]) -> list[dict]:
        with self.session_factory() as db_session:
            return self.repository.get_projects_by_owner_ids(db_session, owner_ids)

    def create_project(
        self, *, owner_id: int, name: str, product_url: str = "", tags: Any = None
    ) -> dict:
        with self.session_factory() as db_session:
            return self.repository.create_project(
                db_session,
                owner_id=int(owner_id),
                name=name,
                product_url=product_url,
                keywords=normalize_tags(tags),
            )

    def delete_project(self, *, project_id: int, owner_id: int) -> bool:
        with self.session_factory() as db_session:
            return self.repository.delete_project_for_owner(db_session, project_id, int(owner_id))

    def update_project(self, *, project_id: int, owner_id: int, data: dict) -> WorkspaceResult:
        update_data = build_project_update_data(data)
        if not update_data:
            return WorkspaceResult(status="empty_update")
        with self.session_factory() as db_session:
            updated = self.repository.update_project_for_owner(
                db_session, project_id, int(owner_id), update_data
            )
        if not updated:
            return WorkspaceResult(status="not_found")
        return WorkspaceResult(status="ok", data=updated)

    def get_project(self, *, project_id: int, owner_ids: Sequence[int]) -> WorkspaceResult:
        with self.session_factory() as db_session:
            project, allowed = self.repository.get_project_for_owner_ids(
                db_session, project_id, owner_ids
            )
        if not project:
            return WorkspaceResult(status="not_found")
        if not allowed:
            return WorkspaceResult(status="forbidden")
        return WorkspaceResult(status="ok", data=project)

    def list_project_studies(self, *, project_id: int, owner_ids: Sequence[int]) -> WorkspaceResult:
        with self.session_factory() as db_session:
            project_owner_id = self.repository.get_project_owner_id(db_session, project_id)
            if project_owner_id is None:
                return WorkspaceResult(status="not_found")
            allowed_owner_ids = {str(owner_id) for owner_id in owner_ids if owner_id is not None}
            if str(project_owner_id) not in allowed_owner_ids:
                return WorkspaceResult(status="forbidden")
            studies = self.repository.get_studies_by_project_id(db_session, project_id)
        return WorkspaceResult(status="ok", data=studies)

    def get_study(self, *, study_id: int, owner_ids: Sequence[int]) -> WorkspaceResult:
        with self.session_factory() as db_session:
            study, allowed = self.repository.get_study_for_owner_ids(db_session, study_id, owner_ids)
        if not study:
            return WorkspaceResult(status="not_found")
        if not allowed:
            return WorkspaceResult(status="forbidden")
        return WorkspaceResult(status="ok", data=study)

    def get_study_schedule(self, *, study_id: int, owner_ids: Sequence[int]) -> WorkspaceResult:
        with self.session_factory() as db_session:
            study, allowed = self.repository.get_study_for_owner_ids(db_session, study_id, owner_ids)
            if not study:
                return WorkspaceResult(status="not_found")
            if not allowed:
                return WorkspaceResult(status="forbidden")
            schedule = self.repository.get_latest_schedule_by_study_id(db_session, study_id)
        return WorkspaceResult(status="ok", data=schedule)

    def update_artifact_content(self, *, artifact_id: int, owner_id: int, content: str) -> bool:
        with self.session_factory() as db_session:
            return self.repository.update_artifact_content_for_owner(
                db_session, artifact_id, int(owner_id), content
            )

    def list_study_artifacts(self, *, study_id: int, owner_ids: Sequence[int]) -> WorkspaceResult:
        with self.session_factory() as db_session:
            study, allowed = self.repository.get_study_for_owner_ids(db_session, study_id, owner_ids)
            if not study:
                return WorkspaceResult(status="not_found")
            if not allowed:
                return WorkspaceResult(status="forbidden")
            artifacts = self.repository.get_artifacts_by_study_id(db_session, study_id)
        return WorkspaceResult(status="ok", data=artifacts)

    def authorize_study(self, *, study_id: int, owner_ids: Sequence[int]) -> WorkspaceResult:
        return self.get_study(study_id=study_id, owner_ids=owner_ids)

    def get_artifact_for_stream_start(self, *, artifact_id: int, owner_id: int) -> Optional[dict]:
        with self.session_factory() as db_session:
            return self.repository.get_artifact_by_id_for_owner(db_session, artifact_id, int(owner_id))

    def get_artifact_for_stream_poll(self, *, artifact_id: int) -> Optional[dict]:
        with self.session_factory() as db_session:
            return self.repository.get_artifact_by_id(db_session, artifact_id)

    def delete_study(self, *, study_id: int, owner_id: int) -> WorkspaceResult:
        with self.session_factory() as db_session:
            deleted, status = self.repository.delete_study_for_owner(
                db_session, study_id, int(owner_id)
            )
        if deleted:
            return WorkspaceResult(status="ok")
        return WorkspaceResult(status=status)

    def delete_artifact(self, *, artifact_id: int, owner_id: int) -> bool:
        with self.session_factory() as db_session:
            return self.repository.delete_artifact_for_owner(db_session, artifact_id, int(owner_id))

    def update_study(self, *, study_id: int, owner_id: int, data: dict) -> WorkspaceResult:
        update_data = build_study_update_data(data)
        if not update_data:
            return WorkspaceResult(status="empty_update")

        with self.session_factory() as db_session:
            study_row = self.repository.get_study_by_id_with_owner(db_session, study_id)
            if not study_row:
                return WorkspaceResult(status="not_found")
            _study, actual_owner_id = study_row
            if actual_owner_id is not None and int(actual_owner_id) != int(owner_id):
                return WorkspaceResult(status="forbidden")

            updated = self.repository.update_study_for_owner(
                db_session, study_id, int(owner_id), update_data
            )
        if not updated:
            return WorkspaceResult(status="not_found")
        return WorkspaceResult(status="ok", data=updated)

    def prepare_plan_regeneration(self, *, study_id: int, owner_id: int) -> WorkspaceResult:
        with self.session_factory() as db_session:
            payload, status = self.repository.replace_plan_artifact_for_study_owner(
                db_session, study_id, int(owner_id)
            )
        if payload:
            return WorkspaceResult(status="ok", data=payload)
        return WorkspaceResult(status=status)

    def complete_artifact(self, *, artifact_id: int, content: str) -> bool:
        with self.session_factory() as db_session:
            return self.repository.complete_artifact(db_session, artifact_id, content)

    def delete_artifact_by_id(self, *, artifact_id: int) -> bool:
        with self.session_factory() as db_session:
            return self.repository.delete_artifact_by_id(db_session, artifact_id)

    def get_workspace_summary(self, owner_ids: Sequence[int]) -> WorkspaceSummary:
        with self.session_factory() as db_session:
            projects = self.repository.get_projects_by_owner_ids(db_session, owner_ids)
            project_ids = [project["id"] for project in projects]
            studies = self.repository.get_studies_by_project_ids(db_session, project_ids)
            study_ids = [study["id"] for study in studies]
            artifacts = self.repository.get_artifacts_by_study_ids(db_session, study_ids)

        studies_by_project = self.repository.group_studies_by_project(studies)
        artifacts_by_study = self.repository.group_artifacts_by_study(artifacts)

        projects_with_studies = []
        all_studies = []
        for project in projects:
            project_with_studies = project.copy()
            project_studies = studies_by_project.get(project["id"], [])
            for study in project_studies:
                study["artifacts"] = artifacts_by_study.get(study["id"], [])
                all_studies.append(study.copy())
            project_with_studies["studies"] = project_studies
            projects_with_studies.append(project_with_studies)

        all_artifacts = []
        for study in all_studies:
            for artifact in study.get("artifacts", []):
                artifact_with_study = artifact.copy()
                artifact_with_study["study_name"] = study.get("name", "")
                artifact_with_study["study_slug"] = study.get("slug", study.get("id"))
                all_artifacts.append(artifact_with_study)

        recent_artifacts = sorted(
            all_artifacts,
            key=lambda artifact: artifact.get("created_at", ""),
            reverse=True,
        )[:3]

        return WorkspaceSummary(
            projects=projects_with_studies,
            all_studies=all_studies,
            recent_artifacts=recent_artifacts,
        )


workspace_service = WorkspaceService()
