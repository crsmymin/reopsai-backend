from contextlib import contextmanager
from importlib import import_module

from reopsai.application.dev_evaluator_service import DevEvaluatorService
from reopsai.infrastructure.dev_evaluator import _parse_llm_evaluation_response


@contextmanager
def fake_session_factory():
    yield object()


class FakeRepository:
    content = "stored plan"
    raise_error = False

    @classmethod
    def get_artifact_content(cls, session, artifact_id):
        if cls.raise_error:
            raise RuntimeError("db down")
        return cls.content


def make_service(evaluator=None):
    FakeRepository.content = "stored plan"
    FakeRepository.raise_error = False
    return DevEvaluatorService(
        repository=FakeRepository,
        session_factory=fake_session_factory,
        evaluator=evaluator or (lambda artifact_type, stage, payload, criteria, mode: {"success": True, "payload": payload}),
    )


def test_dev_evaluator_loads_artifact_content_for_plan_final():
    service = make_service()

    result = service.evaluate(
        artifact_type="plan",
        stage="final",
        payload={"artifact_id": "10"},
        criteria=[],
        evaluation_mode=None,
    )

    assert result.status == "ok"
    assert result.data["payload"]["content"] == "stored plan"


def test_dev_evaluator_invalid_artifact_id_and_db_fallback():
    service = make_service()

    invalid = service.evaluate(
        artifact_type="plan",
        stage="final",
        payload={"artifact_id": "bad"},
        criteria=[],
        evaluation_mode=None,
    )
    assert invalid.status == "invalid_artifact_id"

    FakeRepository.raise_error = True
    fallback = service.evaluate(
        artifact_type="plan",
        stage="final",
        payload={"artifact_id": "10", "content": "request content"},
        criteria=[],
        evaluation_mode=None,
    )
    assert fallback.status == "ok"
    assert fallback.data["payload"]["content"] == "request content"


def test_dev_evaluator_failed_evaluation_status():
    service = make_service(evaluator=lambda *args: {"success": False, "error": "failed"})

    result = service.evaluate(
        artifact_type="survey",
        stage="draft",
        payload={},
        criteria=[],
        evaluation_mode="strict",
    )

    assert result.status == "evaluation_failed"
    assert result.data == {"success": False, "error": "failed"}


def test_dev_evaluator_infrastructure_parser_and_legacy_wrapper():
    assert _parse_llm_evaluation_response('```json\n{"criteria": [{"id": "a", "score": 1}]}\n```') == {
        "criteria": [{"id": "a", "score": 1}]
    }

    legacy = import_module("services.dev_evaluator_service")
    assert legacy._parse_llm_evaluation_response is _parse_llm_evaluation_response
