from contextlib import contextmanager
from types import SimpleNamespace

from reopsai.application.plan_service import PlanService, parse_plan_date


@contextmanager
def fake_session_factory():
    yield SimpleNamespace()


class FakePlanRepository:
    owner_id = 10
    study_count = 0
    plan_count = 0
    deleted_artifacts = []
    deleted_studies = []
    completed = {}
    failed = {}

    @classmethod
    def reset(cls):
        cls.owner_id = 10
        cls.study_count = 0
        cls.plan_count = 0
        cls.deleted_artifacts = []
        cls.deleted_studies = []
        cls.completed = {}
        cls.failed = {}

    @classmethod
    def get_project_owner_id(cls, session, project_id):
        return cls.owner_id

    @staticmethod
    def list_owned_project_ids(session, user_id):
        return [1]

    @classmethod
    def count_studies_for_projects(cls, session, project_ids):
        return cls.study_count

    @classmethod
    def count_user_plan_artifacts(cls, session, user_id):
        return cls.plan_count

    @staticmethod
    def create_study(session, **kwargs):
        return SimpleNamespace(id=101, slug="study-slug", **kwargs)

    @staticmethod
    def create_plan_artifact(session, *, study_id, owner_id):
        return SimpleNamespace(id=202, study_id=study_id, owner_id=owner_id)

    @classmethod
    def complete_artifact(cls, session, *, artifact_id, content):
        cls.completed[int(artifact_id)] = content
        return SimpleNamespace(id=artifact_id)

    @classmethod
    def delete_artifact(cls, session, artifact_id):
        cls.deleted_artifacts.append(int(artifact_id))
        return SimpleNamespace(id=artifact_id)

    @classmethod
    def fail_artifact(cls, session, *, artifact_id, message):
        cls.failed[int(artifact_id)] = message
        return SimpleNamespace(id=artifact_id)

    @classmethod
    def delete_study(cls, session, study_id):
        cls.deleted_studies.append(int(study_id))
        return SimpleNamespace(id=study_id)


def make_service():
    FakePlanRepository.reset()
    return PlanService(
        repository=FakePlanRepository,
        session_factory=fake_session_factory,
        project_keyword_fetcher=lambda project_id: ["ux", str(project_id)],
    )


def test_parse_plan_date_and_oneshot_record_statuses():
    assert str(parse_plan_date("2026-05-12")) == "2026-05-12"
    assert parse_plan_date("bad") is None

    service = make_service()
    form_data = {"studyName": "Study", "problemDefinition": "Problem", "methodologies": ["UT"]}
    result = service.create_oneshot_records(project_id=1, user_id=10, tier="free", form_data=form_data)
    assert result.status == "ok"
    assert result.data == {
        "study_id": 101,
        "study_slug": "study-slug",
        "artifact_id": 202,
        "project_keywords": ["ux", "1"],
    }

    FakePlanRepository.owner_id = None
    assert service.create_oneshot_records(project_id=1, user_id=10, tier="free", form_data=form_data).status == "project_not_found"

    service = make_service()
    FakePlanRepository.owner_id = 99
    assert service.create_oneshot_records(project_id=1, user_id=10, tier="free", form_data=form_data).status == "forbidden"

    service = make_service()
    FakePlanRepository.study_count = 1
    assert service.create_oneshot_records(project_id=1, user_id=10, tier="free", form_data=form_data).status == "study_quota_exceeded"

    service = make_service()
    FakePlanRepository.plan_count = 1
    assert service.create_oneshot_records(project_id=1, user_id=10, tier="free", form_data=form_data).status == "plan_quota_exceeded"


def test_conversation_records_and_artifact_lifecycle():
    service = make_service()
    result = service.create_conversation_records(
        project_id=1,
        user_id=10,
        tier="basic",
        study_name="Conversation Study",
        ledger_text="ledger",
        selected_methods=["UT"],
        ledger_cards=[{"type": "methodology_set"}],
    )
    assert result.status == "ok"
    assert result.data["artifact_id"] == 202

    assert service.complete_artifact(artifact_id=202, content="plan").status == "ok"
    assert FakePlanRepository.completed == {202: "plan"}

    assert service.fail_artifact(artifact_id=202, message="boom").status == "ok"
    assert FakePlanRepository.failed == {202: "boom"}

    assert service.cleanup_created_records(study_id=101, artifact_id=202).status == "ok"
    assert FakePlanRepository.deleted_artifacts == [202]
    assert FakePlanRepository.deleted_studies == [101]
