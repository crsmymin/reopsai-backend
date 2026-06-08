from types import SimpleNamespace

from config import Config
from reopsai.infrastructure.persona_storage import PersonaStorage


class FakeBody:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "ContentType": kwargs.get("ContentType"),
        }

    def get_object(self, *, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        return {"Body": FakeBody(value["Body"]), "ContentType": value["ContentType"]}

    def head_object(self, *, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        return {"ContentLength": len(value["Body"])}


def _s3_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "PERSONA_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(Config, "PERSONA_S3_BUCKET", "persona-test")
    monkeypatch.setattr(Config, "PERSONA_S3_PREFIX", "assets")
    monkeypatch.setattr(Config, "PERSONA_S3_REGION", None)
    monkeypatch.setattr(Config, "PERSONA_S3_ENDPOINT_URL", None)
    monkeypatch.setattr(Config, "PERSONA_S3_LOCAL_FALLBACK", True)
    storage = PersonaStorage(local_dir=str(tmp_path))
    storage._s3_client = FakeS3Client()
    return storage


def test_save_bytes_uses_s3_when_enabled(monkeypatch, tmp_path):
    storage = _s3_storage(monkeypatch, tmp_path)

    saved = storage.save_bytes(
        b"image",
        company_id=7,
        filename="screen.png",
        mime_type="image/png",
        asset_type="test_source",
    )

    assert saved["storage_backend"] == "s3"
    assert storage.s3_object_size(saved["storage_key"]) == 5
    content = storage.read_asset_bytes(SimpleNamespace(**saved))
    assert content.bytes == b"image"
    assert content.mime_type == "image/png"
    assert content.source_backend == "s3"


def test_s3_asset_falls_back_to_local_copy(monkeypatch, tmp_path):
    storage = _s3_storage(monkeypatch, tmp_path)
    storage_key = "company-7/test_source/local-only.png"
    local_file = tmp_path / storage_key
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"local-image")

    content = storage.read_asset_bytes(
        SimpleNamespace(storage_backend="s3", storage_key=storage_key, mime_type="image/png")
    )

    assert content.bytes == b"local-image"
    assert content.source_backend == "local_fallback"
