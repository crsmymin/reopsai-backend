from contextlib import contextmanager

from reopsai_backend.application.workspace_service import (
    WorkspaceService,
    build_project_update_data,
    build_study_update_data,
    normalize_tags,
)


class FakeRepository:
    @staticmethod
    def get_projects_by_owner_ids(session, owner_ids):
        assert owner_ids == [10]
        return [{"id": 1, "name": "Project", "created_at": "2026-01-03"}]

    @staticmethod
    def get_studies_by_project_ids(session, project_ids):
        assert project_ids == [1]
        return [{"id": 2, "project_id": 1, "name": "Study", "created_at": "2026-01-02"}]

    @staticmethod
    def get_artifacts_by_study_ids(session, study_ids):
        assert study_ids == [2]
        return [
            {
                "id": 3,
                "study_id": 2,
                "artifact_type": "plan",
                "created_at": "2026-01-01",
            }
        ]

    @staticmethod
    def group_studies_by_project(studies):
        return {1: list(studies)}

    @staticmethod
    def group_artifacts_by_study(artifacts):
        return {2: list(artifacts)}


@contextmanager
def fake_session_factory():
    yield object()


def test_workspace_summary_preserves_response_shape():
    service = WorkspaceService(repository=FakeRepository, session_factory=fake_session_factory)

    summary = service.get_workspace_summary([10])

    assert len(summary.projects) == 1
    assert summary.projects[0]["studies"][0]["artifacts"][0]["id"] == 3
    assert summary.all_studies[0]["name"] == "Study"
    assert summary.recent_artifacts == [
        {
            "id": 3,
            "study_id": 2,
            "artifact_type": "plan",
            "created_at": "2026-01-01",
            "study_name": "Study",
            "study_slug": 2,
        }
    ]


class FakeCrudRepository(FakeRepository):
    @staticmethod
    def create_project(session, *, owner_id, name, product_url, keywords):
        return {
            "id": 11,
            "owner_id": owner_id,
            "name": name,
            "product_url": product_url,
            "keywords": keywords,
        }

    @staticmethod
    def update_project_for_owner(session, project_id, owner_id, update_data):
        if project_id == 404:
            return None
        return {"id": project_id, "owner_id": owner_id, **update_data}

    @staticmethod
    def delete_project_for_owner(session, project_id, owner_id):
        return True

    @staticmethod
    def get_project_for_owner_ids(session, project_id, owner_ids):
        if project_id == 404:
            return None, False
        if project_id == 403:
            return {"id": project_id, "owner_id": 99}, False
        return {"id": project_id, "owner_id": owner_ids[0], "name": "Project"}, True

    @staticmethod
    def get_project_owner_id(session, project_id):
        if project_id == 404:
            return None
        if project_id == 403:
            return 99
        return 10

    @staticmethod
    def get_studies_by_project_id(session, project_id):
        return [{"id": 20, "project_id": project_id}]

    @staticmethod
    def get_study_for_owner_ids(session, study_id, owner_ids):
        if study_id == 404:
            return None, False
        if study_id == 403:
            return {"id": study_id, "project_id": 99}, False
        return {"id": study_id, "project_id": 1, "name": "Study"}, True

    @staticmethod
    def get_latest_schedule_by_study_id(session, study_id):
        return {"study_id": study_id, "final_participants": []}

    @staticmethod
    def update_artifact_content_for_owner(session, artifact_id, owner_id, content):
        return artifact_id != 404

    @staticmethod
    def get_artifacts_by_study_id(session, study_id):
        return [{"id": 30, "study_id": study_id}]

    @staticmethod
    def get_artifact_by_id_for_owner(session, artifact_id, owner_id):
        if artifact_id == 404:
            return None
        return {"id": artifact_id, "owner_id": owner_id, "status": "completed", "content": "done"}

    @staticmethod
    def get_artifact_by_id(session, artifact_id):
        return {"id": artifact_id, "status": "pending", "content": ""}

    @staticmethod
    def delete_study_for_owner(session, study_id, owner_id):
        if study_id == 404:
            return False, "not_found"
        if study_id == 403:
            return False, "forbidden"
        return True, "deleted"

    @staticmethod
    def delete_artifact_for_owner(session, artifact_id, owner_id):
        return artifact_id != 404

    @staticmethod
    def get_study_by_id_with_owner(session, study_id):
        if study_id == 404:
            return None
        if study_id == 403:
            return {"id": study_id}, 99
        return {"id": study_id}, 10

    @staticmethod
    def update_study_for_owner(session, study_id, owner_id, update_data):
        return {"id": study_id, "owner_id": owner_id, **update_data}

    @staticmethod
    def replace_plan_artifact_for_study_owner(session, study_id, owner_id):
        if study_id == 404:
            return None, "not_found"
        if study_id == 403:
            return None, "forbidden"
        return {"artifact_id": 55, "study_slug": "slug", "project_id": 1}, "created"

    @staticmethod
    def complete_artifact(session, artifact_id, content):
        return artifact_id != 404

    @staticmethod
    def delete_artifact_by_id(session, artifact_id):
        return artifact_id != 404


def test_workspace_input_normalization():
    assert normalize_tags(["a", "b"]) == ["a", "b"]
    assert normalize_tags("a") == ["a"]
    assert normalize_tags({"bad": "shape"}) == []
    assert build_project_update_data({"name": "P", "productUrl": "https://x", "tags": "ux"}) == {
        "name": "P",
        "product_url": "https://x",
        "keywords": ["ux"],
    }
    assert build_project_update_data({}) == {}
    assert build_study_update_data({"name": "S", "ignored": True}) == {"name": "S"}


def test_project_crud_service_results():
    service = WorkspaceService(repository=FakeCrudRepository, session_factory=fake_session_factory)

    created = service.create_project(owner_id=10, name="Project", product_url="", tags="ux")
    assert created["keywords"] == ["ux"]

    updated = service.update_project(project_id=1, owner_id=10, data={"tags": "finance"})
    assert updated.status == "ok"
    assert updated.data["keywords"] == ["finance"]

    assert service.update_project(project_id=1, owner_id=10, data={}).status == "empty_update"
    assert service.update_project(project_id=404, owner_id=10, data={"name": "Missing"}).status == "not_found"
    assert service.delete_project(project_id=1, owner_id=10) is True


def test_owner_access_and_listing_service_results():
    service = WorkspaceService(repository=FakeCrudRepository, session_factory=fake_session_factory)

    assert service.get_project(project_id=1, owner_ids=[10]).status == "ok"
    assert service.get_project(project_id=404, owner_ids=[10]).status == "not_found"
    assert service.get_project(project_id=403, owner_ids=[10]).status == "forbidden"

    studies = service.list_project_studies(project_id=1, owner_ids=[10])
    assert studies.status == "ok"
    assert studies.data == [{"id": 20, "project_id": 1}]
    assert service.list_project_studies(project_id=404, owner_ids=[10]).status == "not_found"
    assert service.list_project_studies(project_id=403, owner_ids=[10]).status == "forbidden"


def test_study_artifact_and_regeneration_service_results():
    service = WorkspaceService(repository=FakeCrudRepository, session_factory=fake_session_factory)

    assert service.get_study(study_id=1, owner_ids=[10]).status == "ok"
    assert service.get_study(study_id=404, owner_ids=[10]).status == "not_found"
    assert service.get_study(study_id=403, owner_ids=[10]).status == "forbidden"
    assert service.get_study_schedule(study_id=1, owner_ids=[10]).data["study_id"] == 1
    assert service.list_study_artifacts(study_id=1, owner_ids=[10]).data == [{"id": 30, "study_id": 1}]

    assert service.update_artifact_content(artifact_id=1, owner_id=10, content="body") is True
    assert service.update_artifact_content(artifact_id=404, owner_id=10, content="body") is False
    assert service.delete_study(study_id=1, owner_id=10).status == "ok"
    assert service.delete_study(study_id=404, owner_id=10).status == "not_found"
    assert service.delete_study(study_id=403, owner_id=10).status == "forbidden"
    assert service.delete_artifact(artifact_id=1, owner_id=10) is True
    assert service.delete_artifact(artifact_id=404, owner_id=10) is False

    assert service.update_study(study_id=1, owner_id=10, data={}).status == "empty_update"
    assert service.update_study(study_id=1, owner_id=10, data={"name": "Updated"}).data["name"] == "Updated"
    assert service.update_study(study_id=404, owner_id=10, data={"name": "Missing"}).status == "not_found"
    assert service.update_study(study_id=403, owner_id=10, data={"name": "Nope"}).status == "forbidden"

    prepared = service.prepare_plan_regeneration(study_id=1, owner_id=10)
    assert prepared.status == "ok"
    assert prepared.data["artifact_id"] == 55
    assert service.prepare_plan_regeneration(study_id=404, owner_id=10).status == "not_found"
    assert service.prepare_plan_regeneration(study_id=403, owner_id=10).status == "forbidden"
    assert service.complete_artifact(artifact_id=1, content="done") is True
    assert service.delete_artifact_by_id(artifact_id=1) is True
