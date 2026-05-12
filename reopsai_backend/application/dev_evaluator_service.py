from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reopsai_backend.infrastructure.repositories import DevEvaluatorRepository


@dataclass(frozen=True)
class DevEvaluatorResult:
    status: str
    data: Any = None
    error: str | None = None


class DevEvaluatorService:
    _DEFAULT_SESSION_FACTORY = object()
    _DEFAULT_EVALUATOR = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY, evaluator=_DEFAULT_EVALUATOR):
        if repository is None:
            repository = DevEvaluatorRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        if evaluator is self._DEFAULT_EVALUATOR:
            evaluator = self._run_evaluation_lazy

        self.repository = repository
        self.session_factory = session_factory
        self.evaluator = evaluator

    @staticmethod
    def _run_evaluation_lazy(*args, **kwargs):
        from reopsai_backend.infrastructure.evaluation import run_evaluation

        return run_evaluation(*args, **kwargs)

    def db_ready(self):
        return self.session_factory is not None

    def evaluate(self, *, artifact_type, stage, payload, criteria, evaluation_mode) -> DevEvaluatorResult:
        if artifact_type == "plan" and stage == "final" and payload.get("artifact_id"):
            try:
                artifact_id = int(payload.get("artifact_id"))
            except (TypeError, ValueError):
                return DevEvaluatorResult("invalid_artifact_id", error="artifact_id는 숫자여야 합니다.")

            if self.db_ready():
                try:
                    with self.session_factory() as db_session:
                        content = self.repository.get_artifact_content(db_session, artifact_id)
                        if content is not None:
                            payload = {**payload, "content": content}
                except Exception:
                    # Development-only endpoint: preserve legacy behavior by evaluating original payload.
                    pass

        result = self.evaluator(artifact_type, stage, payload, criteria, evaluation_mode)
        if not result.get("success"):
            return DevEvaluatorResult("evaluation_failed", data=result)
        return DevEvaluatorResult("ok", data=result)


dev_evaluator_service = DevEvaluatorService()
