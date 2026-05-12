from types import SimpleNamespace

from flask import Flask


class FakeDevEvaluatorService:
    def __init__(self):
        self.status = "ok"

    def evaluate(self, **kwargs):
        if self.status == "invalid_artifact_id":
            return SimpleNamespace(status="invalid_artifact_id", data=None, error="artifact_id는 숫자여야 합니다.")
        if self.status == "evaluation_failed":
            return SimpleNamespace(status="evaluation_failed", data={"success": False, "error": "failed"}, error=None)
        return SimpleNamespace(status="ok", data={"success": True, "kwargs": kwargs}, error=None)


def _make_client(monkeypatch):
    import routes.dev_evaluator as module

    fake_service = FakeDevEvaluatorService()
    monkeypatch.setattr(module, "dev_evaluator_service", fake_service)
    monkeypatch.setenv("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(module.dev_evaluator_bp)
    return app.test_client(), fake_service


def test_dev_evaluator_controller_response_shape(monkeypatch):
    client, service = _make_client(monkeypatch)

    response = client.post(
        "/api/dev/evaluate",
        json={"artifact_type": "plan", "stage": "final", "payload": {"artifact_id": 1}, "criteria": []},
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["kwargs"]["artifact_type"] == "plan"

    service.status = "invalid_artifact_id"
    invalid = client.post(
        "/api/dev/evaluate",
        json={"artifact_type": "plan", "stage": "final", "payload": {"artifact_id": "bad"}, "criteria": []},
    )
    assert invalid.status_code == 400
    assert invalid.get_json() == {"success": False, "error": "artifact_id는 숫자여야 합니다."}

    service.status = "evaluation_failed"
    failed = client.post("/api/dev/evaluate", json={"payload": {}, "criteria": []})
    assert failed.status_code == 400
    assert failed.get_json() == {"success": False, "error": "failed"}


def test_dev_evaluator_controller_validation_and_env(monkeypatch):
    client, _service = _make_client(monkeypatch)

    bad_payload = client.post("/api/dev/evaluate", json={"payload": [], "criteria": []})
    assert bad_payload.status_code == 400
    assert bad_payload.get_json() == {"success": False, "error": "payload는 객체여야 합니다."}

    bad_criteria = client.post("/api/dev/evaluate", json={"payload": {}, "criteria": {}})
    assert bad_criteria.status_code == 400
    assert bad_criteria.get_json() == {"success": False, "error": "criteria는 배열이어야 합니다."}

    monkeypatch.setenv("FLASK_ENV", "production")
    blocked = client.post("/api/dev/evaluate", json={})
    assert blocked.status_code == 404
    assert blocked.get_json() == {"success": False, "error": "개발 환경에서만 사용 가능합니다."}
