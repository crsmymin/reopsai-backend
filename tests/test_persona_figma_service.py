from contextlib import contextmanager
from types import SimpleNamespace

from reopsai.infrastructure.persona_figma_client import PersonaFigmaClientError

from reopsai.application.persona_service import PersonaService


@contextmanager
def fake_session_factory():
    yield object()


class FakeRepository:
    account = SimpleNamespace(id=7, access_token_encrypted="encrypted-access-token")
    figma_file = SimpleNamespace(
        id=42,
        company_id=100,
        figma_account_id=7,
        figma_file_key="file-a",
        figma_file_name="File A",
        figma_file_link="https://figma.com/design/file-a/a",
        thumbnail_url="https://example.com/old.png",
        last_synced_at=None,
        sync_status="completed",
        sync_error=None,
    )
    figma_flows = [
        SimpleNamespace(
            id=3,
            figma_file_id=42,
            figma_page_id="1:1",
            figma_page_name="Page",
            figma_start_node_id="2:1",
            figma_flow_name="Main Flow",
            metadata_=None,
        )
    ]
    deleted_file = None
    upserted_files = []
    replaced_flows = []
    refresh_existing_on_upsert = False

    @staticmethod
    def get_figma_account(session, *, company_id, user_id):
        if company_id == 100 and user_id == 10:
            return FakeRepository.account
        return None

    @staticmethod
    def get_figma_file(session, *, company_id, file_id):
        if company_id == 100 and file_id == 42:
            return FakeRepository.figma_file
        return None

    @staticmethod
    def delete_figma_file(session, figma_file):
        FakeRepository.deleted_file = figma_file

    @staticmethod
    def get_figma_file_by_key(session, *, company_id, figma_file_key):
        return next(
            (
                row
                for row in FakeRepository.upserted_files
                if row.company_id == company_id and row.figma_file_key == figma_file_key
            ),
            None,
        )

    @staticmethod
    def upsert_figma_file(session, *, company_id, account_id, data):
        existing = next(
            (
                row
                for row in FakeRepository.upserted_files
                if row.company_id == company_id and row.figma_file_key == data["figma_file_key"]
            ),
            None,
        )
        if (
            existing is None
            and FakeRepository.refresh_existing_on_upsert
            and FakeRepository.figma_file.company_id == company_id
            and FakeRepository.figma_file.figma_file_key == data["figma_file_key"]
        ):
            existing = FakeRepository.figma_file
        if existing is not None:
            for key, value in data.items():
                setattr(existing, key, value)
            return existing
        figma_file = SimpleNamespace(
            id=len(FakeRepository.upserted_files) + 1,
            company_id=company_id,
            figma_account_id=account_id,
            figma_file_key=data["figma_file_key"],
            figma_file_name=data["figma_file_name"],
            figma_file_link=data.get("figma_file_link"),
            thumbnail_url=data.get("thumbnail_url"),
            last_synced_at=data.get("last_synced_at"),
            sync_status=data.get("sync_status"),
            sync_error=data.get("sync_error"),
        )
        FakeRepository.upserted_files.append(figma_file)
        return figma_file

    @staticmethod
    def replace_figma_flows(session, *, company_id, file_id, flows):
        FakeRepository.replaced_flows = list(flows)
        return [
            SimpleNamespace(
                id=index + 1,
                figma_file_id=file_id,
                figma_page_id=flow.get("figma_page_id"),
                figma_page_name=flow.get("figma_page_name"),
                figma_start_node_id=flow.get("figma_start_node_id"),
                figma_flow_name=flow.get("figma_flow_name"),
                metadata_=flow.get("metadata"),
            )
            for index, flow in enumerate(FakeRepository.replaced_flows)
        ]

    @staticmethod
    def list_figma_flows(session, *, company_id, file_id):
        if company_id == 100 and file_id == 42:
            return FakeRepository.figma_flows
        return []

    @staticmethod
    def create_asset(session, *, company_id, user_id, data):
        return SimpleNamespace(id=77, company_id=company_id, created_by_user_id=user_id, **data)


class FakeFigmaClient:
    payloads = {
        "file-a": {
            "figma_file_name": "File A from Figma",
            "thumbnail_url": "https://example.com/a.png",
            "flows": [{"figma_page_id": "1:1", "figma_page_name": "Page", "figma_start_node_id": "2:1", "figma_flow_name": "Main Flow"}],
        },
        "file-b": {
            "figma_file_name": "File B from Figma",
            "thumbnail_url": "https://example.com/b.png",
            "flows": [{"figma_page_id": "1:2", "figma_page_name": "Page", "figma_start_node_id": "2:2", "figma_flow_name": "Sub Flow"}],
        },
        "no-flow": {"figma_file_name": "No Flow", "thumbnail_url": None, "flows": []},
    }

    @staticmethod
    def decrypt(value):
        return "access-token" if value else None

    @staticmethod
    def fetch_file_with_flows(*, file_key, access_token):
        if file_key == "forbidden":
            raise PersonaFigmaClientError("figma_permission", "해당 링크의 파일 권한을 확인해주세요", 403)
        return FakeFigmaClient.payloads[file_key]

    @staticmethod
    def fetch_flow_preview(*, file_key, start_node_id, access_token):
        return {
            "startScreenId": f"screen_{start_node_id}",
            "screens": [
                {
                    "id": f"screen_{start_node_id}",
                    "name": "Start",
                    "figmaNodeId": start_node_id,
                    "imageUrl": "https://figma.example.com/start.png",
                    "width": 390,
                    "height": 844,
                    "viewport": "mobile",
                    "order": 0,
                },
                {
                    "id": "screen_2:2",
                    "name": "Next",
                    "figmaNodeId": "2:2",
                    "imageUrl": "https://figma.example.com/next.png",
                    "width": 390,
                    "height": 844,
                    "viewport": "mobile",
                    "order": 1,
                },
            ],
            "transitions": [{"id": "trans_1", "fromScreenId": f"screen_{start_node_id}", "toScreenId": "screen_2:2"}],
        }

    @staticmethod
    def download_image(image_url):
        return b"figma-image", "image/png"


class FakeStorage:
    @staticmethod
    def save_bytes(image_bytes, *, company_id, filename, mime_type, asset_type="generated_image"):
        return {
            "asset_type": asset_type,
            "storage_backend": "local",
            "storage_key": f"company-100/figma/{filename}",
            "original_filename": filename,
            "mime_type": mime_type,
            "byte_size": len(image_bytes),
        }


def test_sync_figma_file_allows_sequential_file_connections():
    FakeRepository.upserted_files = []
    FakeRepository.replaced_flows = []
    FakeRepository.refresh_existing_on_upsert = False
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, figma_client=FakeFigmaClient)

    first = service.sync_figma_file(
        company_id=100,
        user_id=10,
        data={"figma_file_key": "file-a", "figma_file_name": "File A", "figma_file_link": "https://figma.com/design/file-a/a"},
    )
    second = service.sync_figma_file(
        company_id=100,
        user_id=10,
        data={"figma_file_key": "file-b", "figma_file_name": "File B", "figma_file_link": "https://figma.com/design/file-b/b"},
    )

    assert first.status == "ok"
    assert second.status == "ok"
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.data["data"]["figma_file_key"] == "file-a"
    assert second.data["data"]["figma_file_key"] == "file-b"
    assert first.data["data"]["figma_file_name"] == "File A from Figma"
    assert first.data["data"]["flows"][0]["figma_flow_name"] == "Main Flow"
    assert [row.figma_file_key for row in FakeRepository.upserted_files] == ["file-a", "file-b"]


def test_sync_figma_file_rejects_duplicate_file_key():
    FakeRepository.upserted_files = [
        SimpleNamespace(id=1, company_id=100, figma_file_key="file-a")
    ]
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, figma_client=FakeFigmaClient)

    result = service.sync_figma_file(
        company_id=100,
        user_id=10,
        data={"figma_file_key": "file-a", "figma_file_name": "File A", "figma_file_link": "https://figma.com/design/file-a/a"},
    )

    assert result.status == "duplicate"
    assert result.status_code == 409
    assert result.error == "이미 추가된 URL 주소입니다."


def test_sync_figma_file_requires_prototype_flow():
    FakeRepository.upserted_files = []
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, figma_client=FakeFigmaClient)

    result = service.sync_figma_file(
        company_id=100,
        user_id=10,
        data={"figma_file_key": "no-flow", "figma_file_name": "No Flow", "figma_file_link": "https://figma.com/design/no-flow/a"},
    )

    assert result.status == "missing_flow"
    assert result.status_code == 400
    assert result.error == "파일 내 프로토 타입 Flow가 연결되어 있는지 확인해주세요."


def test_refresh_figma_file_refetches_file_and_replaces_flows():
    FakeRepository.upserted_files = []
    FakeRepository.replaced_flows = []
    FakeRepository.refresh_existing_on_upsert = True
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, figma_client=FakeFigmaClient)

    result = service.refresh_figma_file(company_id=100, user_id=10, file_id=42)

    assert result.status == "ok"
    refreshed = result.data["data"]
    assert refreshed["id"] == 42
    assert refreshed["figma_file_name"] == "File A from Figma"
    assert refreshed["thumbnail_url"] == "https://example.com/a.png"
    assert refreshed["last_synced_at"]
    assert refreshed["flows"][0]["figma_flow_name"] == "Main Flow"
    assert FakeRepository.replaced_flows[0]["figma_start_node_id"] == "2:1"
    FakeRepository.refresh_existing_on_upsert = False


def test_preview_figma_flow_returns_storage_backed_screen_images():
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, figma_client=FakeFigmaClient, storage=FakeStorage())

    result = service.preview_figma_flow(company_id=100, user_id=10, file_id=42, flow_id=3)

    assert result.status == "ok"
    preview = result.data["data"]
    assert preview["flowName"] == "Main Flow"
    assert preview["screenCount"] == 2
    assert preview["transitionCount"] == 1
    assert preview["screens"][0]["imageUrl"] == "/api/persona/storage/77"
    assert preview["screens"][0]["remoteImageUrl"] == "https://figma.example.com/start.png"


def test_delete_figma_file_deletes_company_scoped_file():
    FakeRepository.deleted_file = None
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory)

    result = service.delete_figma_file(company_id=100, file_id=42)

    assert result.status == "ok"
    assert result.status_code == 200
    assert FakeRepository.deleted_file is FakeRepository.figma_file


def test_delete_figma_file_returns_not_found_for_missing_file():
    FakeRepository.deleted_file = None
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory)

    result = service.delete_figma_file(company_id=100, file_id=99)

    assert result.status == "not_found"
    assert result.status_code == 404
    assert result.error == "figma file not found"
    assert FakeRepository.deleted_file is None
