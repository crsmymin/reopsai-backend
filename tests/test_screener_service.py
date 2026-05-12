from contextlib import contextmanager
import sys
import types

from reopsai.application.screener_service import ScreenerService

fake_pandas = types.ModuleType("pandas")
fake_pandas.Index = tuple
sys.modules.setdefault("pandas", fake_pandas)


@contextmanager
def fake_session_factory():
    yield object()


class FakeScreenerRepository:
    saved = None

    @classmethod
    def upsert_study_schedule(cls, session, *, study_id, final_participants, saved_at):
        cls.saved = {
            "study_id": study_id,
            "final_participants": final_participants,
            "saved_at": saved_at,
        }
        return {
            "id": 1,
            "study_id": study_id,
            "final_participants": final_participants,
            "saved_at": saved_at.isoformat(),
            "updated_at": None,
        }


class FakeOpenAi:
    def generate_response(self, prompt, generation_config=None):
        return {"success": True, "content": '{"summary": "ok"}'}


def make_service():
    FakeScreenerRepository.saved = None
    return ScreenerService(
        repository=FakeScreenerRepository,
        session_factory=fake_session_factory,
        openai_adapter=FakeOpenAi(),
        gemini_adapter=object(),
    )


def test_screener_analyze_plan_response_shape():
    service = make_service()

    result = service.analyze_plan(plan_text="plan")

    assert result.status == "ok"
    assert result.data == {"success": True, "analysis": {"summary": "ok"}}


def test_screener_save_schedule_upserts_and_preserves_shape():
    service = make_service()

    result = service.save_schedule(
        data={
            "study_id": "10",
            "optimized_schedule": {
                "schedule_assignments": {
                    "2026-05-12": {
                        "weekday": "Tue",
                        "10:00": ["Alice"],
                    }
                }
            },
            "participants_data": [{"participant_id": "p1", "name": "Alice"}],
            "schedule_columns": ["slot"],
            "name_column": "name",
            "validation_data": {"unassigned_count": 1, "missing_count": 0},
        }
    )

    assert result.status == "ok"
    assert result.data["success"] is True
    assert result.data["saved_participants_count"] == 1
    assert result.data["assigned_count"] == 1
    assert result.data["unassigned_count"] == 0
    assert result.data["saved_record"]["study_id"] == 10
    assert FakeScreenerRepository.saved["study_id"] == 10


def test_screener_save_schedule_invalid_and_db_unavailable():
    service = make_service()
    assert service.save_schedule(data={"study_id": "bad"}).status == "invalid_study_id"

    unavailable = ScreenerService(
        repository=FakeScreenerRepository,
        session_factory=None,
        openai_adapter=FakeOpenAi(),
        gemini_adapter=object(),
    )
    assert unavailable.save_schedule(data={"study_id": 10}).status == "db_unavailable"
