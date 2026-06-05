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


class DefaultFolderRepository(FolderRepository):
    @staticmethod
    def get_folder(session, *, company_id, folder_id):
        return SimpleNamespace(
            id=folder_id,
            company_id=company_id,
            created_by_user_id=10,
            is_default=True,
        )

    @staticmethod
    def soft_delete_folder(session, folder, *, user_id):
        raise AssertionError("soft_delete_folder should not be called for default folders")


class DefaultSourcePersonaRepository:
    @staticmethod
    def get_persona(session, *, company_id, persona_id):
        return SimpleNamespace(
            id=persona_id,
            company_id=company_id,
            folder_id=4,
            created_by_user_id=10,
        )

    @staticmethod
    def can_modify_record(session, record, *, company_id, user_id):
        return True

    @staticmethod
    def get_folder(session, *, company_id, folder_id):
        return SimpleNamespace(
            id=folder_id,
            company_id=company_id,
            is_default=True,
        )

    @staticmethod
    def update_persona(session, persona, *, user_id, data):
        raise AssertionError("update_persona should not be called when moving out of a default folder")


class DefaultTargetPersonaRepository(DefaultSourcePersonaRepository):
    @staticmethod
    def get_persona(session, *, company_id, persona_id):
        return SimpleNamespace(
            id=persona_id,
            company_id=company_id,
            folder_id=None,
            created_by_user_id=10,
        )

    @staticmethod
    def get_visible_folder(session, *, company_id, user_id, folder_id):
        return SimpleNamespace(
            id=folder_id,
            company_id=company_id,
            created_by_user_id=None,
            is_default=True,
        )

    @staticmethod
    def update_persona(session, persona, *, user_id, data):
        raise AssertionError("update_persona should not be called when moving into a default folder")


class ListFoldersRepository:
    @staticmethod
    def list_folders(session, *, company_id, user_id):
        return [
            SimpleNamespace(
                id=1,
                company_id=company_id,
                team_id=None,
                name="기본 폴더",
                description=None,
                color=None,
                is_default=True,
                created_by_user_id=None,
                created_at=None,
                updated_at=None,
            ),
            SimpleNamespace(
                id=2,
                company_id=company_id,
                team_id=None,
                name="커스텀 폴더",
                description=None,
                color=None,
                is_default=False,
                created_by_user_id=user_id,
                created_at=None,
                updated_at=None,
            ),
        ]

    @staticmethod
    def count_visible_personas_by_folder_ids(session, *, company_id, user_id, folder_ids):
        return {1: 4, 2: 7}


def test_list_folders_includes_persona_counts_for_all_folder_types():
    service = PersonaService(repository=ListFoldersRepository, session_factory=fake_session_factory)

    result = service.list_folders(company_id=5, user_id=10)

    assert result.status == "ok"
    assert result.data["data"][0]["is_default"] is True
    assert result.data["data"][0]["persona_count"] == 4
    assert result.data["data"][0]["personaCount"] == 4
    assert result.data["data"][1]["is_default"] is False
    assert result.data["data"][1]["persona_count"] == 7


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


def test_delete_folder_rejects_default_folder():
    service = PersonaService(repository=DefaultFolderRepository, session_factory=fake_session_factory)

    result = service.delete_folder(company_id=5, user_id=10, folder_id=4)

    assert result.status == "forbidden"
    assert result.status_code == 403
    assert result.error == "default folder cannot be deleted"


def test_update_persona_rejects_moving_out_of_default_folder():
    service = PersonaService(repository=DefaultSourcePersonaRepository, session_factory=fake_session_factory)

    result = service.update_persona(
        company_id=5,
        user_id=10,
        persona_id=20,
        data={"folder_id": None},
    )

    assert result.status == "forbidden"
    assert result.status_code == 403
    assert result.error == "default folder personas cannot be moved"


def test_update_persona_rejects_moving_into_default_folder():
    service = PersonaService(repository=DefaultTargetPersonaRepository, session_factory=fake_session_factory)

    result = service.update_persona(
        company_id=5,
        user_id=10,
        persona_id=20,
        data={"folder_id": 4},
    )

    assert result.status == "forbidden"
    assert result.status_code == 403
    assert result.error == "personas cannot be moved into default folders"
