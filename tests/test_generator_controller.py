from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeGeneratorService:
    def __init__(self):
        self.upload_status = "ok"
        self.download_status = "not_found"
        self.upload_folder = "/tmp"

    def process_upload(self, *, file, user_id):
        if self.upload_status == "ok":
            return SimpleNamespace(
                status="ok",
                data={
                    "success": True,
                    "file_id": "file.png",
                    "file_name": file.filename,
                    "processed_content": "content",
                    "pii": {},
                },
                error=None,
            )
        return SimpleNamespace(status=self.upload_status, data=None, error="upload error")

    def authorize_file_download(self, *, filename, user_id, tier):
        if self.download_status == "ok":
            return SimpleNamespace(status="ok", data={"safe_filename": filename}, error=None)
        if self.download_status == "forbidden":
            return SimpleNamespace(status="forbidden", data=None, error="권한이 없습니다.")
        return SimpleNamespace(status="not_found", data=None, error="파일을 찾을 수 없습니다.")


def _make_generator_client(monkeypatch):
    import reopsai_backend.api.generator as module

    fake_service = FakeGeneratorService()
    monkeypatch.setattr(module, "generator_service", fake_service)

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(module.generator_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}", "X-User-ID": "10"}, fake_service


def test_generator_upload_controller_response_shape(monkeypatch):
    client, headers, service = _make_generator_client(monkeypatch)

    missing = client.post("/api/generator/upload-file", headers=headers)
    assert missing.status_code == 400
    assert missing.get_json() == {"error": "파일이 없습니다."}

    response = client.post(
        "/api/generator/upload-file",
        headers=headers,
        data={"file": (__import__("io").BytesIO(b"data"), "screen.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["file_id"] == "file.png"

    service.upload_status = "unsupported"
    bad = client.post(
        "/api/generator/upload-file",
        headers=headers,
        data={"file": (__import__("io").BytesIO(b"data"), "bad.exe")},
        content_type="multipart/form-data",
    )
    assert bad.status_code == 400
    assert bad.get_json() == {"error": "upload error"}


def test_generator_download_controller_not_found_and_forbidden(monkeypatch):
    client, headers, service = _make_generator_client(monkeypatch)

    missing = client.get("/api/generator/file/missing.png", headers=headers)
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "파일을 찾을 수 없습니다."}

    service.download_status = "forbidden"
    forbidden = client.get("/api/generator/file/file.png", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.get_json() == {"error": "권한이 없습니다."}
