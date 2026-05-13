from __future__ import annotations

from sqlalchemy import select

from reopsai.infrastructure.persistence.models.core import Artifact, Project, Study


class GuidelineRepository:
    @staticmethod
    def get_study(session, study_id):
        return session.execute(select(Study).where(Study.id == int(study_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_project_owner_id(session, project_id):
        return session.execute(
            select(Project.owner_id).where(Project.id == int(project_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_guideline_artifact(session, *, study_id, owner_id):
        artifact = Artifact(
            study_id=int(study_id),
            artifact_type="guideline",
            content="",
            status="pending",
            owner_id=int(owner_id),
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
    def complete_artifact(session, *, artifact_id, content):
        artifact = GuidelineRepository.get_artifact(session, artifact_id)
        if artifact:
            artifact.content = content
            artifact.status = "completed"
        return artifact

    @staticmethod
    def delete_artifact(session, artifact_id):
        artifact = GuidelineRepository.get_artifact(session, artifact_id)
        if artifact:
            session.delete(artifact)
        return artifact

    @staticmethod
    def mark_artifact_failed(session, *, artifact_id, message):
        artifact = GuidelineRepository.get_artifact(session, artifact_id)
        if artifact:
            artifact.status = "failed"
            artifact.content = f"❌ 생성 실패: {message}"
        return artifact

