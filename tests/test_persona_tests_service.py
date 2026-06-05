from contextlib import contextmanager
from datetime import datetime, timezone
import json
import threading
import time
from types import SimpleNamespace

from reopsai.application.persona_service import (
    PersonaService,
    _apply_structured_flow_analysis_scores,
    _apply_structured_scoring,
    _apply_structured_screen_scores,
    _aggregate_flow_step_risk,
    _get_comment_score,
    _normalize_ui_scoring_analysis,
    _score_flow_completion_from_flow_analysis,
    _score_flow_completion_metric,
)
from reopsai.infrastructure.persona_capture import PersonaCapture, normalize_capture_url


def _now():
    return datetime.now(timezone.utc)


def _test_record(**overrides):
    base = {
        "id": 1,
        "company_id": 100,
        "created_by_user_id": 10,
        "name": "가입 플로우 테스트",
        "description": "회원가입 화면 검증",
        "device_type": "pc",
        "validation_type": "single",
        "scope_type": "screen",
        "source_type": "image",
        "status": "draft",
        "progress": 0,
        "error_message": None,
        "persona_count": None,
        "screen_count": 1,
        "summary": None,
        "source_data": {
            "imageEntries": [{"id": "screen-1", "name": "가입 화면", "imageUrl": "/asset.png"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
        },
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _persona_record(**overrides):
    record = SimpleNamespace(
        id=20,
        company_id=100,
        team_id=None,
        folder_id=None,
        created_by_user_id=10,
        name="김민수",
        title="직장인",
        age=34,
        gender="남자",
        personality="신중함",
        language="ko",
        source_type="manual",
        source_data=None,
        image_asset_id=None,
        image_url=None,
        image_prompt=None,
        image_mime_type=None,
        schema_version=3,
        locale="KR",
        profile=None,
        telecom_profile=None,
        income=None,
        sector=None,
        generation="millennial",
        ethnicity=None,
        current_city="서울",
        current_country="KR",
        locations=None,
        organisation=None,
        role_area="기획",
        role_level=None,
        attitudes=None,
        biography="업무 도구를 고를 때 실패 비용을 크게 보는 편입니다.",
        demeanour=None,
        interests=None,
        behaviours="꼼꼼히 비교합니다.",
        motivation="실패 없는 선택",
        upbringing=None,
        preferences="근거 중심",
        social_context="팀 내에서 새로운 서비스 도입 여부를 검토합니다.",
        cultural_background=None,
        quote="근거가 먼저 보여야 해요.",
        additional_info=None,
        telecom_usage=None,
        telecom_values=None,
        ux_interaction=None,
        telecom_behavior_dimensions=None,
        generation_metadata=None,
        interview_pack=None,
        interview_pack_source_hash=None,
        interview_pack_model=None,
        interview_pack_version=None,
        interview_pack_updated_at=None,
        created_at=_now(),
        updated_at=_now(),
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


class FakeLlmAdapter:
    prompts = []
    media_parts = []

    def __init__(self):
        self.prompts = []
        self.media_parts = []
        self.model_names = []

    def generate_response(self, prompt, generation_config=None, model_name=None):
        self.prompts.append(prompt)
        self.model_names.append(model_name)
        if "STAGE: ab_screen_analysis" in prompt:
            content = {
                "mode": "flow" if "모드: flow" in prompt else "single",
                "elementDifferences": [
                    "A: CTA '가입하기' 1개 / B: CTA '요금제 변경' 1개",
                    "A: 카드 4개 / B: 비교표 1개",
                ],
                "variants": {
                    "A": [
                        {
                            "stepIndex": 0,
                            "name": "A안",
                            "visibleHeadings": ["멤버십 혜택"],
                            "ctaLabels": ["가입하기"],
                            "uiElements": ["카드 4개", "2열 그리드"],
                            "textBlocks": ["누적 할인액"],
                            "structureNotes": ["상단 헤더", "세로 스크롤"],
                        }
                    ],
                    "B": [
                        {
                            "stepIndex": 0,
                            "name": "B안",
                            "visibleHeadings": ["요금제 비교"],
                            "ctaLabels": ["요금제 변경"],
                            "uiElements": ["비교표 1개", "텍스트 블록 8개"],
                            "textBlocks": ["월 요금"],
                            "structureNotes": ["상단 헤더", "긴 스크롤"],
                        }
                    ],
                },
                "stepElementDifferences": [{"stepIndex": 0, "facts": ["A: 카드 4개", "B: 비교표 1개"]}],
            }
        elif "STAGE: ab_persona_preference" in prompt:
            content = {
                "scores": {
                    "winner": "A",
                    "reasonForChoice": "A안 대비 B안은 제가 찾는 혜택 정보를 더 빨리 확인할 수 있어요. B안은 요금제 비교에 초점이 맞아 지금 목적과는 거리가 있어요.",
                },
                "feedback": [
                    "A안 대비 B안은 카테고리 탐색이 더 바로 보여요.",
                    "B안 대비 A안은 개인화된 멤버십 정보가 먼저 보여요.",
                ],
            }
        elif "A/B UX variants" in prompt:
            content = {
                "scores": {"winner": "A", "reasonForChoice": "A안이 더 명확합니다."},
                "feedback": ["A안 대비 B안은 다음 행동 근거가 약합니다."],
            }
        elif "interview_question_set" in prompt or "질문 세트를 생성" in prompt:
            content = {
                "opening": ["평소 어떤 기준으로 가입 서비스를 살펴보시나요?"],
                "tasks": [{"title": "판단 기준", "questions": ["무엇을 확인하나요?"]}],
                "closing": ["마지막으로 꼭 개선되었으면 하는 점은 무엇인가요?"],
                "followup_strategies": ["망설임이 드러난 답변을 더 깊게 묻습니다."],
            }
        elif "인터뷰 응답에 적합한 메모리로 정제" in prompt:
            content = {
                "identity": {"name": "김민수", "age": 34, "gender": "남자", "job": "직장인", "life_context": "근거 중심"},
                "core_traits": ["신중함"],
                "decision_rules": ["금액과 조건을 먼저 확인"],
                "experience_memory": [],
                "experience_library": [],
                "needs_and_painpoints": ["명확한 조건"],
                "ux_context_clues": ["CTA와 비용 정보"],
                "generative_thought_seeds": ["비교 기준"],
                "communication_style": {"tone": "차분함"},
                "consistency_rules": ["근거 중심으로 판단"],
                "grounding_notes": ["없는 사실은 만들지 않음"],
            }
        elif "1:1 AI interview" in prompt or "1:1 AI 인터뷰" in prompt:
            content = {
                "summary": {
                    "headline": "근거 중심 의사결정",
                    "key_needs": ["금액과 조건"],
                    "pain_points": ["불명확한 혜택"],
                    "opportunities": ["비교 근거 강화"],
                },
                "turns": [{"question": "무엇을 확인하나요?", "answer": "금액과 조건을 먼저 봅니다."}],
            }
        else:
            content = {
                "summary": "김민수님은 CTA와 신뢰 근거를 중심으로 평가했습니다.",
                "personaGoalFit": "목표 수행 가능",
                "scores": {"clarity": 82, "usability": 78, "appeal": 70, "overall": 77},
                "feedback": {
                    "overallFeedback": "전반적으로 이해 가능합니다.",
                    "screenFeedbacks": [{"screenIndex": 0, "feedback": "CTA가 보입니다."}],
                },
                "pinComments": [{"screenIndex": 0, "x": 52, "y": 45, "type": "improvement", "content": "근거를 강화합니다."}],
                "flowAnalysis": [],
                "strengths": ["CTA 노출"],
                "risks": ["근거 부족"],
                "recommendations": ["신뢰 근거 추가"],
                "screenInsights": [{"screenId": "screen-1", "name": "가입 화면", "positives": ["CTA"], "issues": [], "recommendation": "근거 추가"}],
            }
        return {"success": True, "content": json.dumps(content, ensure_ascii=False), "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    def generate_multimodal_response(self, prompt, *, media_parts=None, generation_config=None, model_name=None):
        self.media_parts.append(media_parts or [])
        return self.generate_response(prompt, generation_config=generation_config, model_name=model_name)


class MalformedAbFlowAdapter(FakeLlmAdapter):
    def generate_response(self, prompt, generation_config=None, model_name=None):
        self.prompts.append(prompt)
        if "STAGE: ab_screen_analysis" in prompt:
            return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)
        content = {
            "scores": {
                "winner": "A",
                "reasonForChoice": "A안이 더 명확합니다.",
                "journeyComparison": "A 플로우가 더 쉬워 보입니다.",
                "stepAnalysis": ["첫 단계는 A가 낫습니다.", {"stepIndex": "2", "preferredVersion": "B"}],
            },
            "feedback": ["A안 대비 B안은 다음 행동 근거가 약합니다."],
        }
        return {"success": True, "content": json.dumps(content, ensure_ascii=False), "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class FailingInterviewAdapter(FakeLlmAdapter):
    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "아래 인터뷰 설계" in prompt:
            raise RuntimeError("interview failed")
        return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)


class MalformedInterviewAdapter(FakeLlmAdapter):
    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "아래 인터뷰 설계" in prompt:
            return {"success": True, "content": "not json", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)


class TransientInterviewAdapter(FakeLlmAdapter):
    def __init__(self, *, failures_before_success=2):
        super().__init__()
        self.failures_before_success = failures_before_success
        self.interview_calls = 0

    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "아래 인터뷰 설계" in prompt:
            self.interview_calls += 1
            if self.interview_calls <= self.failures_before_success:
                raise RuntimeError(f"transient interview failure {self.interview_calls}")
        return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)


class ConcurrentInterviewAdapter(FakeLlmAdapter):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._active_interviews = 0
        self.max_active_interviews = 0

    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "아래 인터뷰 설계" not in prompt:
            return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)

        with self._lock:
            self._active_interviews += 1
            self.max_active_interviews = max(self.max_active_interviews, self._active_interviews)
        try:
            time.sleep(0.05)
            return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)
        finally:
            with self._lock:
                self._active_interviews -= 1


class ConcurrentUiTestAdapter(FakeLlmAdapter):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._active_ui_tests = 0
        self.max_active_ui_tests = 0

    def generate_response(self, prompt, generation_config=None, model_name=None):
        if "evaluating a UI" not in prompt:
            return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)

        with self._lock:
            self._active_ui_tests += 1
            self.max_active_ui_tests = max(self.max_active_ui_tests, self._active_ui_tests)
        try:
            time.sleep(0.05)
            return super().generate_response(prompt, generation_config=generation_config, model_name=model_name)
        finally:
            with self._lock:
                self._active_ui_tests -= 1


class FakeRepository:
    ui_test = _test_record()
    asset = None
    ab_test = _test_record(
        id=2,
        purpose="가입 화면 비교",
        service_context="가입",
        mode="single",
        screens=[{"version": "A", "name": "A안"}, {"version": "B", "name": "B안"}],
        transitions=None,
        context_data={"personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]}},
        enable_consistency_validation=False,
        consistency_run_count=3,
    )
    interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"questions": [{"id": "q1", "text": "무엇을 확인하나요?"}]},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )
    personas = None
    pack_updates = []

    @staticmethod
    def get_figma_account(session, *, company_id, user_id):
        if company_id == 100 and user_id == 10:
            return SimpleNamespace(id=7, access_token_encrypted="encrypted-token")
        return None

    @staticmethod
    def can_modify_record(session, record, *, company_id, user_id):
        return record.company_id == company_id and record.created_by_user_id == user_id

    @staticmethod
    def create_asset(session, *, company_id, user_id, data):
        return SimpleNamespace(id=77, company_id=company_id, created_by_user_id=user_id, **data)

    @staticmethod
    def get_asset(session, *, company_id, asset_id):
        if FakeRepository.asset and FakeRepository.asset.id == int(asset_id):
            return FakeRepository.asset
        return None

    @staticmethod
    def get_ui_test(session, *, company_id, test_id):
        return FakeRepository.ui_test

    @staticmethod
    def update_ui_test(session, test, *, user_id, data):
        for key, value in data.items():
            setattr(test, key, value)
        return test

    @staticmethod
    def delete_ui_test_results(session, *, company_id, test_id):
        return None

    @staticmethod
    def list_ui_test_results(session, *, company_id, test_id):
        return [
            SimpleNamespace(
                id=11,
                test_id=test_id,
                persona_id=20,
                status="completed",
                summary="전반적으로 이해 가능합니다.",
                persona_goal_fit="목표 수행 가능",
                scores={"clarity": 82, "usability": 78, "appeal": 70, "overall": 77},
                feedback={"overallFeedback": "전반적으로 이해 가능합니다.", "screenFeedbacks": []},
                pin_comments=[],
                flow_analysis=[],
                persona_snapshot={"id": 20, "name": "김민수", "title": "직장인", "imageUrl": None},
                confidence={"promptVersion": "persona_test_v2"},
                evidence_ids=["promptVersion:persona_test_v2"],
                strengths=["CTA 노출"],
                risks=["근거 부족"],
                recommendations=["신뢰 근거 추가"],
                screen_insights=[],
                evidence=None,
                raw_response=None,
                error_message=None,
                created_at=_now(),
                updated_at=_now(),
            )
        ]

    @staticmethod
    def list_personas_by_ids(session, *, company_id, persona_ids):
        if FakeRepository.personas is not None:
            return [row for row in FakeRepository.personas if row.id in set(int(item) for item in persona_ids)]
        return [_persona_record()]

    @staticmethod
    def list_all_personas(session, *, company_id):
        return FakeRepository.personas or [_persona_record()]

    @staticmethod
    def create_ui_test_result(session, *, company_id, test_id, persona_id, data):
        return SimpleNamespace(id=11, test_id=test_id, persona_id=persona_id, status="completed", created_at=_now(), updated_at=_now(), error_message=None, evidence=None, **data)

    @staticmethod
    def create_activity(session, *, company_id, persona_id, data):
        return SimpleNamespace(id=99, **data)

    @staticmethod
    def get_memory_settings(session, *, company_id, persona_id):
        return SimpleNamespace(
            id=41,
            persona_id=persona_id,
            enable_memory=True,
            memory_strength=70,
            apply_to_chat=True,
            apply_to_tests=True,
            created_at=_now(),
            updated_at=_now(),
        )

    @staticmethod
    def list_activities(session, *, company_id, persona_id):
        return [
            SimpleNamespace(
                id=42,
                persona_id=persona_id,
                activity_type="ui_test",
                activity_id=1,
                summary="가입 화면에서 비용 근거를 확인하려 했습니다.",
                was_validated=True,
                was_correct=True,
                metadata_={},
                created_at=_now(),
            )
        ]

    @staticmethod
    def list_traits(session, *, company_id, persona_id):
        return [
            SimpleNamespace(
                id=43,
                persona_id=persona_id,
                trait="비용 조건을 먼저 확인합니다.",
                category="decision",
                confidence=0.82,
                source_count=1,
                sources=[],
                is_active=True,
                created_at=_now(),
                updated_at=_now(),
            )
        ]

    @staticmethod
    def get_ab_test(session, *, company_id, ab_test_id):
        return FakeRepository.ab_test

    @staticmethod
    def update_ab_test(session, test, *, user_id, data):
        for key, value in data.items():
            setattr(test, key, value)
        return test

    @staticmethod
    def delete_ab_test_results(session, *, company_id, ab_test_id):
        return None

    @staticmethod
    def list_ab_test_results(session, *, company_id, ab_test_id):
        return [
            SimpleNamespace(
                id=12,
                ab_test_id=ab_test_id,
                persona_id=20,
                status="completed",
                persona_snapshot={"id": 20, "name": "김민수", "title": "직장인", "imageUrl": None},
                scores={"winner": "A", "reasonForChoice": "A안이 더 명확합니다."},
                feedback=["A안 대비 B안은 다음 행동 근거가 약합니다."],
                confidence={"promptVersion": "persona_test_v2"},
                evidence_ids=["promptVersion:persona_test_v2"],
                raw_response=None,
                error_message=None,
                created_at=_now(),
                updated_at=_now(),
            )
        ]

    @staticmethod
    def create_ab_test_result(session, *, company_id, ab_test_id, persona_id, data):
        return SimpleNamespace(id=12, ab_test_id=ab_test_id, persona_id=persona_id, status="completed", created_at=_now(), updated_at=_now(), error_message=None, **data)

    @staticmethod
    def get_interview(session, *, company_id, interview_id):
        return FakeRepository.interview

    @staticmethod
    def list_interviews(session, *, company_id):
        return [FakeRepository.interview]

    @staticmethod
    def create_interview(session, *, company_id, user_id, data):
        FakeRepository.interview = SimpleNamespace(
            id=3,
            company_id=company_id,
            created_by_user_id=user_id,
            name=data["name"],
            goal=data["goal"],
            product_description=data.get("product_description"),
            length=data.get("length") or "quick",
            question_set=data.get("question_set"),
            model=data.get("model"),
            pack_model=data.get("pack_model"),
            status="draft",
            progress=0,
            persona_ids=data.get("persona_ids") or [],
            summary=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=_now(),
            updated_at=_now(),
        )
        return FakeRepository.interview

    @staticmethod
    def update_interview(session, interview, *, user_id, data):
        for key, value in data.items():
            setattr(interview, key, value)
        return interview

    @staticmethod
    def delete_interview_results(session, *, company_id, interview_id):
        return None

    @staticmethod
    def list_interview_results(session, *, company_id, interview_id):
        return [
            SimpleNamespace(
                id=13,
                interview_id=interview_id,
                persona_id=20,
                status="completed",
                persona_snapshot={"id": 20, "name": "김민수", "title": "직장인"},
                summary={"insights": ["근거 중심 의사결정"]},
                turns=[{"question": "무엇을 확인하나요?", "answer": "금액과 조건을 먼저 봅니다."}],
                pack=None,
                raw_response=None,
                error_message=None,
                created_at=_now(),
                updated_at=_now(),
            )
        ]

    @staticmethod
    def create_interview_result(session, *, company_id, interview_id, persona_id, data):
        payload = dict(data)
        status = payload.pop("status", "completed")
        error_message = payload.pop("error_message", None)
        return SimpleNamespace(id=13, interview_id=interview_id, persona_id=persona_id, status=status, created_at=_now(), updated_at=_now(), error_message=error_message, **payload)

    @staticmethod
    def update_persona_interview_pack(session, persona, *, user_id, pack, source_hash, model, version):
        persona.interview_pack = pack
        persona.interview_pack_source_hash = source_hash
        persona.interview_pack_model = model
        persona.interview_pack_version = version
        persona.interview_pack_updated_at = _now()
        FakeRepository.pack_updates.append(
            {"persona_id": persona.id, "pack": pack, "source_hash": source_hash, "model": model, "version": version}
        )
        return persona


@contextmanager
def fake_session_factory():
    yield object()


class FakeCapture:
    def capture_url(self, url):
        return {
            "url": url,
            "title": "랜딩 페이지",
            "status_code": 200,
            "content_type": "text/html",
            "screenshot_base64": "aW1hZ2U=",
            "capture_backend": "playwright",
        }


class FakeCaptureWithoutScreenshot:
    def capture_url(self, url):
        return {
            "url": url,
            "title": "랜딩 페이지",
            "status_code": 200,
            "content_type": "text/html",
            "capture_backend": "http",
        }


class FakeStorage:
    local_paths = {}

    @staticmethod
    def save_bytes(image_bytes, *, company_id, filename, mime_type, asset_type="generated_image"):
        return {
            "asset_type": asset_type,
            "storage_backend": "local",
            "storage_key": "company-100/test/captured.png",
            "original_filename": filename,
            "mime_type": mime_type,
            "byte_size": len(image_bytes),
        }

    @staticmethod
    def resolve_local_path(storage_key):
        return FakeStorage.local_paths[storage_key]


class FakeFigmaClient:
    @staticmethod
    def decrypt(value):
        return "access-token" if value else None

    @staticmethod
    def fetch_flow_preview(*, file_key, start_node_id, access_token):
        return {
            "startScreenId": f"screen_{start_node_id}",
            "screens": [
                {
                    "id": f"screen_{start_node_id}",
                    "name": "Signup Start",
                    "figmaNodeId": start_node_id,
                    "imageUrl": "https://figma.example.com/screen-1.png",
                    "width": 390,
                    "height": 844,
                    "viewport": "mobile",
                    "order": 0,
                },
                {
                    "id": "screen_56:78",
                    "name": "Signup Done",
                    "figmaNodeId": "56:78",
                    "imageUrl": "https://figma.example.com/screen-2.png",
                    "width": 390,
                    "height": 844,
                    "viewport": "mobile",
                    "order": 1,
                },
            ],
            "transitions": [{"id": "trans_1", "fromScreenId": f"screen_{start_node_id}", "toScreenId": "screen_56:78"}],
        }

    @staticmethod
    def fetch_node_images(*, file_key, node_ids, access_token):
        return {node_ids[0]: "https://figma.example.com/screen.png"}

    @staticmethod
    def download_image(image_url):
        return b"figma-image", "image/png"


def _service(**overrides):
    adapter = overrides.pop("llm_adapter", FakeLlmAdapter())
    return PersonaService(repository=FakeRepository, session_factory=fake_session_factory, llm_adapter=adapter, **overrides)


def test_normalize_capture_url_accepts_common_user_input_shapes():
    assert normalize_capture_url("example.com") == "https://example.com"
    assert normalize_capture_url(" example.com/path?q=1 ") == "https://example.com/path?q=1"
    assert normalize_capture_url("//example.com/path") == "https://example.com/path"
    assert normalize_capture_url("localhost:3000") == "http://localhost:3000"
    assert normalize_capture_url("127.0.0.1:5001/api/auth/test") == "http://127.0.0.1:5001/api/auth/test"
    assert normalize_capture_url("192.168.0.10:3000") == "http://192.168.0.10:3000"


def test_ui_test_run_generates_persona_result_shape_without_zero_dummy():
    FakeRepository.ui_test = _test_record()
    result = _service().run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status_code == 200
    row = result.data["results"][0]
    assert row["scores"]["overall"] == 77
    assert row["pin_comments"][0]["content"] == "근거를 강화합니다."
    assert row["pinComments"][0]["content"] == "근거를 강화합니다."
    assert any(comment["type"] == "praise" for comment in row["pinComments"])
    assert row["personaId"] == 20
    assert row["personaName"] == "김민수"
    assert row["evidence_ids"] == ["promptVersion:persona_test_v2"]


def test_ui_test_uses_default_openai_persona_test_model():
    FakeRepository.ui_test = _test_record()
    adapter = FakeLlmAdapter()

    result = _service(llm_adapter=adapter).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status_code == 200
    assert adapter.model_names == ["gpt-5.4"]
    assert result.data["results"][0]["confidence"]["model"] == "gpt-5.4"


def test_ui_test_run_processes_personas_concurrently(monkeypatch):
    persona_ids = [20, 21, 22, 23]
    FakeRepository.personas = [_persona_record(id=persona_id, name=f"김민수{persona_id}") for persona_id in persona_ids]
    FakeRepository.ui_test = _test_record(
        source_data={
            "imageEntries": [{"id": "screen-1", "name": "가입 화면", "imageUrl": "/asset.png"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": persona_ids},
        },
    )
    monkeypatch.setenv("PERSONA_UI_TEST_MAX_CONCURRENCY", "3")
    adapter = ConcurrentUiTestAdapter()

    try:
        result = _service(llm_adapter=adapter).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

        assert result.status_code == 200
        assert len(result.data["results"]) == 4
        assert adapter.max_active_ui_tests > 1
    finally:
        FakeRepository.personas = None


def test_ui_test_pin_coordinates_accept_ratio_and_percent_values():
    pins = _service()._normalize_ui_pin_comments(
        feedback={
            "pinComments": [
                {"screenIndex": 0, "x": 0.52, "y": "0.45", "type": "problem", "content": "비율 좌표"},
                {"screenIndex": 0, "x": "63%", "y": "48%", "type": "praise", "content": "퍼센트 좌표"},
            ]
        },
        screens=[{"id": "screen-1", "name": "가입 화면"}],
    )

    assert pins[0]["x"] == 52
    assert pins[0]["y"] == 45
    assert pins[0]["hasMarkerCoordinates"] is True
    assert pins[1]["x"] == 63
    assert pins[1]["y"] == 48
    assert pins[1]["hasMarkerCoordinates"] is True


def test_ui_test_pin_coordinates_mark_generated_fallbacks():
    pins = _service()._normalize_ui_pin_comments(
        feedback={
            "pinComments": [
                {"screenIndex": 0, "type": "problem", "content": "좌표 없는 코멘트"},
            ]
        },
        screens=[{"id": "screen-1", "name": "가입 화면"}],
    )

    assert pins[0]["x"] == 42
    assert pins[0]["y"] == 38
    assert pins[0]["hasMarkerCoordinates"] is False


def test_ui_test_pin_coordinates_use_figma_control_bounds_when_missing():
    service = _service()
    screens = service._screen_manifest(
        {
            "figmaScreens": [
                {
                    "id": "screen_12:34",
                    "name": "가입 화면",
                    "figmaNodeId": "12:34",
                    "imageUrl": "/api/persona/storage/88",
                    "width": 400,
                    "height": 800,
                }
            ],
            "figmaPreview": {
                "transitions": [
                    {
                        "fromScreenId": "screen_12:34",
                        "toScreenId": "screen_56:78",
                        "controlNodeId": "button-1",
                        "controlNodeName": "Primary CTA",
                        "controlText": "시작하기",
                        "controlBounds": {"x": 300, "y": 640, "width": 80, "height": 50},
                    }
                ]
            },
        }
    )

    pins = service._normalize_ui_pin_comments(
        feedback={"pinComments": [{"screenIndex": 0, "type": "improvement", "content": "시작하기 버튼의 근거를 강화합니다."}]},
        screens=screens,
    )
    pins = service._apply_ui_pin_coordinate_hints(pin_comments=pins, screens=screens)

    assert pins[0]["x"] == 85
    assert pins[0]["y"] == 83
    assert pins[0]["coordinateSource"] == "figmaControlBounds"
    assert pins[0]["controlNodeId"] == "button-1"


def test_ui_test_run_repairs_missing_screen_feedback_for_every_flow_screen():
    FakeRepository.ui_test = _test_record(
        scope_type="flow",
        source_data={
            "imageEntries": [
                {"id": "screen-1", "name": "가입 화면", "imageUrl": "/asset-1.png"},
                {"id": "screen-2", "name": "약관 화면", "imageUrl": "/asset-2.png"},
            ],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
            "flow_goal": "회원가입 완료",
        },
    )

    result = _service().run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    row = result.data["results"][0]
    assert {item["screenIndex"] for item in row["feedback"]["screenFeedbacks"]} == {0, 1}
    assert {item["screenIndex"] for item in row["flowAnalysis"]} == {0, 1}
    assert {item["screenIndex"] for item in row["scores"]["screenScores"]} == {0, 1}
    assert {item["screenId"] for item in row["scores"]["screenScores"]} == {"screen-1", "screen-2"}
    assert len(row["screenInsights"]) == 2
    assert row["confidence"]["screenCoverage"]["screens"] == 2


def test_ui_test_run_captures_url_entries_before_persona_feedback_generation():
    FakeRepository.ui_test = _test_record(
        source_type="url",
        source_data={
            "urlEntries": [{"id": "screen-1", "name": "랜딩", "url": "https://example.com"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
        },
    )

    result = _service(capture=FakeCapture(), storage=FakeStorage()).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status_code == 200
    updated_source = result.data["data"]["sourceData"]
    assert updated_source["urlEntries"][0]["capturedImageUrl"] == "/api/persona/storage/77"
    assert updated_source["urlEntries"][0]["pageTitle"] == "랜딩 페이지"


def test_ui_test_run_caches_figma_flow_screen_before_persona_feedback_generation():
    FakeRepository.ui_test = _test_record(
        source_type="figma",
        source_data={
            "figma_file": {
                "id": 1,
                "figma_file_key": "file-key",
                "figma_file_name": "Figma File",
                "figma_file_link": "https://www.figma.com/design/file-key/name",
            },
            "figma_flow": {
                "id": 2,
                "figma_start_node_id": "12:34",
                "figma_flow_name": "Signup Flow",
            },
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
        },
    )

    result = _service(figma_client=FakeFigmaClient(), storage=FakeStorage()).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status_code == 200
    updated_source = result.data["data"]["sourceData"]
    assert updated_source["figmaScreens"][0]["imageUrl"] == "/api/persona/storage/77"
    assert updated_source["figmaScreens"][0]["figmaNodeId"] == "12:34"
    assert updated_source["figmaScreens"][1]["figmaNodeId"] == "56:78"
    assert updated_source["screens"][0]["image_url"] == "/api/persona/storage/77"
    assert updated_source["figmaPreview"]["transitionCount"] == 1


def test_ui_test_run_normalizes_scheme_less_url_entries(monkeypatch):
    capture = PersonaCapture()

    def fake_playwright_capture(url):
        return {
            "url": url,
            "title": "랜딩 페이지",
            "status_code": 200,
            "content_type": "text/html",
            "screenshot_base64": "aW1hZ2U=",
            "capture_backend": "playwright",
        }

    monkeypatch.setattr(capture, "_capture_with_playwright", fake_playwright_capture)
    FakeRepository.ui_test = _test_record(
        source_type="url",
        source_data={
            "urlEntries": [{"id": "screen-1", "name": "랜딩", "url": "example.com/path?q=1"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
        },
    )

    result = _service(capture=capture, storage=FakeStorage()).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status_code == 200
    updated_entry = result.data["data"]["sourceData"]["urlEntries"][0]
    assert updated_entry["url"] == "https://example.com/path?q=1"
    assert updated_entry["capturedImageUrl"] == "/api/persona/storage/77"


def test_capture_url_normalizes_scheme_less_url_before_playwright(monkeypatch):
    capture = PersonaCapture()

    def fake_playwright_capture(url):
        return {
            "url": url,
            "title": "랜딩 페이지",
            "status_code": 200,
            "content_type": "text/html",
            "screenshot_base64": "aW1hZ2U=",
            "capture_backend": "playwright",
        }

    monkeypatch.setattr(capture, "_capture_with_playwright", fake_playwright_capture)

    result = _service(capture=capture, storage=FakeStorage()).capture_url(company_id=100, user_id=10, url="example.com")

    assert result.status_code == 200
    assert result.data["data"]["url"] == "https://example.com"
    assert result.data["data"]["capturedImageUrl"] == "/api/persona/storage/77"


def test_capture_url_rejects_unsupported_url_scheme():
    result = _service(capture=PersonaCapture(), storage=FakeStorage()).capture_url(company_id=100, user_id=10, url="ftp://example.com")

    assert result.status == "invalid"
    assert result.status_code == 400
    assert result.error == "URL must use http:// or https://"


def test_ui_test_run_fails_when_url_capture_has_no_image():
    FakeRepository.ui_test = _test_record(
        source_type="url",
        source_data={
            "urlEntries": [{"id": "screen-1", "name": "랜딩", "url": "https://example.com"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
        },
    )

    result = _service(capture=FakeCaptureWithoutScreenshot(), storage=FakeStorage()).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status == "capture_failed"
    assert result.status_code == 502
    assert FakeRepository.ui_test.status == "failed"
    assert FakeRepository.ui_test.error_message == "URL capture did not produce a screenshot image"


def test_capture_url_fails_when_capture_backend_has_no_screenshot():
    result = _service(capture=FakeCaptureWithoutScreenshot(), storage=FakeStorage()).capture_url(company_id=100, user_id=10, url="https://example.com")

    assert result.status == "capture_failed"
    assert result.status_code == 502
    assert result.error == "URL capture did not produce a screenshot image"


def test_ui_test_prompt_uses_rich_persona_context_and_flow_goal():
    FakeRepository.ui_test = _test_record(
        scope_type="flow",
        source_data={
            "imageEntries": [{"id": "screen-1", "name": "가입 화면", "imageUrl": "/asset.png"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
            "flow_goal": "회원가입 완료",
        },
    )
    adapter = FakeLlmAdapter()

    _service(llm_adapter=adapter).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    prompt = adapter.prompts[-1]
    assert "not as a generic UX reviewer" in prompt
    assert "Task/Flow Goal: 회원가입 완료" in prompt
    assert "Biography" in prompt
    assert "Social Context" in prompt


def test_ui_test_run_sends_screen_images_to_multimodal_llm_for_pin_coordinates(tmp_path):
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"fake image bytes")
    FakeStorage.local_paths = {"company-100/upload/screen.png": image_path}
    FakeRepository.asset = SimpleNamespace(
        id=88,
        company_id=100,
        storage_key="company-100/upload/screen.png",
        mime_type="image/png",
    )
    FakeRepository.ui_test = _test_record(
        source_data={
            "imageEntries": [{"id": "screen-1", "name": "가입 화면", "imageUrl": "/api/persona/storage/88"}],
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
        },
    )
    adapter = FakeLlmAdapter()

    result = _service(llm_adapter=adapter, storage=FakeStorage()).run_ui_test(company_id=100, user_id=10, test_id=1, data={})

    assert result.status_code == 200
    assert adapter.media_parts
    assert adapter.media_parts[-1][0]["type"] == "text"
    assert adapter.media_parts[-1][1]["type"] == "image"
    assert adapter.media_parts[-1][1]["screenIndex"] == 0
    assert result.data["results"][0]["confidence"]["screenCoverage"]["imageEvidenceScreens"] == 1


def test_ab_test_run_generates_winner_summary():
    FakeRepository.ab_test = _test_record(
        id=2,
        purpose="가입 화면 비교",
        service_context="가입",
        mode="single",
        screens=[{"version": "A", "name": "A안"}, {"version": "B", "name": "B안"}],
        transitions=None,
        context_data={"personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]}},
        enable_consistency_validation=False,
        consistency_run_count=3,
    )
    result = _service().run_ab_test(company_id=100, user_id=10, ab_test_id=2, data={})

    assert result.status_code == 200
    assert result.data["results"][0]["scores"]["winner"] == "A"
    assert result.data["data"]["summary"]["winner"] == "A"


def test_ab_test_uses_default_gemini_flash_model():
    FakeRepository.ab_test = _test_record(
        id=2,
        purpose="가입 화면 비교",
        service_context="가입",
        mode="single",
        screens=[{"version": "A", "name": "A안"}, {"version": "B", "name": "B안"}],
        transitions=None,
        context_data={"personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]}},
        enable_consistency_validation=False,
        consistency_run_count=3,
    )
    adapter = FakeLlmAdapter()

    result = _service(llm_adapter=adapter).run_ab_test(company_id=100, user_id=10, ab_test_id=2, data={})

    assert result.status_code == 200
    assert adapter.model_names == ["gemini-2.5-flash", "gemini-2.5-flash"]
    assert result.data["results"][0]["confidence"]["model"] == "gemini-2.5-flash"
    assert result.data["results"][0]["confidence"]["promptVersion"] == "ab_test_v4"
    assert result.data["data"]["summary"]["variantBrief"]["elementDifferences"]


def test_ab_test_run_sends_screen_images_once_for_analysis(tmp_path):
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"fake image bytes")
    FakeStorage.local_paths = {"company-100/upload/screen.png": image_path}
    FakeRepository.asset = SimpleNamespace(
        id=88,
        company_id=100,
        storage_key="company-100/upload/screen.png",
        mime_type="image/png",
    )
    FakeRepository.ab_test = _test_record(
        id=2,
        purpose="가입 화면 비교",
        service_context="가입",
        mode="single",
        screens=[
            {"key": "A", "name": "A안", "imageUrl": "/api/persona/storage/88"},
            {"key": "B", "name": "B안", "imageUrl": "/api/persona/storage/88"},
        ],
        transitions=None,
        context_data={
            "personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]},
            "source_data": {
                "variants": {
                    "A": [{"key": "A", "name": "A안", "imageUrl": "/api/persona/storage/88"}],
                    "B": [{"key": "B", "name": "B안", "imageUrl": "/api/persona/storage/88"}],
                }
            },
        },
        enable_consistency_validation=False,
        consistency_run_count=3,
    )
    adapter = FakeLlmAdapter()

    result = _service(llm_adapter=adapter, storage=FakeStorage()).run_ab_test(company_id=100, user_id=10, ab_test_id=2, data={})

    assert result.status_code == 200
    assert len(adapter.media_parts) == 1
    assert adapter.media_parts[0][0]["variant"] == "A"
    assert adapter.media_parts[0][1]["type"] == "image"
    assert adapter.media_parts[0][2]["variant"] == "B"
    assert "STAGE: ab_screen_analysis" in adapter.prompts[0]
    assert "STAGE: ab_persona_preference" in adapter.prompts[1]
    assert "UI inventory" in adapter.prompts[1]
    assert result.data["data"]["summary"]["variantBrief"]["analysisMeta"]["imageEvidenceCount"] == 2


def test_ab_flow_summary_skips_malformed_llm_journey_and_step_items():
    FakeRepository.ab_test = _test_record(
        id=2,
        purpose="가입 플로우 비교",
        service_context="가입",
        mode="flow",
        screens={"A": [{"name": "A 1단계"}], "B": [{"name": "B 1단계"}]},
        transitions=None,
        context_data={"personaSelection": {"useAllPersonas": False, "selectedPersonaIds": [20]}},
        enable_consistency_validation=False,
        consistency_run_count=3,
    )

    result = _service(llm_adapter=MalformedAbFlowAdapter()).run_ab_test(company_id=100, user_id=10, ab_test_id=2, data={})

    assert result.status_code == 200
    assert result.data["data"]["status"] == "completed"
    assert result.data["data"]["summary"]["winner"] == "A"
    assert result.data["data"]["summary"]["flowMetrics"]["avgFlowARating"] == 0
    assert result.data["data"]["summary"]["flowMetrics"]["stepPreferences"] == [
        {"stepIndex": 2, "voteA": 0, "voteB": 1, "voteTie": 0}
    ]
    assert result.data["results"][0]["scores"]["journeyComparison"] == {}
    assert result.data["results"][0]["scores"]["stepAnalysis"] == [{"stepIndex": 2, "preferredVersion": "B"}]


def test_interview_run_generates_turns():
    FakeRepository.interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"questions": [{"id": "q1", "text": "무엇을 확인하나요?"}]},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )
    FakeRepository.personas = None
    FakeRepository.pack_updates = []
    adapter = FakeLlmAdapter()

    result = _service(llm_adapter=adapter).run_interview(company_id=100, user_id=10, interview_id=3, data={})

    assert result.status_code == 200
    assert result.data["data"]["status"] == "completed"
    assert result.data["data"]["model"] == "gpt-5.4"
    assert result.data["data"]["packModel"] == "gemini-2.5-flash"
    assert result.data["data"]["questionSet"]["tasks"][0]["questions"][0] == "무엇을 확인하나요?"
    assert result.data["data"]["results"][0]["turns"][0]["answer"] == "금액과 조건을 먼저 봅니다."
    assert result.data["results"][0]["pack"]["identity"]["name"] == "김민수"
    assert FakeRepository.pack_updates[0]["version"] == "persona_interview_pack_v1"
    assert FakeRepository.pack_updates[0]["model"] == "gemini-2.5-flash"
    assert adapter.model_names == ["gemini-2.5-flash", "gpt-5.4"]


def test_interview_run_processes_personas_concurrently(monkeypatch):
    FakeRepository.interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"questions": [{"id": "q1", "text": "무엇을 확인하나요?"}]},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20, 21, 22, 23],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )
    FakeRepository.personas = [_persona_record(id=persona_id, name=f"김민수{persona_id}") for persona_id in [20, 21, 22, 23]]
    FakeRepository.pack_updates = []
    monkeypatch.setenv("PERSONA_INTERVIEW_MAX_CONCURRENCY", "3")
    adapter = ConcurrentInterviewAdapter()

    result = _service(llm_adapter=adapter).run_interview(company_id=100, user_id=10, interview_id=3, data={})

    assert result.status_code == 200
    assert result.data["data"]["status"] == "completed"
    assert len(result.data["results"]) == 4
    assert adapter.max_active_interviews > 1
    FakeRepository.personas = None


def test_generate_interview_questions_returns_canonical_shape():
    result = _service().generate_interview_questions(
        company_id=100,
        user_id=10,
        data={"goal": "가입 반응 확인", "productDescription": "가입 서비스", "length": "standard"},
    )

    questions = result.data["data"]["questions"]
    assert result.status_code == 200
    assert questions["opening"][0] == "평소 어떤 기준으로 가입 서비스를 살펴보시나요?"
    assert questions["tasks"][0]["title"] == "판단 기준"
    assert questions["closing"][0] == "마지막으로 꼭 개선되었으면 하는 점은 무엇인가요?"
    assert questions["followup_strategies"][0] == "망설임이 드러난 답변을 더 깊게 묻습니다."


def test_interview_questions_use_default_model_and_request_override():
    default_adapter = FakeLlmAdapter()
    default_result = _service(llm_adapter=default_adapter).generate_interview_questions(
        company_id=100,
        user_id=10,
        data={"goal": "가입 반응 확인", "productDescription": "가입 서비스", "length": "standard"},
    )
    override_adapter = FakeLlmAdapter()
    override_result = _service(llm_adapter=override_adapter).generate_interview_questions(
        company_id=100,
        user_id=10,
        data={"goal": "가입 반응 확인", "productDescription": "가입 서비스", "length": "standard", "model": "gemini:gemini-2.5-flash"},
    )

    assert default_result.status_code == 200
    assert override_result.status_code == 200
    assert default_adapter.model_names == ["gpt-5.4"]
    assert override_adapter.model_names == ["gemini-2.5-flash"]


def test_interview_pack_cache_hit_reuses_cached_pack_without_llm_call():
    persona = _persona_record()
    service = _service()
    model = service._default_model_for_stage("persona_interview_pack")
    source_text = service._persona_interview_source(object(), company_id=100, persona=persona, include_activities=False)
    cached_pack = {"identity": {"name": "cached"}, "source_persona_id": "20"}
    persona.interview_pack = cached_pack
    persona.interview_pack_source_hash = service._pack_source_hash(persona_text=source_text, model=model)
    persona.interview_pack_model = model
    persona.interview_pack_version = "persona_interview_pack_v2"
    adapter = FakeLlmAdapter()

    pack = _service(llm_adapter=adapter)._get_or_create_persona_interview_pack(
        object(),
        company_id=100,
        user_id=10,
        persona=persona,
        model=model,
    )

    assert pack == cached_pack
    assert not any("인터뷰 응답에 적합한 메모리로 정제" in prompt for prompt in adapter.prompts)


def test_interview_pack_cache_ignores_activity_history():
    persona = _persona_record()
    service = _service()
    model = service._default_model_for_stage("persona_interview_pack")
    profile_text = service._persona_interview_source(object(), company_id=100, persona=persona, include_activities=False)
    cached_pack = {"identity": {"name": "cached"}, "source_persona_id": "20"}
    persona.interview_pack = cached_pack
    persona.interview_pack_source_hash = service._pack_source_hash(persona_text=profile_text, model=model)
    persona.interview_pack_model = model
    persona.interview_pack_version = "persona_interview_pack_v2"
    adapter = FakeLlmAdapter()

    pack = service._get_or_create_persona_interview_pack(
        object(),
        company_id=100,
        user_id=10,
        persona=persona,
        model=model,
    )

    assert pack == cached_pack
    assert not adapter.prompts


def test_interview_run_records_failed_persona_without_failing_request():
    FakeRepository.interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"opening": ["평소 기준은 무엇인가요?"], "tasks": [], "closing": [], "followup_strategies": []},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )

    result = _service(llm_adapter=FailingInterviewAdapter()).run_interview(company_id=100, user_id=10, interview_id=3, data={})

    assert result.status_code == 200
    assert result.data["data"]["status"] == "completed_with_errors"
    assert result.data["data"]["results"][0]["status"] == "failed"
    assert "interview failed" in result.data["data"]["results"][0]["error"]
    assert "2회 재시도했지만 실패했습니다" in result.data["data"]["results"][0]["errorMessage"]


def test_interview_run_retries_failed_persona_before_recording_result(monkeypatch):
    FakeRepository.interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"opening": ["평소 기준은 무엇인가요?"], "tasks": [], "closing": [], "followup_strategies": []},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )
    monkeypatch.setenv("PERSONA_INTERVIEW_RETRY_ATTEMPTS", "2")
    adapter = TransientInterviewAdapter(failures_before_success=2)

    result = _service(llm_adapter=adapter).run_interview(company_id=100, user_id=10, interview_id=3, data={})

    assert result.status_code == 200
    assert result.data["data"]["status"] == "completed"
    assert result.data["data"]["results"][0]["status"] == "completed"
    assert result.data["data"]["results"][0]["turns"][0]["answer"] == "금액과 조건을 먼저 봅니다."
    assert result.data["data"]["results"][0]["rawResponse"]["retryAttempts"] == 2
    assert adapter.interview_calls == 3


def test_interview_run_does_not_replace_malformed_response_with_generic_answers():
    FakeRepository.interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"opening": ["평소 기준은 무엇인가요?"], "tasks": [], "closing": [], "followup_strategies": []},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )

    result = _service(llm_adapter=MalformedInterviewAdapter()).run_interview(company_id=100, user_id=10, interview_id=3, data={})

    assert result.status_code == 200
    assert result.data["data"]["status"] == "completed_with_errors"
    assert result.data["data"]["results"][0]["status"] == "failed"
    assert result.data["data"]["results"][0]["turns"] == []
    assert "2회 재시도했지만 실패했습니다" in result.data["data"]["results"][0]["errorMessage"]
    assert "제 상황에서는 근거와 다음 행동" not in json.dumps(result.data, ensure_ascii=False)


def _flow_scoring_event(screen_index, metric="혼란도", polarity="negative", severity=4):
    return {
        "testType": "flow",
        "metric": metric,
        "subMetric": "직관성",
        "targetElement": "CTA",
        "matchedKeyElement": None,
        "polarity": polarity,
        "severity": severity,
        "elementImportance": 1.0,
        "personaRelevance": 4,
        "confidence": 0.85,
        "mappingRole": "primary",
        "impactMultiplier": 1.0,
        "screenIndex": screen_index,
        "stepIndex": screen_index,
        "reason": "test",
        "sourceComment": "test comment",
    }


def test_normalize_ui_scoring_analysis_separates_flow_and_screen_metrics():
    flow_payload = {
        "analysisEvents": [
            {
                "testType": "screen",
                "metric": "명확성",
                "subMetric": "직관성",
                "targetElement": "CTA",
                "polarity": "negative",
                "severity": 3,
                "elementImportance": 1.0,
                "personaRelevance": 3,
                "confidence": 0.8,
                "mappingRole": "primary",
                "impactMultiplier": 1.0,
                "screenIndex": 0,
                "stepIndex": 0,
                "reason": "ignored",
                "sourceComment": "screen comment",
            },
            {
                "testType": "flow",
                "metric": "혼란도",
                "subMetric": "직관성",
                "targetElement": "CTA",
                "polarity": "negative",
                "severity": 4,
                "elementImportance": 1.0,
                "personaRelevance": 4,
                "confidence": 0.85,
                "mappingRole": "primary",
                "impactMultiplier": 1.0,
                "screenIndex": 0,
                "stepIndex": 0,
                "reason": "kept",
                "sourceComment": "flow comment",
            },
        ]
    }
    flow_events = _normalize_ui_scoring_analysis(flow_payload, is_flow_test=True)["analysisEvents"]
    assert len(flow_events) == 1
    assert flow_events[0]["testType"] == "flow"
    assert flow_events[0]["metric"] == "혼란도"

    screen_payload = {
        "analysisEvents": [
            {
                "testType": "flow",
                "metric": "혼란도",
                "subMetric": "직관성",
                "targetElement": "CTA",
                "polarity": "negative",
                "severity": 4,
                "elementImportance": 1.0,
                "personaRelevance": 4,
                "confidence": 0.85,
                "mappingRole": "primary",
                "impactMultiplier": 1.0,
                "screenIndex": 0,
                "stepIndex": 0,
                "reason": "ignored",
                "sourceComment": "flow comment",
            },
            {
                "testType": "screen",
                "metric": "명확성",
                "subMetric": "직관성",
                "targetElement": "CTA",
                "polarity": "negative",
                "severity": 3,
                "elementImportance": 1.0,
                "personaRelevance": 3,
                "confidence": 0.8,
                "mappingRole": "primary",
                "impactMultiplier": 1.0,
                "screenIndex": 0,
                "stepIndex": None,
                "reason": "kept",
                "sourceComment": "screen comment",
            },
        ]
    }
    screen_events = _normalize_ui_scoring_analysis(screen_payload, is_flow_test=False)["analysisEvents"]
    assert len(screen_events) == 1
    assert screen_events[0]["testType"] == "screen"
    assert screen_events[0]["metric"] == "명확성"


def test_flow_completion_score_uses_structured_flow_analysis_steps():
    screens = [{"id": "screen-1", "name": "A"}, {"id": "screen-2", "name": "B"}]
    events = [_flow_scoring_event(0, severity=4)]
    flow_analysis = _service()._normalize_ui_flow_analysis(
        feedback={"flowAnalysis": [{"screenIndex": 0, "confusionScore": 10, "dropoffRisk": 10}]},
        screens=screens,
        is_flow=True,
    )
    flow_analysis = _apply_structured_flow_analysis_scores(flow_analysis=flow_analysis, analysis_events=events)

    legacy_score = _score_flow_completion_metric(events, total_step_count=2)
    unified_score = _score_flow_completion_from_flow_analysis(flow_analysis, events, total_step_count=2)
    structured = _apply_structured_scoring(
        fallback_scores={"clarity": 70, "usability": 70, "appeal": 65, "overall": 68, "overallFlowScore": None},
        analysis_events=events,
        is_flow_test=True,
        flow_step_count=2,
        flow_analysis=flow_analysis,
    )

    assert legacy_score != unified_score
    assert structured["overallFlowScore"] == unified_score
    assert flow_analysis[1]["confusionScore"] == 35
    assert flow_analysis[1]["dropoffRisk"] == 30

    card_confusion = _aggregate_flow_step_risk([item["confusionScore"] for item in flow_analysis])
    card_dropoff = _aggregate_flow_step_risk([item["dropoffRisk"] for item in flow_analysis])
    failure_risk = card_confusion * 0.45 + card_dropoff * 0.55
    risk_ceiling = max(0, min(99 if failure_risk > 0 else 100, 100 - failure_risk * 0.65))
    assert unified_score <= risk_ceiling


def test_detail_payloads_embed_results_and_original_camel_case_aliases():
    FakeRepository.interview = SimpleNamespace(
        id=3,
        company_id=100,
        created_by_user_id=10,
        name="가입 인터뷰",
        goal="가입 반응 확인",
        product_description="가입 서비스",
        length="quick",
        question_set={"questions": [{"id": "q1", "text": "무엇을 확인하나요?"}]},
        model=None,
        pack_model=None,
        status="draft",
        progress=0,
        persona_ids=[20],
        summary=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )
    service = _service()

    ui = service.get_ui_test(company_id=100, test_id=1).data["data"]
    ab = service.get_ab_test(company_id=100, ab_test_id=2).data["data"]
    interview = service.get_interview(company_id=100, interview_id=3).data["data"]

    assert ui["deviceType"] == "pc"
    assert ui["sourceData"]["imageEntries"][0]["id"] == "screen-1"
    assert ui["results"][0]["personaGoalFit"] == "목표 수행 가능"
    assert ui["results"][0]["evidenceIds"] == ["promptVersion:persona_test_v2"]
    assert ab["serviceContext"] == "가입"
    assert ab["contextData"]["personaSelection"]["selectedPersonaIds"] == [20]
    assert ab["results"][0]["testId"] == 2
    assert ab["results"][0]["personaName"] == "김민수"
    assert interview["productDescription"] == "가입 서비스"
    assert interview["questionSet"]["tasks"][0]["questions"][0] == "무엇을 확인하나요?"
    assert interview["results"][0]["interviewId"] == 3
    assert interview["results"][0]["error"] is None


def test_comment_score_uses_severity_only_per_design_doc():
    assert _get_comment_score(
        {
            "testType": "screen",
            "metric": "명확성",
            "polarity": "positive",
            "severity": 2,
        }
    ) == 74
    assert _get_comment_score(
        {
            "testType": "screen",
            "metric": "명확성",
            "polarity": "negative",
            "severity": 3,
        }
    ) == 26


def test_apply_structured_screen_scores_uses_events_or_ai_base():
    events = [
        {
            "testType": "screen",
            "metric": "만족도",
            "polarity": "positive",
            "severity": 3,
            "elementImportance": 1.0,
            "personaRelevance": 3,
            "confidence": 0.8,
            "impactMultiplier": 1.0,
            "screenIndex": 0,
        }
    ]
    screen_scores = [{"screenIndex": 0, "clarity": 72, "usability": 68, "appeal": 78}]
    updated = _apply_structured_screen_scores(screen_scores=screen_scores, analysis_events=events)
    assert updated[0]["clarity"] == 72
    assert updated[0]["usability"] == 68
    assert updated[0]["appeal"] == 86
