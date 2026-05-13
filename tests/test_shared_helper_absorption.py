from flask import Flask, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required

from reopsai.application.keywords import _refine_extracted_keywords
from reopsai.shared.idempotency import (
    _complete_idempotency_entry,
    _fail_idempotency_entry,
    _idempotency_cache,
    _reserve_idempotency_entry,
    _respond_from_entry,
)
from reopsai.shared.llm import _safe_parse_json_object, parse_llm_json_response
from reopsai.shared.request import _extract_request_user_id


def test_llm_json_helpers_parse_fenced_and_repair_escaped_json():
    fenced = parse_llm_json_response({"content": '```json\n{"name": "Study"}\n```'})
    assert fenced == {"name": "Study"}

    repaired = parse_llm_json_response({"content": '{"pattern": "\\s+"}'})
    assert repaired == {"pattern": "\\s+"}

    assert _safe_parse_json_object('prefix ```json\n{"ok": true}\n``` suffix') == {"ok": True}
    assert _safe_parse_json_object("no json") is None


def test_keyword_refinement_removes_stopwords_duplicates_and_preserves_order():
    assert _refine_extracted_keywords(
        ["연구", "Checkout", "checkout", "UX"],
        ["survey", "UX"],
    ) == ["Checkout", "UX", "survey"]


def test_idempotency_helpers_reserve_complete_fail_and_respond():
    _idempotency_cache.clear()
    app = Flask(__name__)

    with app.app_context():
        entry, is_new = _reserve_idempotency_entry("key-1")
        assert is_new is True

        same_entry, is_new = _reserve_idempotency_entry("key-1")
        assert same_entry is entry
        assert is_new is False

        _complete_idempotency_entry("key-1", {"success": True}, 201)
        response, status = _respond_from_entry(entry)
        assert status == 201
        assert response.get_json() == {"success": True}

        failed_entry, _ = _reserve_idempotency_entry("key-2")
        _fail_idempotency_entry("key-2", {"success": False, "error": "boom"}, 503)
        response, status = _respond_from_entry(failed_entry)
        assert status == 503
        assert response.get_json() == {"success": False, "error": "boom"}


def test_request_user_id_helper_uses_header_then_jwt_identity():
    app = Flask(__name__)
    app.config.update(
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
    )
    JWTManager(app)

    @app.get("/header")
    def header_id():
        user_id, err_body, err_status = _extract_request_user_id()
        return jsonify({"user_id": user_id, "has_error": err_body is not None, "err_status": err_status})

    @app.get("/jwt")
    @jwt_required()
    def jwt_id():
        user_id, err_body, err_status = _extract_request_user_id()
        return jsonify({"user_id": user_id, "has_error": err_body is not None, "err_status": err_status})

    client = app.test_client()
    assert client.get("/header", headers={"X-User-ID": "42"}).get_json() == {
        "user_id": 42,
        "has_error": False,
        "err_status": None,
    }

    with app.app_context():
        token = create_access_token(identity="43")
    assert client.get("/jwt", headers={"Authorization": f"Bearer {token}"}).get_json() == {
        "user_id": 43,
        "has_error": False,
        "err_status": None,
    }


def test_root_utils_wrappers_reexport_absorbed_helpers():
    from reopsai.application import keywords as absorbed_keywords
    from reopsai.shared import idempotency as absorbed_idempotency
    from reopsai.shared import llm as absorbed_llm
    from reopsai.shared import request as absorbed_request
    from utils import idempotency as legacy_idempotency
    from utils import keyword_utils as legacy_keywords
    from utils import llm_utils as legacy_llm
    from utils import request_utils as legacy_request

    assert legacy_request._extract_request_user_id is absorbed_request._extract_request_user_id
    assert legacy_idempotency._reserve_idempotency_entry is absorbed_idempotency._reserve_idempotency_entry
    assert legacy_llm.parse_llm_json_response is absorbed_llm.parse_llm_json_response
    assert legacy_keywords._refine_extracted_keywords is absorbed_keywords._refine_extracted_keywords
