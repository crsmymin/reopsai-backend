from contextlib import contextmanager
from types import SimpleNamespace

from reopsai.application.persona_service import PersonaService


@contextmanager
def fake_session_factory():
    session = SimpleNamespace(rollback=lambda: None)
    yield session


class DuplicateFolderRepository:
    @staticmethod
    def get_folder(session, *, company_id, folder_id):
        return SimpleNamespace(
            id=folder_id,
            company_id=company_id,
            created_by_user_id=10,
        )

    @staticmethod
    def can_modify_record(session, record, *, company_id, user_id):
        return True

    @staticmethod
    def folder_name_exists(session, *, company_id, name, exclude_folder_id=None):
        return name == "skt" and exclude_folder_id == 4

    @staticmethod
    def update_folder(session, folder, *, user_id, data):
        raise AssertionError("update_folder should not be called when the name is duplicated")


class FolderRepository(DuplicateFolderRepository):
    @staticmethod
    def folder_name_exists(session, *, company_id, name, exclude_folder_id=None):
        return False

    @staticmethod
    def update_folder(session, folder, *, user_id, data):
        folder.name = data["name"]
        folder.team_id = None
        folder.description = None
        folder.color = None
        folder.is_default = False
        folder.created_at = None
        folder.updated_at = None
        folder.created_by_user_id = user_id
        return folder


def test_update_folder_rejects_duplicate_company_folder_name():
    service = PersonaService(repository=DuplicateFolderRepository, session_factory=fake_session_factory)

    result = service.update_folder(
        company_id=5,
        user_id=10,
        folder_id=4,
        data={"name": " skt "},
    )

    assert result.status == "duplicate"
    assert result.status_code == 409
    assert result.error == "이미 존재하는 폴더명입니다."


def test_update_folder_allows_same_folder_name_when_no_duplicate():
    service = PersonaService(repository=FolderRepository, session_factory=fake_session_factory)

    result = service.update_folder(
        company_id=5,
        user_id=10,
        folder_id=4,
        data={"name": "skt"},
    )

    assert result.status == "ok"
    assert result.status_code == 200
    assert result.data["data"]["name"] == "skt"
