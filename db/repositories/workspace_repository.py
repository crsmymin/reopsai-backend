from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from db.models.core import Artifact, Project, Study, StudySchedule, Team, TeamMember


def _serialize_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def model_to_dict(model) -> dict:
    data = {}
    for column in model.__table__.columns:
        data[column.name] = _serialize_value(getattr(model, column.name))
    return data


class WorkspaceRepository:
    @staticmethod
    def _generate_unique_project_slug(session: Session) -> str:
        while True:
            candidate = str(uuid4())
            existing_id = session.execute(
                select(Project.id).where(Project.slug == candidate).limit(1)
            ).scalar_one_or_none()
            if existing_id is None:
                return candidate

    @staticmethod
    def get_primary_team_id_for_user(session: Session, user_id: int) -> Optional[int]:
        owner_team = session.execute(
            select(Team.id).where(Team.owner_id == user_id, Team.status != "deleted").limit(1)
        ).scalar_one_or_none()
        if owner_team is not None:
            return int(owner_team)

        member_team = session.execute(
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == user_id, Team.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()
        return int(member_team) if member_team is not None else None

    @staticmethod
    def get_team_member_ids(session: Session, team_id: int) -> List[int]:
        rows = session.execute(
            select(TeamMember.user_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.team_id == team_id, Team.status != "deleted")
        ).scalars().all()
        return [int(row) for row in rows if row is not None]

    @staticmethod
    def get_projects_by_owner_ids(session: Session, owner_ids: Sequence[int]) -> List[dict]:
        if not owner_ids:
            return []
        rows = session.execute(
            select(Project)
            .where(Project.owner_id.in_(owner_ids))
            .order_by(Project.created_at.desc())
        ).scalars().all()
        return [model_to_dict(row) for row in rows]

    @staticmethod
    def get_studies_by_project_ids(session: Session, project_ids: Sequence[int]) -> List[dict]:
        if not project_ids:
            return []
        rows = session.execute(
            select(Study)
            .where(Study.project_id.in_(project_ids))
            .order_by(Study.created_at.desc())
        ).scalars().all()
        return [model_to_dict(row) for row in rows]

    @staticmethod
    def get_artifacts_by_study_ids(session: Session, study_ids: Sequence[int]) -> List[dict]:
        if not study_ids:
            return []
        rows = session.execute(
            select(Artifact)
            .where(Artifact.study_id.in_(study_ids))
            .order_by(Artifact.created_at.desc())
        ).scalars().all()
        return [model_to_dict(row) for row in rows]

    @staticmethod
    def create_project(
        session: Session, *, owner_id: int, name: str, product_url: str, keywords: list
    ) -> dict:
        slug = WorkspaceRepository._generate_unique_project_slug(session)
        project = Project(
            owner_id=owner_id,
            name=name,
            slug=slug,
            product_url=product_url or None,
            keywords=keywords or [],
        )
        session.add(project)
        session.flush()
        session.refresh(project)
        return model_to_dict(project)

    @staticmethod
    def delete_project_for_owner(session: Session, project_id: int, owner_id: int) -> bool:
        result = session.execute(
            delete(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        )
        return result.rowcount > 0

    @staticmethod
    def update_project_for_owner(session: Session, project_id: int, owner_id: int, update_data: dict) -> Optional[dict]:
        if not update_data:
            return None
        session.execute(
            update(Project)
            .where(Project.id == project_id, Project.owner_id == owner_id)
            .values(**update_data)
        )
        project = session.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == owner_id).limit(1)
        ).scalar_one_or_none()
        if not project:
            return None
        return model_to_dict(project)

    @staticmethod
    def get_project_by_id(session: Session, project_id: int) -> Optional[dict]:
        row = session.execute(select(Project).where(Project.id == project_id).limit(1)).scalar_one_or_none()
        return model_to_dict(row) if row else None

    @staticmethod
    def get_project_for_owner_ids(
        session: Session, project_id: int, owner_ids: Sequence[int]
    ) -> Tuple[Optional[dict], bool]:
        project = WorkspaceRepository.get_project_by_id(session, project_id)
        if not project:
            return None, False
        allowed_owner_ids = {str(owner_id) for owner_id in owner_ids if owner_id is not None}
        owner_id = project.get("owner_id")
        if owner_id is not None and str(owner_id) not in allowed_owner_ids:
            return project, False
        return project, True

    @staticmethod
    def get_project_owner_id(session: Session, project_id: int) -> Optional[int]:
        owner_id = session.execute(
            select(Project.owner_id).where(Project.id == project_id).limit(1)
        ).scalar_one_or_none()
        return int(owner_id) if owner_id is not None else None

    @staticmethod
    def get_study_by_id_with_owner(session: Session, study_id: int) -> Optional[Tuple[dict, int]]:
        row = session.execute(
            select(Study, Project.owner_id)
            .join(Project, Project.id == Study.project_id)
            .where(Study.id == study_id)
            .limit(1)
        ).first()
        if not row:
            return None
        study, owner_id = row
        return model_to_dict(study), int(owner_id) if owner_id is not None else None

    @staticmethod
    def get_study_for_owner_ids(
        session: Session, study_id: int, owner_ids: Sequence[int]
    ) -> Tuple[Optional[dict], bool]:
        row = WorkspaceRepository.get_study_by_id_with_owner(session, study_id)
        if not row:
            return None, False
        study, owner_id = row
        allowed_owner_ids = {str(owner_id) for owner_id in owner_ids if owner_id is not None}
        if owner_id is not None and str(owner_id) not in allowed_owner_ids:
            return study, False
        return study, True

    @staticmethod
    def get_studies_by_project_id(session: Session, project_id: int) -> List[dict]:
        rows = session.execute(
            select(Study).where(Study.project_id == project_id).order_by(Study.created_at.desc())
        ).scalars().all()
        return [model_to_dict(row) for row in rows]

    @staticmethod
    def get_latest_schedule_by_study_id(session: Session, study_id: int) -> Optional[dict]:
        row = session.execute(
            select(StudySchedule)
            .where(StudySchedule.study_id == study_id)
            .order_by(StudySchedule.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return model_to_dict(row) if row else None

    @staticmethod
    def update_artifact_content_for_owner(
        session: Session, artifact_id: int, owner_id: int, content: str
    ) -> bool:
        result = session.execute(
            update(Artifact)
            .where(Artifact.id == artifact_id, Artifact.owner_id == owner_id)
            .values(content=content, updated_at=datetime.utcnow())
        )
        return result.rowcount > 0

    @staticmethod
    def get_artifact_by_id(session: Session, artifact_id: int) -> Optional[dict]:
        row = session.execute(select(Artifact).where(Artifact.id == artifact_id).limit(1)).scalar_one_or_none()
        return model_to_dict(row) if row else None

    @staticmethod
    def get_artifact_by_id_for_owner(
        session: Session, artifact_id: int, owner_id: int
    ) -> Optional[dict]:
        row = session.execute(
            select(Artifact)
            .where(Artifact.id == artifact_id, Artifact.owner_id == owner_id)
            .limit(1)
        ).scalar_one_or_none()
        return model_to_dict(row) if row else None

    @staticmethod
    def get_artifacts_by_study_id(session: Session, study_id: int) -> List[dict]:
        rows = session.execute(
            select(Artifact).where(Artifact.study_id == study_id).order_by(Artifact.created_at.desc())
        ).scalars().all()
        return [model_to_dict(row) for row in rows]

    @staticmethod
    def update_study_for_owner(
        session: Session, study_id: int, owner_id: int, update_data: dict
    ) -> Optional[dict]:
        if not update_data:
            return None
        session.execute(
            update(Study)
            .where(
                Study.id == study_id,
                Study.project_id.in_(select(Project.id).where(Project.owner_id == owner_id)),
            )
            .values(**update_data)
        )
        row = session.execute(select(Study).where(Study.id == study_id).limit(1)).scalar_one_or_none()
        if not row:
            return None
        # owner 검증
        owner_check = session.execute(
            select(Project.owner_id).where(Project.id == row.project_id).limit(1)
        ).scalar_one_or_none()
        if owner_check is None or int(owner_check) != int(owner_id):
            return None
        return model_to_dict(row)

    @staticmethod
    def delete_study_for_owner(session: Session, study_id: int, owner_id: int) -> Tuple[bool, str]:
        study = session.execute(select(Study).where(Study.id == study_id).limit(1)).scalar_one_or_none()
        if not study:
            return False, "not_found"

        actual_owner_id = session.execute(
            select(Project.owner_id).where(Project.id == study.project_id).limit(1)
        ).scalar_one_or_none()
        if actual_owner_id is None or int(actual_owner_id) != int(owner_id):
            return False, "forbidden"

        session.delete(study)
        return True, "deleted"

    @staticmethod
    def delete_artifact_for_owner(session: Session, artifact_id: int, owner_id: int) -> bool:
        artifact = session.execute(
            select(Artifact)
            .where(Artifact.id == artifact_id, Artifact.owner_id == owner_id)
            .limit(1)
        ).scalar_one_or_none()
        if not artifact:
            return False
        session.delete(artifact)
        return True

    @staticmethod
    def replace_plan_artifact_for_study_owner(
        session: Session, study_id: int, owner_id: int
    ) -> Tuple[Optional[dict], str]:
        study = session.execute(select(Study).where(Study.id == study_id).limit(1)).scalar_one_or_none()
        if not study:
            return None, "not_found"

        actual_owner_id = session.execute(
            select(Project.owner_id).where(Project.id == study.project_id).limit(1)
        ).scalar_one_or_none()
        if actual_owner_id is None or int(actual_owner_id) != int(owner_id):
            return None, "forbidden"

        existing_plans = session.execute(
            select(Artifact).where(Artifact.study_id == study_id, Artifact.artifact_type == "plan")
        ).scalars().all()
        for existing in existing_plans:
            session.delete(existing)

        pending = Artifact(
            study_id=study_id,
            artifact_type="plan",
            content="",
            owner_id=int(actual_owner_id),
            status="pending",
        )
        session.add(pending)
        session.flush()
        session.refresh(pending)
        return (
            {
                "artifact_id": pending.id,
                "study_slug": study.slug or str(study_id),
                "project_id": study.project_id,
                "owner_id": int(actual_owner_id),
            },
            "created",
        )

    @staticmethod
    def complete_artifact(session: Session, artifact_id: int, content: str) -> bool:
        artifact = session.execute(
            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
        ).scalar_one_or_none()
        if not artifact:
            return False
        artifact.content = content
        artifact.status = "completed"
        return True

    @staticmethod
    def delete_artifact_by_id(session: Session, artifact_id: int) -> bool:
        artifact = session.execute(
            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
        ).scalar_one_or_none()
        if not artifact:
            return False
        session.delete(artifact)
        return True

    @staticmethod
    def group_studies_by_project(studies: Iterable[dict]) -> Dict[int, List[dict]]:
        grouped: Dict[int, List[dict]] = defaultdict(list)
        for study in studies:
            grouped[int(study["project_id"])].append(study)
        return grouped

    @staticmethod
    def group_artifacts_by_study(artifacts: Iterable[dict]) -> Dict[int, List[dict]]:
        grouped: Dict[int, List[dict]] = defaultdict(list)
        for artifact in artifacts:
            grouped[int(artifact["study_id"])].append(artifact)
        return grouped
