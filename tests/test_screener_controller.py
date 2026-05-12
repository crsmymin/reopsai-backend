from types import SimpleNamespace

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token


class FakeScreenerService:
    def analyze_plan(self, *, plan_text):
        return SimpleNamespace(status="ok", data={"success": True, "analysis": {"summary": "ok"}})

    def upload_csv(self, *, csv_content):
        return SimpleNamespace(status="ok", data={"success": True, "csv_info": {"rows": 1}})

    def find_optimal_participants_stream(self, **kwargs):
        yield 'data: {"step": 1}\n\n'

    def optimize_schedule(self, *, data):
        if data.get("empty"):
            return SimpleNamespace(status="no_availability", data=None, error="일정 정보가 있는 참여자를 찾을 수 없습니다.")
        return SimpleNamespace(
            status="ok",
            data={"success": True, "optimized_schedule": {}, "validation": {}},
            error=None,
        )

    def finalize_participants(self, *, data):
        return SimpleNamespace(
            status="ok",
            data={"success": True, "final_participants": [], "reserve_participants": []},
        )

    def save_schedule(self, *, data):
        if data.get("study_id") == "bad":
            return SimpleNamespace(status="invalid_study_id", data=None, error="study_id must be an integer")
        return SimpleNamespace(status="ok", data={"success": True, "saved_record": {"study_id": int(data["study_id"])}})


def _make_screener_client(monkeypatch):
    import reopsai.api.screener as module

    monkeypatch.setattr(module, "screener_service", FakeScreenerService())

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)
    app.register_blueprint(module.screener_bp)

    with app.app_context():
        token = create_access_token(identity="10", additional_claims={"tier": "free"})
    return app.test_client(), {"Authorization": f"Bearer {token}", "X-User-ID": "10"}


def test_screener_analyze_upload_and_stream_controller_shape(monkeypatch):
    client, headers = _make_screener_client(monkeypatch)

    missing_plan = client.post("/api/screener/analyze-plan", headers=headers, json={})
    assert missing_plan.status_code == 400
    assert missing_plan.get_json() == {"success": False, "error": "계획서 텍스트가 필요합니다."}

    plan = client.post("/api/screener/analyze-plan", headers=headers, json={"plan_text": "plan"})
    assert plan.status_code == 200
    assert plan.get_json() == {"success": True, "analysis": {"summary": "ok"}}

    upload = client.post("/api/screener/upload-csv", headers=headers, json={"csv_content": "a\n1"})
    assert upload.status_code == 200
    assert upload.get_json() == {"success": True, "csv_info": {"rows": 1}}

    missing_stream = client.post("/api/screener/find-optimal-participants", headers=headers, json={})
    assert missing_stream.status_code == 200
    assert missing_stream.data == b'data: {"error": "\\ud544\\uc218 \\ub370\\uc774\\ud130\\uac00 \\ub204\\ub77d\\ub418\\uc5c8\\uc2b5\\ub2c8\\ub2e4."}\n\n'

    stream = client.post(
        "/api/screener/find-optimal-participants",
        headers=headers,
        json={"csv_content": "a", "plan_json": {"target_groups": []}, "csv_info": {"columns": []}, "sincerity_rules": ["rule"]},
    )
    assert stream.status_code == 200
    assert stream.data == b'data: {"step": 1}\n\n'


def test_screener_schedule_finalize_and_save_controller_shape(monkeypatch):
    client, headers = _make_screener_client(monkeypatch)

    no_participants = client.post("/api/screener/optimize-schedule", headers=headers, json={})
    assert no_participants.status_code == 400
    assert no_participants.get_json() == {"success": False, "error": "participants_data is required"}

    optimized = client.post(
        "/api/screener/optimize-schedule",
        headers=headers,
        json={"participants_data": [{"name": "A"}], "schedule_columns": ["slot"]},
    )
    assert optimized.status_code == 200
    assert optimized.get_json() == {"success": True, "optimized_schedule": {}, "validation": {}}

    finalize_missing = client.post("/api/screener/finalize-participants", headers=headers, json={})
    assert finalize_missing.status_code == 400
    assert finalize_missing.get_json() == {"success": False, "error": "participants_data is required"}

    finalized = client.post(
        "/api/screener/finalize-participants",
        headers=headers,
        json={"participants_data": [{"participant_id": "p1"}]},
    )
    assert finalized.status_code == 200
    assert finalized.get_json()["success"] is True

    save_missing = client.post("/api/screener/save-schedule", headers=headers, json={})
    assert save_missing.status_code == 400
    assert save_missing.get_json() == {"success": False, "error": "study_id is required"}

    save_bad = client.post("/api/screener/save-schedule", headers=headers, json={"study_id": "bad"})
    assert save_bad.status_code == 400
    assert save_bad.get_json() == {"success": False, "error": "study_id must be an integer"}

    saved = client.post("/api/screener/save-schedule", headers=headers, json={"study_id": "10"})
    assert saved.status_code == 200
    assert saved.get_json() == {"success": True, "saved_record": {"study_id": 10}}
