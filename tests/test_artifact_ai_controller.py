from types import SimpleNamespace

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager, create_access_token


class FakeArtifactAiService:
    def list_edit_history(self, **kwargs):
        if kwargs["artifact_id"] == 404:
            return SimpleNamespace(status="not_found", data=None)
        if kwargs["artifact_id"] == 403:
            return SimpleNamespace(status="forbidden", data=None)
        return SimpleNamespace(status="ok", data=[{"id": "h1", "artifact_id": kwargs["artifact_id"]}])

    def create_edit_history(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            data={
                "id": "created",
                "artifact_id": kwargs["artifact_id"],
                "before_markdown": kwargs["before_markdown"],
                "after_markdown": kwargs["after_markdown"],
            },
        )

    def modify_artifact_text(self, **kwargs):
        selected = kwargs["selected_text"]
        if selected == "llm":
            return SimpleNamespace(status="llm_failed", error="AI 수정 실패: boom", data=None)
        if selected == "incomplete":
            return SimpleNamespace(status="incomplete_response", error="AI 응답이 완전하지 않습니다.", data=None)
        if selected == "partial":
            return SimpleNamespace(status="partial_response", error="AI가 문서 전체가 아니라 일부만 반환했습니다.", data=None)
        return SimpleNamespace(
            status="ok",
            data={"original": selected, "modified": "modified", "message": "AI 수정 제안을 생성했습니다."},
        )


def _make_artifact_ai_client(monkeypatch):
    import routes.artifact_ai as artifact_ai_module

    monkeypatch.setattr(artifact_ai_module, "artifact_ai_service", FakeArtifactAiService())
    monkeypatch.setattr(artifact_ai_module, "_resolve_workspace_owner_ids", lambda user_id: [user_id])

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)

    @app.route("/fake-error")
    def fake_error():
        return jsonify({"success": False, "error": "auth error"}), 401

    app.register_blueprint(artifact_ai_module.artifact_ai_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}", "X-User-ID": "10"}


def test_artifact_ai_history_controller_shapes(monkeypatch):
    client, headers = _make_artifact_ai_client(monkeypatch)

    listed = client.get("/api/artifacts/100/edit_history?limit=bad", headers=headers)
    assert listed.status_code == 200
    assert listed.get_json() == {"success": True, "history": [{"id": "h1", "artifact_id": 100}]}

    missing = client.get("/api/artifacts/404/edit_history", headers=headers)
    assert missing.status_code == 404
    assert missing.get_json() == {"success": False, "error": "아티팩트를 찾을 수 없습니다."}

    forbidden = client.get("/api/artifacts/403/edit_history", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.get_json() == {"success": False, "error": "접근 권한이 없습니다."}

    invalid = client.post("/api/artifacts/100/edit_history", headers=headers, json={"before_markdown": "", "after_markdown": ""})
    assert invalid.status_code == 400
    assert invalid.get_json() == {"success": False, "error": "before_markdown / after_markdown가 필요합니다."}

    created = client.post(
        "/api/artifacts/100/edit_history",
        headers=headers,
        json={"before_markdown": "before", "after_markdown": "after", "prompt": "prompt"},
    )
    assert created.status_code == 200
    assert created.get_json()["history"]["id"] == "created"


def test_artifact_ai_modify_controller_shapes(monkeypatch):
    client, headers = _make_artifact_ai_client(monkeypatch)

    missing_body = client.post("/api/artifacts/100/modify", headers=headers)
    assert missing_body.status_code == 400
    assert missing_body.get_json() == {"success": False, "error": "요청 데이터가 필요합니다."}

    missing_selected = client.post("/api/artifacts/100/modify", headers=headers, json={"user_prompt": "수정"})
    assert missing_selected.status_code == 400
    assert missing_selected.get_json() == {"success": False, "error": "selected_text가 필요합니다."}

    missing_prompt = client.post("/api/artifacts/100/modify", headers=headers, json={"selected_text": "text"})
    assert missing_prompt.status_code == 400
    assert missing_prompt.get_json() == {"success": False, "error": "user_prompt가 필요합니다."}

    modified = client.post(
        "/api/artifacts/100/modify",
        headers=headers,
        json={"selected_text": "text", "modification_prompt": "수정"},
    )
    assert modified.status_code == 200
    assert modified.get_json() == {
        "success": True,
        "original": "text",
        "modified": "modified",
        "message": "AI 수정 제안을 생성했습니다.",
    }

    llm = client.post("/api/artifacts/100/modify", headers=headers, json={"selected_text": "llm", "user_prompt": "수정"})
    assert llm.status_code == 500
    assert llm.get_json() == {"success": False, "error": "AI 수정 실패: boom"}

    incomplete = client.post("/api/artifacts/100/modify", headers=headers, json={"selected_text": "incomplete", "user_prompt": "수정"})
    assert incomplete.status_code == 500
    assert incomplete.get_json() == {"success": False, "error": "AI 응답이 완전하지 않습니다."}

    partial = client.post("/api/artifacts/100/modify", headers=headers, json={"selected_text": "partial", "user_prompt": "수정"})
    assert partial.status_code == 200
    assert partial.get_json() == {"success": False, "error": "AI가 문서 전체가 아니라 일부만 반환했습니다."}
