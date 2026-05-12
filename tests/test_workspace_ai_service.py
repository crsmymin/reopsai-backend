import sys
import types
from types import SimpleNamespace


def _install_workspace_ai_import_fakes():
    fake_openai = types.ModuleType("services.openai_service")
    fake_openai.openai_service = SimpleNamespace(generate_response=lambda *args, **kwargs: {"success": True, "content": ""})
    sys.modules.setdefault("services.openai_service", fake_openai)

    fake_gemini = types.ModuleType("services.gemini_service")
    fake_gemini.gemini_service = SimpleNamespace(generate_response=lambda *args, **kwargs: {"success": True, "content": ""})
    sys.modules.setdefault("services.gemini_service", fake_gemini)

    fake_vector = types.ModuleType("services.vector_service")
    fake_vector.vector_service = SimpleNamespace(
        improved_service=SimpleNamespace(
            hybrid_search=lambda **kwargs: {"principles": "", "examples": ""},
            context_optimization=lambda text, max_length=1000: text,
        )
    )
    sys.modules.setdefault("services.vector_service", fake_vector)

    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda *args, **kwargs: None
    sys.modules.setdefault("requests", fake_requests)

    fake_bs4 = types.ModuleType("bs4")
    fake_bs4.BeautifulSoup = lambda *args, **kwargs: None
    sys.modules.setdefault("bs4", fake_bs4)


_install_workspace_ai_import_fakes()

from reopsai.application.workspace_ai_service import WorkspaceAiService


class FakeOpenAi:
    def __init__(self, content, success=True):
        self.content = content
        self.success = success

    def generate_response(self, *args, **kwargs):
        if not self.success:
            return {"success": False, "error": "boom"}
        return {"success": True, "content": self.content}


class FakePlanGeneration:
    def __init__(self, success=True):
        self.success = success

    def generate_oneshot_parallel_experts(self, form_data, project_keywords):
        if self.success:
            return {"success": True, "final_plan": "plan"}
        return {"success": False, "error": "failed"}


class FakeWorkspaceRecords:
    def __init__(self, complete_result=True):
        self.complete_result = complete_result
        self.completed = {}
        self.deleted = []

    def complete_artifact(self, *, artifact_id, content):
        self.completed[artifact_id] = content
        return self.complete_result

    def delete_artifact_by_id(self, *, artifact_id):
        self.deleted.append(artifact_id)
        return True


def test_workspace_ai_project_and_study_name_parsing():
    service = WorkspaceAiService(
        openai_adapter=FakeOpenAi('{"projectName": "KB증권 M-able", "tags": ["금융", "MTS"]}'),
        project_keyword_fetcher=lambda project_id: [],
        plan_generation_adapter=FakePlanGeneration(),
        workspace_record_service=FakeWorkspaceRecords(),
    )

    project = service.generate_project_name(study_name="Study", problem_definition="Problem")
    assert project.status == "ok"
    assert project.data == {"projectName": "KB증권 M-able", "tags": ["금융", "MTS"]}

    study_service = WorkspaceAiService(
        openai_adapter=FakeOpenAi('1. "결제 이탈 원인"'),
        project_keyword_fetcher=lambda project_id: [],
        plan_generation_adapter=FakePlanGeneration(),
        workspace_record_service=FakeWorkspaceRecords(),
    )
    study = study_service.generate_study_name(problem_definition="결제 전환율이 낮은 문제가 있습니다.")
    assert study.status == "ok"
    assert study.data == {"studyName": "결제 이탈 원인"}


def test_workspace_ai_tags_stream_and_failure_shape():
    service = WorkspaceAiService(
        openai_adapter=FakeOpenAi("금융, 모바일앱, MTS"),
        project_keyword_fetcher=lambda project_id: [],
        plan_generation_adapter=FakePlanGeneration(),
        workspace_record_service=FakeWorkspaceRecords(),
    )
    chunks = list(service.stream_tags(project_title="M-able", product_url=""))
    assert '"tags": ["금융"]' in chunks[0]
    assert '"done": true' in chunks[-1]

    failing = WorkspaceAiService(
        openai_adapter=FakeOpenAi("", success=False),
        project_keyword_fetcher=lambda project_id: [],
        plan_generation_adapter=FakePlanGeneration(),
        workspace_record_service=FakeWorkspaceRecords(),
    )
    assert '"error": "boom"' in list(failing.stream_tags(project_title="M-able", product_url=""))[0]


def test_workspace_ai_regenerate_plan_background_success_and_failure():
    records = FakeWorkspaceRecords()
    service = WorkspaceAiService(
        openai_adapter=FakeOpenAi(""),
        project_keyword_fetcher=lambda project_id: ["ux"],
        plan_generation_adapter=FakePlanGeneration(success=True),
        workspace_record_service=records,
    )
    result = service.regenerate_plan_background(artifact_id=1, study_id=2, project_id=3, form_data={})
    assert result.status == "ok"
    assert records.completed == {1: "plan"}

    failed_records = FakeWorkspaceRecords()
    failed_service = WorkspaceAiService(
        openai_adapter=FakeOpenAi(""),
        project_keyword_fetcher=lambda project_id: ["ux"],
        plan_generation_adapter=FakePlanGeneration(success=False),
        workspace_record_service=failed_records,
    )
    failed = failed_service.regenerate_plan_background(artifact_id=4, study_id=5, project_id=6, form_data={})
    assert failed.status == "failed"
    assert failed_records.deleted == [4]
