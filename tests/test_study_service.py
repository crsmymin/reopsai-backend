from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

from reopsai_backend.application.study_service import StudyService


@contextmanager
def fake_session_factory():
    yield object()


class FakeStudyRepository:
    study_owner_id = 10
    project_owner_id = 10

    @classmethod
    def get_study_with_owner_by_slug(cls, session, slug):
        if slug == "missing":
            return None
        study = SimpleNamespace(
            id=1,
            project_id=2,
            name="Study",
            slug=str(slug),
            initial_input="input",
            keywords=["ux"],
            methodologies=["UT"],
            participant_count=5,
            start_date=date(2026, 5, 1),
            end_date=None,
            timeline="2 weeks",
            budget="100",
            target_audience="users",
            additional_requirements="none",
            created_at=datetime(2026, 5, 1, 12, 0, 0),
            updated_at=None,
        )
        return study, cls.study_owner_id

    @classmethod
    def get_project_by_slug(cls, session, slug):
        if slug == "missing":
            return None
        return SimpleNamespace(
            id=2,
            owner_id=cls.project_owner_id,
            name="Project",
            slug=str(slug),
            product_url="https://example.com",
            keywords=["ux"],
            created_at=datetime(2026, 5, 1, 12, 0, 0),
            updated_at=None,
        )


def make_service():
    FakeStudyRepository.study_owner_id = 10
    FakeStudyRepository.project_owner_id = 10
    return StudyService(repository=FakeStudyRepository, session_factory=fake_session_factory)


def test_study_by_slug_payload_and_access_statuses():
    service = make_service()

    result = service.get_study_by_slug(slug="study-slug", owner_ids=[10])
    assert result.status == "ok"
    assert result.data["slug"] == "study-slug"
    assert result.data["start_date"] == "2026-05-01"
    assert result.data["projects"] == {"owner_id": 10}

    assert service.get_study_by_slug(slug="missing", owner_ids=[10]).status == "not_found"

    FakeStudyRepository.study_owner_id = 99
    assert service.get_study_by_slug(slug="study-slug", owner_ids=[10]).status == "forbidden"


def test_project_by_slug_payload_and_access_statuses():
    service = make_service()

    result = service.get_project_by_slug(slug="project-slug", owner_ids=[10])
    assert result.status == "ok"
    assert result.data == {
        "id": 2,
        "owner_id": 10,
        "name": "Project",
        "slug": "project-slug",
        "product_url": "https://example.com",
        "keywords": ["ux"],
        "created_at": "2026-05-01T12:00:00",
        "updated_at": None,
    }

    assert service.get_project_by_slug(slug="missing", owner_ids=[10]).status == "not_found"

    FakeStudyRepository.project_owner_id = 99
    assert service.get_project_by_slug(slug="project-slug", owner_ids=[10]).status == "forbidden"
