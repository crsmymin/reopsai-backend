from sqlalchemy import select

from db.models.core import Artifact


class DevEvaluatorRepository:
    @staticmethod
    def get_artifact_content(session, artifact_id):
        artifact = session.execute(
            select(Artifact).where(Artifact.id == artifact_id).limit(1)
        ).scalar_one_or_none()
        if not artifact:
            return None
        return artifact.content or ""
