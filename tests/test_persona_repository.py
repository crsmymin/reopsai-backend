from types import SimpleNamespace

from reopsai.infrastructure.persistence.models.persona import Persona
from reopsai.infrastructure.persistence.repositories.persona_repository import PersonaRepository


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, *args):
        return self

    def update(self, values):
        self.session.bulk_update_values = values
        for row in self.session.existing_rows:
            for key, value in values.items():
                setattr(row, key.key, value)


class FakeSession:
    def __init__(self, existing_rows):
        self.existing_rows = existing_rows
        self.added = []
        self.bulk_update_values = None
        self.flushed = False
        self.last_statement = None

    def query(self, model):
        return FakeQuery(self)

    def execute(self, statement):
        self.last_statement = statement
        return FakeScalarResult(self.existing_rows)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed = True


def test_replace_figma_flows_updates_existing_start_nodes_instead_of_reinserting():
    existing_flow = SimpleNamespace(
        company_id=5,
        figma_file_id=4,
        figma_page_id="old-page",
        figma_page_name="Old page",
        figma_start_node_id="45:12",
        figma_flow_name="Old flow",
        metadata_=None,
        active=True,
        updated_at=None,
    )
    session = FakeSession([existing_flow])

    rows = PersonaRepository.replace_figma_flows(
        session,
        company_id=5,
        file_id=4,
        flows=[
            {
                "figma_page_id": "45:8",
                "figma_page_name": "PC 스크롤 포함 이미지 2개-A안",
                "figma_start_node_id": "45:12",
                "figma_flow_name": "홈에서 상품서비스",
                "metadata": {"source": "figma_api"},
            },
            {
                "figma_page_id": "49:2",
                "figma_page_name": "신규 SKT",
                "figma_start_node_id": "49:197",
                "figma_flow_name": "Flow 1",
                "metadata": {"source": "figma_api"},
            },
        ],
    )

    assert session.flushed is True
    assert len(session.added) == 1
    assert rows[0] is existing_flow
    assert existing_flow.active is True
    assert existing_flow.figma_page_id == "45:8"
    assert existing_flow.figma_flow_name == "홈에서 상품서비스"
    assert existing_flow.metadata_ == {"source": "figma_api"}
    assert session.added[0].figma_start_node_id == "49:197"


def test_soft_delete_folder_also_soft_deletes_contained_personas():
    folder = SimpleNamespace(
        id=10,
        company_id=5,
        deleted_at=None,
        updated_by_user_id=None,
        updated_at=None,
    )
    persona = SimpleNamespace(
        id=1,
        company_id=5,
        folder_id=10,
        deleted_at=None,
        updated_by_user_id=None,
        updated_at=None,
    )
    session = FakeSession([persona])

    PersonaRepository.soft_delete_folder(session, folder, user_id=99)

    assert folder.deleted_at is not None
    assert folder.updated_by_user_id == 99
    assert persona.deleted_at is not None
    assert persona.updated_by_user_id == 99
    assert session.flushed is True
    assert Persona.deleted_at in session.bulk_update_values
    assert Persona.folder_id not in session.bulk_update_values


def test_folder_name_exists_only_checks_active_folders():
    session = FakeSession([])

    exists = PersonaRepository.folder_name_exists(
        session,
        company_id=5,
        name="skt",
        exclude_folder_id=4,
    )

    compiled = str(session.last_statement.compile(compile_kwargs={"literal_binds": True}))
    assert exists is False
    assert "persona_folders.deleted_at IS NULL" in compiled
    assert "persona_folders.id != 4" in compiled


def test_list_folders_filters_to_owned_or_default_folders():
    session = FakeSession([])

    PersonaRepository.list_folders(session, company_id=5, user_id=10)

    compiled = str(session.last_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "persona_folders.company_id = 5" in compiled
    assert "persona_folders.deleted_at IS NULL" in compiled
    assert "persona_folders.created_by_user_id = 10" in compiled
    assert "persona_folders.is_default IS true" in compiled


def test_visible_persona_filter_allows_owned_or_default_folder_personas():
    compiled = str(
        PersonaRepository._visible_persona_filter(company_id=5, user_id=10).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "personas.created_by_user_id = 10" in compiled
    assert "personas.folder_id IN" in compiled
    assert "persona_folders.company_id = 5" in compiled
    assert "persona_folders.is_default IS true" in compiled
    assert "persona_folders.deleted_at IS NULL" in compiled


def test_test_and_interview_lists_filter_to_creator_when_user_is_given():
    expectations = [
        (PersonaRepository.list_ui_tests, "persona_ui_tests.created_by_user_id = 10"),
        (PersonaRepository.list_ab_tests, "persona_ab_tests.created_by_user_id = 10"),
        (PersonaRepository.list_interviews, "persona_interviews.created_by_user_id = 10"),
    ]

    for list_method, expected_sql in expectations:
        session = FakeSession([])
        list_method(session, company_id=5, user_id=10)
        compiled = str(session.last_statement.compile(compile_kwargs={"literal_binds": True}))
        assert expected_sql in compiled
