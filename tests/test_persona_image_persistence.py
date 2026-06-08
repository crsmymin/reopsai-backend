from types import SimpleNamespace

from reopsai.application.persona_service import PersonaService


class FakeRepository:
    @staticmethod
    def create_asset(session, *, company_id, user_id, data):
        return SimpleNamespace(id=123, company_id=company_id, created_by_user_id=user_id, **data)


class FakeStorage:
    @staticmethod
    def save_bytes(image_bytes, *, company_id, filename, mime_type, asset_type="generated_image"):
        return {
            "asset_type": asset_type,
            "storage_backend": "s3",
            "storage_key": "company-100/persona_image/persona.png",
            "original_filename": filename,
            "mime_type": mime_type,
            "byte_size": len(image_bytes),
        }


def test_persist_persona_image_does_not_store_image_blob_on_persona_row():
    service = PersonaService(repository=FakeRepository, storage=FakeStorage())

    persisted = service._persist_persona_image_if_needed(
        None,
        company_id=100,
        user_id=10,
        persona_data={
            "name": "김민수",
            "image_url": "data:image/png;base64,aW1hZ2U=",
        },
    )

    assert persisted["image_asset_id"] == 123
    assert persisted["image_url"] == "/api/persona/storage/123"
    assert persisted["image_data"] is None
    assert persisted["image_mime_type"] == "image/png"
