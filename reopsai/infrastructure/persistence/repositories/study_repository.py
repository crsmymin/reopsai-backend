from sqlalchemy import select

from reopsai.infrastructure.persistence.models.core import Project, Study


class StudyRepository:
    @staticmethod
    def get_study_with_owner_by_slug(session, slug):
        row = session.execute(
            select(Study, Project.owner_id)
            .join(Project, Project.id == Study.project_id)
            .where(Study.slug == slug)
            .limit(1)
        ).first()
        if not row and str(slug).isdigit():
            row = session.execute(
                select(Study, Project.owner_id)
                .join(Project, Project.id == Study.project_id)
                .where(Study.id == int(slug))
                .limit(1)
            ).first()
        return row

    @staticmethod
    def get_project_by_slug(session, slug):
        project = session.execute(
            select(Project).where(Project.slug == slug).limit(1)
        ).scalar_one_or_none()
        if project is None and str(slug).isdigit():
            project = session.execute(
                select(Project).where(Project.id == int(slug)).limit(1)
            ).scalar_one_or_none()
        return project

