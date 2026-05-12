from __future__ import annotations

from sqlalchemy import func, select

from db.models.core import Artifact, Project, Study


class PlanRepository:
    @staticmethod
    def get_project_owner_id(session, project_id):
        return session.execute(
            select(Project.owner_id).where(Project.id == int(project_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def list_owned_project_ids(session, user_id):
        return session.execute(
            select(Project.id).where(Project.owner_id == int(user_id))
        ).scalars().all()

    @staticmethod
    def count_studies_for_projects(session, project_ids):
        if not project_ids:
            return 0
        return (
            session.execute(
                select(func.count()).select_from(Study).where(Study.project_id.in_(project_ids))
            ).scalar_one()
            or 0
        )

    @staticmethod
    def count_user_plan_artifacts(session, user_id):
        return (
            session.execute(
                select(func.count()).select_from(Artifact).where(
                    Artifact.artifact_type == "plan",
                    Artifact.owner_id == int(user_id),
                )
            ).scalar_one()
            or 0
        )

    @staticmethod
    def create_study(session, **kwargs):
        study = Study(**kwargs)
        session.add(study)
        session.flush()
        session.refresh(study)
        return study

    @staticmethod
    def create_plan_artifact(session, *, study_id, owner_id):
        artifact = Artifact(
            study_id=int(study_id),
            artifact_type="plan",
            content="",
            owner_id=int(owner_id),
            status="pending",
        )
        session.add(artifact)
        session.flush()
        session.refresh(artifact)
        return artifact

    @staticmethod
    def get_artifact(session, artifact_id):
        return session.execute(
            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_study(session, study_id):
        return session.execute(
            select(Study).where(Study.id == int(study_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def complete_artifact(session, *, artifact_id, content):
        artifact = PlanRepository.get_artifact(session, artifact_id)
        if artifact:
            artifact.content = content
            artifact.status = "completed"
        return artifact

    @staticmethod
    def delete_artifact(session, artifact_id):
        artifact = PlanRepository.get_artifact(session, artifact_id)
        if artifact:
            session.delete(artifact)
        return artifact

    @staticmethod
    def fail_artifact(session, *, artifact_id, message):
        artifact = PlanRepository.get_artifact(session, artifact_id)
        if artifact:
            artifact.status = "failed"
            artifact.content = f"❌ 생성 실패: {message}"
        return artifact

    @staticmethod
    def delete_study(session, study_id):
        study = PlanRepository.get_study(session, study_id)
        if study:
            session.delete(study)
        return study
