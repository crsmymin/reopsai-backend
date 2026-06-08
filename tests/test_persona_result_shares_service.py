from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from reopsai.application import persona_service as persona_service_module
from reopsai.application.persona_service import PersonaService


def _now():
    return datetime.now(timezone.utc)


def _ui_test_record(**overrides):
    data = {
        "id": 38,
        "company_id": 100,
        "created_by_user_id": 10,
        "name": "가입 화면 테스트",
        "description": "",
        "device_type": "pc",
        "validation_type": "single",
        "scope_type": "screen",
        "source_type": "image",
        "status": "completed",
        "progress": 100,
        "error_message": None,
        "persona_count": 1,
        "screen_count": 1,
        "summary": {},
        "source_data": {"imageEntries": [{"name": "화면 1", "imageUrl": "/api/persona/storage/88"}]},
        "started_at": None,
        "completed_at": _now(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _ab_test_record(**overrides):
    data = {
        "id": 39,
        "company_id": 100,
        "created_by_user_id": 10,
        "name": "가입 A/B 테스트",
        "purpose": "비교",
        "service_context": "",
        "mode": "single",
        "screens": [{"version": "A", "imageUrl": "/api/persona/storage/88"}],
        "transitions": None,
        "context_data": {},
        "summary": {},
        "status": "completed",
        "progress": 100,
        "error_message": None,
        "enable_consistency_validation": False,
        "consistency_run_count": 3,
        "created_at": _now(),
        "updated_at": _now(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _interview_record(**overrides):
    data = {
        "id": 40,
        "company_id": 100,
        "created_by_user_id": 10,
        "name": "가입 인터뷰",
        "goal": "가입 반응 확인",
        "product_description": "",
        "length": "standard",
        "question_set": {"tasks": []},
        "model": "gpt-5.4",
        "pack_model": "gemini-2.5-flash",
        "status": "completed",
        "progress": 100,
        "persona_ids": [20],
        "summary": {},
        "error_message": None,
        "started_at": None,
        "completed_at": _now(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeShareRepository:
    shares = []
    ui_test = _ui_test_record()
    ab_test = _ab_test_record()
    interview = _interview_record()
    asset = SimpleNamespace(id=88, company_id=100, storage_key="company-100/ui/screen.png", mime_type="image/png", original_filename="screen.png")

    @classmethod
    def reset(cls):
        cls.shares = []
        cls.ui_test = _ui_test_record()
        cls.ab_test = _ab_test_record()
        cls.interview = _interview_record()

    @staticmethod
    def _visible(record, *, company_id, resource_id, user_id=None):
        if not record or record.company_id != company_id or record.id != int(resource_id):
            return None
        if user_id is not None and record.created_by_user_id != user_id:
            return None
        return record

    @classmethod
    def get_ui_test(cls, session, *, company_id, test_id, user_id=None):
        return cls._visible(cls.ui_test, company_id=company_id, resource_id=test_id, user_id=user_id)

    @classmethod
    def get_ab_test(cls, session, *, company_id, ab_test_id, user_id=None):
        return cls._visible(cls.ab_test, company_id=company_id, resource_id=ab_test_id, user_id=user_id)

    @classmethod
    def get_interview(cls, session, *, company_id, interview_id, user_id=None):
        return cls._visible(cls.interview, company_id=company_id, resource_id=interview_id, user_id=user_id)

    @classmethod
    def get_active_result_share(cls, session, *, company_id, resource_type, resource_id):
        return next(
            (
                row for row in cls.shares
                if row.company_id == company_id
                and row.resource_type == resource_type
                and row.resource_id == int(resource_id)
                and row.revoked_at is None
            ),
            None,
        )

    @classmethod
    def get_result_share_by_hash(cls, session, *, token_hash):
        return next((row for row in cls.shares if row.token_hash == token_hash and row.revoked_at is None), None)

    @classmethod
    def create_result_share(cls, session, *, company_id, user_id, data):
        row = SimpleNamespace(
            id=len(cls.shares) + 1,
            company_id=company_id,
            resource_type=data["resource_type"],
            resource_id=int(data["resource_id"]),
            token_hash=data["token_hash"],
            token_salt=data["token_salt"],
            created_by_user_id=user_id,
            created_at=_now(),
            expires_at=data.get("expires_at"),
            revoked_at=None,
        )
        cls.shares.append(row)
        return row

    @staticmethod
    def revoke_result_share(session, share):
        share.revoked_at = _now()
        return share

    @staticmethod
    def list_ui_test_results(session, *, company_id, test_id):
        return [
            SimpleNamespace(
                id=1,
                test_id=test_id,
                persona_id=20,
                status="completed",
                summary="좋습니다.",
                persona_goal_fit="적합",
                scores={},
                feedback={},
                pin_comments=[],
                flow_analysis=[],
                persona_snapshot={"id": 20, "name": "김민수", "imageUrl": "/api/persona/storage/88"},
                confidence={},
                evidence_ids=[],
                strengths=[],
                risks=[],
                recommendations=[],
                screen_insights=[],
                evidence=None,
                raw_response=None,
                error_message=None,
                created_at=_now(),
                updated_at=_now(),
            )
        ]

    @staticmethod
    def list_ab_test_results(session, *, company_id, ab_test_id):
        return []

    @staticmethod
    def list_interview_results(session, *, company_id, interview_id):
        return []

    @classmethod
    def get_asset(cls, session, *, company_id, asset_id):
        if company_id == cls.asset.company_id and int(asset_id) == cls.asset.id:
            return cls.asset
        return None


class FakeStorage:
    @staticmethod
    def read_asset_bytes(asset):
        return SimpleNamespace(bytes=b"image", mime_type=asset.mime_type, source_backend="local")


@contextmanager
def fake_session_factory():
    yield SimpleNamespace(rollback=lambda: None)


def _service():
    return PersonaService(repository=FakeShareRepository, session_factory=fake_session_factory, storage=FakeStorage())


def test_create_share_reuses_active_link_without_storing_plain_token(monkeypatch):
    FakeShareRepository.reset()
    monkeypatch.setattr(persona_service_module.Config, "FRONTEND_URL", "https://app.example.com")
    monkeypatch.setattr(persona_service_module.Config, "JWT_SECRET_KEY", "test-secret-key-with-at-least-32-bytes")

    first = _service().create_result_share(company_id=100, user_id=10, data={"resourceType": "ui_test", "resourceId": 38})
    second = _service().create_result_share(company_id=100, user_id=10, data={"resourceType": "ui_test", "resourceId": 38})

    assert first.status_code == 201
    assert second.data["data"]["token"] == first.data["data"]["token"]
    assert second.data["data"]["shareUrl"].startswith("https://app.example.com/tests/ui/shareview/")
    assert len(FakeShareRepository.shares) == 1
    assert not hasattr(FakeShareRepository.shares[0], "token")


def test_shared_result_is_public_and_rewrites_storage_urls(monkeypatch):
    FakeShareRepository.reset()
    monkeypatch.setattr(persona_service_module.Config, "JWT_SECRET_KEY", "test-secret-key-with-at-least-32-bytes")
    created = _service().create_result_share(company_id=100, user_id=10, data={"resourceType": "ui_test", "resourceId": 38})

    result = _service().get_shared_result(token=created.data["data"]["token"])

    assert result.status_code == 200
    payload = result.data["data"]["result"]
    assert payload["sourceData"]["imageEntries"][0]["imageUrl"].startswith("/api/persona/share-links/")
    assert payload["results"][0]["personaSnapshot"]["imageUrl"].startswith("/api/persona/share-links/")


def test_shared_asset_requires_asset_reference(monkeypatch):
    FakeShareRepository.reset()
    monkeypatch.setattr(persona_service_module.Config, "JWT_SECRET_KEY", "test-secret-key-with-at-least-32-bytes")
    created = _service().create_result_share(company_id=100, user_id=10, data={"resourceType": "ui_test", "resourceId": 38})
    token = created.data["data"]["token"]

    allowed = _service().get_shared_asset(token=token, asset_id=88)
    denied = _service().get_shared_asset(token=token, asset_id=99)

    assert allowed.status_code == 200
    assert allowed.data["content"].bytes == b"image"
    assert denied.status_code == 404


def test_share_link_revoke_blocks_public_lookup(monkeypatch):
    FakeShareRepository.reset()
    monkeypatch.setattr(persona_service_module.Config, "JWT_SECRET_KEY", "test-secret-key-with-at-least-32-bytes")
    created = _service().create_result_share(company_id=100, user_id=10, data={"resourceType": "interview", "resourceId": 40})
    token = created.data["data"]["token"]

    revoked = _service().revoke_result_share(company_id=100, user_id=10, token=token)
    result = _service().get_shared_result(token=token)

    assert revoked.status_code == 200
    assert result.status_code == 404
