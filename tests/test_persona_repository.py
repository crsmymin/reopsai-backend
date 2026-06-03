from types import SimpleNamespace

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
