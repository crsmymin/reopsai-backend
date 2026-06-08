from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from reopsai.application.persona_service import PersonaService


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def fake_session_factory():
    yield object()


class FakeRepository:
    next_asset_id = 900
    created_assets = []

    @classmethod
    def create_asset(cls, session, *, company_id, user_id, data):
        cls.next_asset_id += 1
        asset = SimpleNamespace(id=cls.next_asset_id, company_id=company_id, created_by_user_id=user_id, **data)
        cls.created_assets.append(asset)
        return asset

    @staticmethod
    def create_ui_test(session, *, company_id, user_id, data):
        return SimpleNamespace(
            id=1,
            company_id=company_id,
            created_by_user_id=user_id,
            name=data["name"],
            description=data.get("description"),
            device_type=data.get("device_type") or "pc",
            validation_type=data.get("validation_type") or "single",
            scope_type=data.get("scope_type") or "screen",
            source_type=data["source_type"],
            status=data.get("status") or "draft",
            progress=0,
            error_message=None,
            persona_count=data.get("persona_count"),
            screen_count=int(data.get("screen_count") or 0),
            source_data=data.get("source_data"),
            summary=data.get("summary"),
            created_at=_now(),
            updated_at=_now(),
            started_at=None,
            completed_at=None,
        )

    @staticmethod
    def create_ab_test(session, *, company_id, user_id, data):
        return SimpleNamespace(
            id=2,
            company_id=company_id,
            created_by_user_id=user_id,
            name=data["name"],
            purpose=data.get("purpose"),
            service_context=data.get("service_context"),
            mode=data.get("mode") or "single",
            screens=data.get("screens"),
            transitions=data.get("transitions"),
            context_data=data.get("context_data"),
            summary=data.get("summary"),
            status=data.get("status") or "draft",
            progress=0,
            error_message=None,
            enable_consistency_validation=False,
            consistency_run_count=3,
            created_at=_now(),
            updated_at=_now(),
        )


class FakeStorage:
    @staticmethod
    def save_bytes(image_bytes, *, company_id, filename, mime_type, asset_type="generated_image"):
        return {
            "asset_type": asset_type,
            "storage_backend": "s3",
            "storage_key": f"company-{company_id}/{asset_type}/{filename}",
            "original_filename": filename,
            "mime_type": mime_type,
            "byte_size": len(image_bytes),
        }


def setup_function():
    FakeRepository.next_asset_id = 900
    FakeRepository.created_assets = []


def test_ui_test_inline_data_image_is_stored_as_asset_url():
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, storage=FakeStorage())

    result = service.create_ui_test(
        company_id=100,
        user_id=10,
        data={
            "name": "inline image test",
            "source_type": "image",
            "source_data": {
                "imageEntries": [
                    {"id": "screen-1", "imageUrl": "data:image/png;base64,aW1hZ2U="},
                ],
            },
        },
    )

    image_url = result.data["data"]["source_data"]["imageEntries"][0]["imageUrl"]
    assert image_url == "/api/persona/storage/901"
    assert not image_url.startswith("data:image")
    assert FakeRepository.created_assets[0].asset_type == "test_source_inline"


def test_ab_test_inline_data_image_is_stored_as_asset_url():
    service = PersonaService(repository=FakeRepository, session_factory=fake_session_factory, storage=FakeStorage())

    result = service.create_ab_test(
        company_id=100,
        user_id=10,
        data={
            "name": "inline ab image test",
            "screens": [{"key": "A", "imageUrl": "data:image/png;base64,aW1hZ2U="}],
            "context_data": {
                "source_data": {
                    "variants": {
                        "B": [{"imageUrl": "data:image/png;base64,aW1hZ2U="}],
                    },
                },
            },
        },
    )

    assert result.data["data"]["screens"][0]["imageUrl"] == "/api/persona/storage/901"
    assert result.data["data"]["context_data"]["source_data"]["variants"]["B"][0]["imageUrl"] == "/api/persona/storage/902"
    assert {asset.asset_type for asset in FakeRepository.created_assets} == {"ab_test_source_inline"}
