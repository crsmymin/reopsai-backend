from contextlib import contextmanager
from types import SimpleNamespace

from reopsai_backend.application.survey_service import SurveyService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace()


class FakeSurveyRepository:
    studies = {}
    owner_id = 10
    artifact_id = 100
    content_updates = []
    completed = {}
    deleted = []
    failed = {}
    delete_raises = False

    @classmethod
    def reset(cls):
        cls.studies = {1: SimpleNamespace(id=1, project_id=20)}
        cls.owner_id = 10
        cls.artifact_id = 100
        cls.content_updates = []
        cls.completed = {}
        cls.deleted = []
        cls.failed = {}
        cls.delete_raises = False

    @classmethod
    def get_study(cls, session, study_id):
        return cls.studies.get(int(study_id))

    @classmethod
    def get_project_owner_id(cls, session, project_id):
        return cls.owner_id

    @classmethod
    def create_survey_artifact(cls, session, *, study_id, owner_id):
        return SimpleNamespace(id=cls.artifact_id, study_id=study_id, owner_id=owner_id)

    @classmethod
    def update_artifact_content(cls, session, *, artifact_id, content):
        cls.content_updates.append((int(artifact_id), content))
        return SimpleNamespace(id=artifact_id, content=content)

    @classmethod
    def complete_artifact(cls, session, *, artifact_id, content):
        cls.completed[int(artifact_id)] = content
        return SimpleNamespace(id=artifact_id, content=content, status="completed")

    @classmethod
    def delete_artifact(cls, session, artifact_id):
        if cls.delete_raises:
            raise RuntimeError("delete failed")
        cls.deleted.append(int(artifact_id))
        return SimpleNamespace(id=artifact_id)

    @classmethod
    def mark_artifact_failed(cls, session, *, artifact_id, message):
        cls.failed[int(artifact_id)] = message
        return SimpleNamespace(id=artifact_id, status="failed", content=message)


class FakeVector:
    def __init__(self):
        self.improved_service = self

    def hybrid_search(self, **kwargs):
        return {"principles": "principles", "examples": "examples"}

    def context_optimization(self, text, max_length=2000):
        return f"optimized:{text}:{max_length}"

    def query_expansion(self, text):
        return f"expanded:{text}"

    def search(self, **kwargs):
        return "relevant examples"


class FakeOpenAI:
    def __init__(self, *, success=True):
        self.success = success
        self.calls = []

    def generate_response(self, prompt, config):
        self.calls.append((prompt, config))
        if not self.success:
            return {"success": False, "error": "failed"}
        return {"success": True, "parsed": {"ok": True}}


class FakeGemini:
    def __init__(self, *, success=True):
        self.success = success
        self.calls = []

    def generate_response(self, prompt, config, model_name=None):
        self.calls.append((prompt, config, model_name))
        if not self.success:
            return {"success": False, "error": "failed"}
        return {"success": True, "parsed": {"ok": True}}


def fake_parser(raw):
    return raw["parsed"]


def make_service(openai=None, gemini=None, vector=None):
    FakeSurveyRepository.reset()
    return SurveyService(
        repository=FakeSurveyRepository,
        session_factory=fake_session_factory,
        openai_adapter=openai or FakeOpenAI(),
        gemini_adapter=gemini or FakeGemini(),
        vector_adapter=vector,
        json_parser=fake_parser,
        project_keyword_fetcher=lambda project_id: ["ux", str(project_id)],
        contextual_keyword_extractor=lambda text: ["keyword"],
        usage_context_builder=lambda **kwargs: kwargs,
        usage_runner=lambda context, func, *args, **kwargs: func(*args, **kwargs),
    )


def test_survey_principles_and_diagnose_order():
    service = make_service(vector=FakeVector())
    assert service.get_survey_principles() == "optimized:principles:2000"

    result = service.diagnose_survey(survey_text="survey")
    assert result.status == "ok"
    assert len(result.data) == 5
    assert result.data == [{"ok": True}] * 5

    fallback = make_service(vector=None).get_survey_principles()
    assert fallback == "참고할 설문 원칙을 DB에서 로드하는 데 실패했습니다."


def test_diagnose_expert_exception_fallback_and_draft_polish():
    failed_openai = FakeOpenAI(success=False)
    service = make_service(openai=failed_openai, vector=FakeVector())

    diagnosis = service.diagnose_survey(survey_text="survey")
    assert diagnosis.status == "ok"
    assert len(diagnosis.data) == 5
    assert diagnosis.data[0]["pass"] is False
    assert "진단 중 오류 발생" in diagnosis.data[0]["reason"]

    draft_openai = FakeOpenAI()
    draft_openai.generate_response = lambda prompt, config: {
        "success": True,
        "parsed": {"draft_suggestions": ["fix"]},
    }
    draft_service = make_service(openai=draft_openai, vector=FakeVector())
    assert draft_service.generate_draft(survey_text="survey", item_to_fix="clarity").data == {"draft": ["fix"]}

    polish_openai = FakeOpenAI()
    polish_openai.generate_response = lambda prompt, config: {
        "success": True,
        "parsed": {"polished": "ok"},
    }
    polish_service = make_service(openai=polish_openai, vector=FakeVector())
    assert polish_service.polish_plan(survey_text="survey", confirmed_survey={}).data == {"polished": "ok"}


def test_create_survey_generation_statuses_and_payload():
    service = make_service(vector=FakeVector())
    result = service.create_survey_generation(study_id=1)
    assert result.status == "ok"
    assert result.data == {"artifact_id": 100, "project_id": 20, "project_keywords": ["ux", "20"]}

    assert service.create_survey_generation(study_id=999).status == "not_found"
    FakeSurveyRepository.owner_id = None
    assert service.create_survey_generation(study_id=1).status == "project_not_found"


def test_background_success_updates_partial_and_completed_content():
    openai = FakeOpenAI()
    openai_responses = [
        {"success": True, "parsed": {"key_variables": ["age"], "balance_variables": [], "target_groups": ["users"]}},
        {"success": True, "parsed": {"screening_criteria": ["used app"]}},
        {"success": True, "parsed": {"options": {"q1": ["A", "B"]}}},
    ]
    openai.generate_response = lambda prompt, config: openai_responses.pop(0)

    gemini = FakeGemini()
    gemini.generate_response = lambda prompt, config, model_name=None: {
        "success": True,
        "parsed": {
            "blocks": [{"id": "B1", "title": "Block 1", "ai_comment": "comment"}],
            "form_elements": [{"id": "q1", "element": "RadioButtons", "text": "Pick", "block_id": "B1"}],
        },
    }
    service = make_service(openai=openai, gemini=gemini, vector=FakeVector())

    result = service.generate_survey_background(
        artifact_id=100,
        research_plan="plan",
        project_keywords=["ux"],
    )

    assert result.status == "ok"
    assert FakeSurveyRepository.content_updates[0][0] == 100
    assert "문항을 생성하고 있습니다" in FakeSurveyRepository.content_updates[0][1]
    assert "# 스크리너 설문" in FakeSurveyRepository.completed[100]
    assert "Pick" in FakeSurveyRepository.completed[100]


def test_background_failure_deletes_or_marks_failed():
    service = make_service(openai=FakeOpenAI(success=False), vector=FakeVector())
    result = service.generate_survey_background(
        artifact_id=100,
        research_plan="plan",
        project_keywords=["ux"],
    )
    assert result.status == "failed"
    assert FakeSurveyRepository.deleted == [100]

    service = make_service(openai=FakeOpenAI(success=False), vector=FakeVector())
    FakeSurveyRepository.delete_raises = True
    result = service.generate_survey_background(
        artifact_id=100,
        research_plan="plan",
        project_keywords=["ux"],
    )
    assert result.status == "failed"
    assert "변수 추출 실패" in FakeSurveyRepository.failed[100]
