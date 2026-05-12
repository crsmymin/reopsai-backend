from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from db.repositories.study_repository import StudyRepository


@dataclass(frozen=True)
class StudyLookupResult:
    status: str
    data: Any = None
    error: str | None = None


def _iso(value):
    return value.isoformat() if value else None


def study_payload(study, owner_id):
    return {
        'id': study.id,
        'project_id': study.project_id,
        'name': study.name,
        'slug': study.slug,
        'initial_input': study.initial_input,
        'keywords': study.keywords,
        'methodologies': study.methodologies,
        'participant_count': study.participant_count,
        'start_date': _iso(study.start_date),
        'end_date': _iso(study.end_date),
        'timeline': study.timeline,
        'budget': study.budget,
        'target_audience': study.target_audience,
        'additional_requirements': study.additional_requirements,
        'created_at': _iso(study.created_at),
        'updated_at': _iso(study.updated_at),
        'projects': {'owner_id': owner_id},
    }


def project_payload(project):
    return {
        'id': project.id,
        'owner_id': project.owner_id,
        'name': project.name,
        'slug': project.slug,
        'product_url': project.product_url,
        'keywords': project.keywords,
        'created_at': _iso(project.created_at),
        'updated_at': _iso(project.updated_at),
    }


class StudyService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY):
        if repository is None:
            repository = StudyRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope

        self.repository = repository
        self.session_factory = session_factory

    def db_ready(self):
        return self.session_factory is not None

    @staticmethod
    def _allowed(owner_id, owner_ids: Sequence[int]) -> bool:
        allowed_owner_ids = {str(owner_id_) for owner_id_ in owner_ids if owner_id_ is not None}
        return owner_id is None or str(owner_id) in allowed_owner_ids

    def get_study_by_slug(self, *, slug: str, owner_ids: Sequence[int]) -> StudyLookupResult:
        if not self.db_ready():
            return StudyLookupResult("db_unavailable")
        with self.session_factory() as db_session:
            row = self.repository.get_study_with_owner_by_slug(db_session, slug)
        if not row:
            return StudyLookupResult("not_found")

        study, owner_id = row
        if not self._allowed(owner_id, owner_ids):
            return StudyLookupResult("forbidden")
        return StudyLookupResult("ok", study_payload(study, owner_id))

    def get_project_by_slug(self, *, slug: str, owner_ids: Sequence[int]) -> StudyLookupResult:
        if not self.db_ready():
            return StudyLookupResult("db_unavailable")
        with self.session_factory() as db_session:
            project = self.repository.get_project_by_slug(db_session, slug)
        if not project:
            return StudyLookupResult("not_found")
        if not self._allowed(project.owner_id, owner_ids):
            return StudyLookupResult("forbidden")
        return StudyLookupResult("ok", project_payload(project))


study_service = StudyService()
