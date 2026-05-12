from io import BytesIO
from pathlib import Path

from reopsai_backend.application.generator_service import GeneratorService


class FakeGemini:
    def analyze_image_with_vision(self, *, image_data, mime_type, prompt):
        return "vision result"

    def generate_text(self, prompt, generation_config=None):
        return "summary"


class FakeUpload:
    def __init__(self, filename, content_type, content):
        self.filename = filename
        self.content_type = content_type
        self._buffer = BytesIO(content)

    def seek(self, *args):
        return self._buffer.seek(*args)

    def tell(self):
        return self._buffer.tell()

    def save(self, path):
        self._buffer.seek(0)
        Path(path).write_bytes(self._buffer.read())


def make_service(tmp_path, max_file_size=1024):
    return GeneratorService(
        upload_folder=str(tmp_path),
        max_file_size=max_file_size,
        gemini_adapter=FakeGemini(),
    )


def test_generator_service_image_upload_and_owner_access(tmp_path):
    service = make_service(tmp_path)
    upload = FakeUpload("screen.png", "image/png", b"image-bytes")

    result = service.process_upload(file=upload, user_id="10")

    assert result.status == "ok"
    assert result.data["success"] is True
    assert result.data["file_name"] == "screen.png"
    assert result.data["processed_content"] == "vision result"
    assert result.data["pii"]["pii_redacted"] is False

    file_id = result.data["file_id"]
    assert service.authorize_file_download(filename=file_id, user_id="10", tier="free").status == "ok"
    assert service.authorize_file_download(filename=file_id, user_id="99", tier="free").status == "forbidden"
    assert service.authorize_file_download(filename=file_id, user_id="99", tier="admin").status == "ok"


def test_generator_service_validation_and_legacy_owner_policy(tmp_path):
    service = make_service(tmp_path, max_file_size=3)

    assert service.process_upload(file=FakeUpload("", "image/png", b"x"), user_id="10").status == "empty_filename"
    assert service.process_upload(file=FakeUpload("bad.exe", "application/octet-stream", b"x"), user_id="10").status == "unsupported"
    assert service.process_upload(file=FakeUpload("big.png", "image/png", b"1234"), user_id="10").status == "too_large"

    legacy_file = tmp_path / "legacy.png"
    legacy_file.write_bytes(b"legacy")
    assert service.authorize_file_download(filename="legacy.png", user_id="10", tier="free").status == "forbidden"
    assert service.authorize_file_download(filename="legacy.png", user_id="10", tier="super").status == "ok"
    assert service.authorize_file_download(filename="missing.png", user_id="10", tier="super").status == "not_found"
