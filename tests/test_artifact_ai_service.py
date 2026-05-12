from contextlib import contextmanager
import json
from types import SimpleNamespace

from reopsai.application.artifact_ai_service import ArtifactAiService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace()


def make_history(row_id="h1"):
    return SimpleNamespace(
        id=row_id,
        artifact_id=100,
        user_id=10,
        prompt="prompt",
        source="toolbar",
        before_markdown="before",
        after_markdown="after",
        selection_from=1,
        selection_to=5,
        created_at=None,
    )


class FakeArtifactAiRepository:
    artifact = None
    history = []
    created = []

    @classmethod
    def reset(cls):
        cls.artifact = SimpleNamespace(id=100, content="artifact", owner_id=10, study_id=20)
        cls.history = [make_history()]
        cls.created = []

    @classmethod
    def get_artifact(cls, session, artifact_id):
        if cls.artifact and int(artifact_id) == cls.artifact.id:
            return cls.artifact
        return None

    @classmethod
    def list_edit_history(cls, session, *, artifact_id, limit):
        return cls.history[:limit]

    @classmethod
    def create_edit_history(cls, session, **kwargs):
        row = make_history("created")
        row.artifact_id = kwargs["artifact_id"]
        row.user_id = kwargs["user_id"]
        row.prompt = kwargs["prompt"]
        row.source = kwargs["source"] or None
        row.before_markdown = kwargs["before_markdown"]
        row.after_markdown = kwargs["after_markdown"]
        row.selection_from = kwargs["selection_from"] if isinstance(kwargs["selection_from"], int) else None
        row.selection_to = kwargs["selection_to"] if isinstance(kwargs["selection_to"], int) else None
        cls.created.append(row)
        return row


class FakeGemini:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeVector:
    def __init__(self):
        self.improved_service = self

    def hybrid_search(self, **kwargs):
        return {"principles": "principles", "examples": "examples"}

    def context_optimization(self, text, max_length):
        return f"{text}:{max_length}"


def make_service(gemini=None, vector=None):
    FakeArtifactAiRepository.reset()
    return ArtifactAiService(
        repository=FakeArtifactAiRepository,
        session_factory=fake_session_factory,
        gemini_adapter=gemini
        or FakeGemini([
            {"success": True, "content": json.dumps({"original": "before", "modified": "after"})}
        ]),
        vector_adapter=vector,
        usage_context_builder=lambda **kwargs: kwargs,
        usage_runner=lambda context, func, **kwargs: func(**kwargs),
    )


def test_access_history_and_create_history_payloads():
    service = make_service()

    assert service.get_artifact_for_owner(artifact_id=999, owner_ids=["10"]).status == "not_found"
    assert service.get_artifact_for_owner(artifact_id=100, owner_ids=["99"]).status == "forbidden"

    listed = service.list_edit_history(artifact_id=100, owner_ids=["10"], limit=50)
    assert listed.status == "ok"
    assert listed.data[0]["id"] == "h1"

    created = service.create_edit_history(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        before_markdown="before",
        after_markdown="after",
        prompt="prompt",
        source="toolbar",
        selection_from=1,
        selection_to="bad",
    )
    assert created.status == "ok"
    assert created.data["selection_to"] is None
    assert created.data["source"] == "toolbar"


def test_modify_success_and_llm_failure():
    service = make_service()
    result = service.modify_artifact_text(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        selected_text="before",
        user_prompt="수정해줘",
        full_context="context",
        selected_markdown_hint="",
    )
    assert result.status == "ok"
    assert result.data == {"original": "before", "modified": "after", "message": "AI 수정 제안을 생성했습니다."}

    failed = make_service(gemini=FakeGemini([{"success": False, "error": "boom"}])).modify_artifact_text(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        selected_text="before",
        user_prompt="수정해줘",
        full_context="context",
        selected_markdown_hint="",
    )
    assert failed.status == "llm_failed"
    assert failed.error == "AI 수정 실패: boom"


def test_modify_json_fallback_incomplete_and_partial_retry():
    fallback = make_service(gemini=FakeGemini([{"success": True, "content": "plain text"}])).modify_artifact_text(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        selected_text="before",
        user_prompt="수정해줘",
        full_context="context",
        selected_markdown_hint="",
    )
    assert fallback.status == "ok"
    assert fallback.data["modified"] == "plain text"
    assert "JSON 파싱 경고" in fallback.data["message"]

    incomplete = make_service(gemini=FakeGemini([{"success": True, "content": '{"original":"a"'}])).modify_artifact_text(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        selected_text="before",
        user_prompt="수정해줘",
        full_context="context",
        selected_markdown_hint="",
    )
    assert incomplete.status == "incomplete_response"

    long_text = "A" * 2000
    partial_retry = make_service(
        gemini=FakeGemini(
            [
                {"success": True, "content": json.dumps({"original": long_text, "modified": "short"})},
                {"success": True, "content": json.dumps({"original": long_text, "modified": long_text + " updated"})},
            ]
        )
    ).modify_artifact_text(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        selected_text=long_text,
        user_prompt="수정해줘",
        full_context=long_text,
        selected_markdown_hint="",
    )
    assert partial_retry.status == "ok"
    assert partial_retry.data["modified"] == long_text + " updated"

    partial_fail = make_service(
        gemini=FakeGemini(
            [
                {"success": True, "content": json.dumps({"original": long_text, "modified": "short"})},
                {"success": True, "content": json.dumps({"original": long_text, "modified": "still short"})},
            ]
        )
    ).modify_artifact_text(
        artifact_id=100,
        owner_ids=["10"],
        user_id=10,
        selected_text=long_text,
        user_prompt="수정해줘",
        full_context=long_text,
        selected_markdown_hint="",
    )
    assert partial_fail.status == "partial_response"
