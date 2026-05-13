from __future__ import annotations

from sqlalchemy import select

from reopsai.infrastructure.persistence.models.core import Artifact, ArtifactEditHistory


class ArtifactAiRepository:
    @staticmethod
    def get_artifact(session, artifact_id):
        return session.execute(
            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def list_edit_history(session, *, artifact_id, limit):
        return session.execute(
            select(ArtifactEditHistory)
            .where(ArtifactEditHistory.artifact_id == int(artifact_id))
            .order_by(ArtifactEditHistory.created_at.desc())
            .limit(int(limit))
        ).scalars().all()

    @staticmethod
    def create_edit_history(
        session,
        *,
        artifact_id,
        user_id,
        prompt,
        source,
        before_markdown,
        after_markdown,
        selection_from,
        selection_to,
    ):
        row = ArtifactEditHistory(
            artifact_id=int(artifact_id),
            user_id=user_id if isinstance(user_id, int) else None,
            prompt=prompt,
            source=source or None,
            before_markdown=before_markdown,
            after_markdown=after_markdown,
            selection_from=selection_from if isinstance(selection_from, int) else None,
            selection_to=selection_to if isinstance(selection_to, int) else None,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return row

