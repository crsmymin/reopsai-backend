import sys
import types
from types import SimpleNamespace


def _install_plan_generation_import_fakes():
    fake_openai = types.ModuleType("services.openai_service")
    fake_openai.openai_service = SimpleNamespace(generate_response=lambda *args, **kwargs: {"success": True, "content": "{}"})
    sys.modules.setdefault("services.openai_service", fake_openai)

    fake_gemini = types.ModuleType("services.gemini_service")
    fake_gemini.gemini_service = SimpleNamespace(generate_response=lambda *args, **kwargs: {"success": True, "content": "plan"})
    sys.modules.setdefault("services.gemini_service", fake_gemini)

    fake_vector = types.ModuleType("services.vector_service")
    fake_vector.vector_service = SimpleNamespace(
        improved_service=SimpleNamespace(
            hybrid_search=lambda **kwargs: {"principles": "", "examples": ""},
            context_optimization=lambda text, max_length=1000: text,
        )
    )
    sys.modules.setdefault("services.vector_service", fake_vector)


_install_plan_generation_import_fakes()

from reopsai.application.plan_generation_service import PlanGenerationService


class FakeOpenAi:
    def generate_response(self, prompt, config):
        if "오직 JSON 하나만 출력" in prompt:
            return {
                "success": True,
                "content": """
                {
                  "draft_cards": [
                    {"type": "research_goal", "title": "목표", "content": "전환 이유 확인", "because": "필요"},
                    {"type": "question", "title": "왜 이탈하나요?", "content": "왜 이탈하나요?"}
                  ],
                  "message": "추천을 생성했습니다."
                }
                """,
            }
        return {"success": True, "content": "expert"}


class FakeGemini:
    def generate_response(self, prompt, config, model_name=None):
        return {"success": True, "content": "final plan"}


class FakeVectorService:
    def __init__(self):
        self.improved_service = SimpleNamespace(
            hybrid_search=lambda **kwargs: {"principles": "principles", "examples": "examples"},
            context_optimization=lambda text, max_length=1000: f"{text}:{max_length}",
        )


class FakeRecordService:
    def __init__(self):
        self.completed = {}
        self.deleted = []
        self.failed = {}

    def complete_artifact(self, *, artifact_id, content):
        self.completed[artifact_id] = content

    def delete_artifact(self, *, artifact_id):
        self.deleted.append(artifact_id)

    def fail_artifact(self, *, artifact_id, message):
        self.failed[artifact_id] = message


def make_generation_service(record_service=None, openai_adapter=None, gemini_adapter=None):
    return PlanGenerationService(
        openai_adapter=openai_adapter or FakeOpenAi(),
        gemini_adapter=gemini_adapter or FakeGemini(),
        vector_adapter=FakeVectorService(),
        contextual_keyword_extractor=lambda text: ["ux", "research"],
        project_keyword_fetcher=lambda project_id: ["checkout"],
        usage_context_getter=lambda: {},
        usage_runner=lambda context, func, *args, **kwargs: func(*args, **kwargs),
        record_service=record_service or FakeRecordService(),
    )


def test_plan_generation_helpers_and_streaming_shape():
    service = make_generation_service()
    ledger = [
        {"type": "methodology_set", "title": "방법", "content": "UT", "fields": {"methods": ["UT", "IDI", "UT"]}},
    ]

    assert "methodology_set" in service.ledger_cards_to_context_text(ledger)
    assert service.extract_selected_methodologies_from_ledger(ledger) == ["UT", "IDI"]

    chunks = list(service.stream_study_helper_chat(data={"message": "도와줘", "context": {}}))
    assert chunks[-1].startswith("data: ")
    assert '"done": true' in chunks[-1]


def test_conversation_recommendation_filters_cards_and_keeps_response_shape():
    service = make_generation_service()

    result = service.build_conversation_recommendation(
        data={
            "step": 1,
            "mode": "recommend",
            "conversation": [{"type": "user", "content": "전환율 문제를 보고 싶어요"}],
            "ledger_cards": [],
            "projectId": 1,
        }
    )

    assert result.status == "ok"
    assert result.data["success"] is True
    assert result.data["draft_cards"] == [
        {"type": "research_goal", "title": "목표", "content": "전환 이유 확인", "because": "필요"}
    ]
    assert result.data["next_question"]["title"] == "왜 이탈하나요?"
    assert result.data["missing_questions"] == []


def test_background_generation_updates_artifacts():
    record_service = FakeRecordService()
    service = make_generation_service(record_service=record_service)

    one_shot = service.generate_oneshot_plan_background(
        artifact_id=10,
        study_id=20,
        form_data={"studyName": "Study", "problemDefinition": "Problem", "methodologies": ["UT"]},
        project_keywords=["checkout"],
    )
    assert one_shot.status == "ok"
    assert record_service.completed[10] == "final plan"

    conversation = service.generate_conversation_plan_background(
        artifact_id=11,
        study_id=21,
        ledger_text="ledger",
        selected_methods=["UT"],
        project_keywords=["checkout"],
    )
    assert conversation.status == "ok"
    assert record_service.completed[11] == "final plan"


def test_background_generation_failure_fallbacks():
    class FailingGemini:
        def generate_response(self, prompt, config, model_name=None):
            return {"success": False, "error": "boom"}

    record_service = FakeRecordService()
    service = make_generation_service(record_service=record_service, gemini_adapter=FailingGemini())

    one_shot = service.generate_oneshot_plan_background(
        artifact_id=10,
        study_id=20,
        form_data={"studyName": "Study", "problemDefinition": "Problem"},
        project_keywords=[],
    )
    assert one_shot.status == "failed"
    assert record_service.deleted == [10]

    conversation = service.generate_conversation_plan_background(
        artifact_id=11,
        study_id=21,
        ledger_text="ledger",
        selected_methods=[],
        project_keywords=[],
    )
    assert conversation.status == "failed"
    assert record_service.failed[11] == "boom"
