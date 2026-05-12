from contextlib import contextmanager
from types import SimpleNamespace

from reopsai_backend.application.guideline_service import GuidelineService


@contextmanager
def fake_session_factory():
    yield SimpleNamespace()


class FakePromptBuilder:
    @staticmethod
    def prompt_extract_methodologies(research_plan):
        return f"extract:{research_plan}"

    @staticmethod
    def prompt_generate_guideline(research_plan, options_json, rules_context_str, examples_context_str):
        return f"generate:{research_plan}:{options_json}:{rules_context_str}:{examples_context_str}"


class FakeOpenAI:
    client = object()

    def __init__(self, *, success=True, content="content"):
        self.success = success
        self.content = content
        self.calls = []

    def generate_response(self, prompt, config, model_name=None):
        self.calls.append((prompt, config, model_name))
        if not self.success:
            return {"success": False, "error": "failed"}
        return {"success": True, "content": self.content}


class FakeVector:
    def __init__(self):
        self.improved_service = self

    def hybrid_search(self, **kwargs):
        return {"principles": "rules", "examples": "examples"}


class FakeGuidelineRepository:
    studies = {}
    owner_id = 10
    artifact_id = 100
    completed = {}
    deleted = []
    failed = {}
    delete_raises = False

    @classmethod
    def reset(cls):
        cls.studies = {1: SimpleNamespace(id=1, project_id=20)}
        cls.owner_id = 10
        cls.artifact_id = 100
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
    def create_guideline_artifact(cls, session, *, study_id, owner_id):
        return SimpleNamespace(id=cls.artifact_id, study_id=study_id, owner_id=owner_id)

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


def make_service(openai=None, vector=None):
    FakeGuidelineRepository.reset()
    return GuidelineService(
        repository=FakeGuidelineRepository,
        session_factory=fake_session_factory,
        openai_adapter=openai or FakeOpenAI(content='{"methodologies": ["UT"]}'),
        vector_adapter=vector or FakeVector(),
        prompt_builder=FakePromptBuilder,
        json_parser=lambda raw: {"methodologies": ["UT"]},
        project_keyword_fetcher=lambda project_id: ["ux", str(project_id)],
        contextual_keyword_extractor=lambda text: ["keyword"],
    )


def test_extract_methods_and_llm_failure_status():
    service = make_service()
    result = service.extract_methods(research_plan="plan", temperature=0.2, require_success=True)
    assert result.status == "ok"
    assert result.data == {"methodologies": ["UT"]}

    failed = make_service(openai=FakeOpenAI(success=False)).extract_methods(
        research_plan="plan",
        temperature=0.2,
        require_success=True,
    )
    assert failed.status == "llm_failed"
    assert failed.error == "LLM 응답 실패"


def test_create_guideline_generation_statuses_and_payload():
    service = make_service()
    result = service.create_guideline_generation(study_id=1)
    assert result.status == "ok"
    assert result.data == {"artifact_id": 100, "project_id": 20, "project_keywords": ["ux", "20"]}

    missing = service.create_guideline_generation(study_id=999)
    assert missing.status == "not_found"

    FakeGuidelineRepository.owner_id = None
    no_project = service.create_guideline_generation(study_id=1)
    assert no_project.status == "project_not_found"


def test_background_generation_success_completes_artifact():
    openai = FakeOpenAI(content="generated guideline")
    service = make_service(openai=openai)

    result = service.generate_guideline_background(
        artifact_id=100,
        research_plan="plan",
        methodologies=["UT"],
        project_keywords=["ux"],
    )

    assert result.status == "ok"
    assert FakeGuidelineRepository.completed == {100: "generated guideline"}
    assert openai.calls[-1][2] == "gpt-5"


def test_background_generation_failure_deletes_or_marks_failed():
    service = make_service(openai=FakeOpenAI(success=False))
    result = service.generate_guideline_background(
        artifact_id=100,
        research_plan="plan",
        methodologies=["UT"],
        project_keywords=["ux"],
    )
    assert result.status == "failed"
    assert FakeGuidelineRepository.deleted == [100]

    service = make_service(openai=FakeOpenAI(success=False))
    FakeGuidelineRepository.delete_raises = True
    result = service.generate_guideline_background(
        artifact_id=100,
        research_plan="plan",
        methodologies=["UT"],
        project_keywords=["ux"],
    )
    assert result.status == "failed"
    assert "LLM 생성 실패" in FakeGuidelineRepository.failed[100]
