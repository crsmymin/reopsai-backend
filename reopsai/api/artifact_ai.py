"""
Artifact AI 수정 관련 API 라우트
텍스트 선택 수정 기능을 위한 엔드포인트
"""

import traceback

from flask import Blueprint, jsonify, request

from reopsai.application.artifact_ai_service import artifact_ai_service
from reopsai.shared.auth import tier_required
from reopsai.shared.request import _extract_request_user_id, _resolve_workspace_owner_ids

artifact_ai_bp = Blueprint("artifact_ai", __name__, url_prefix="/api")


def _get_owner_ids_for_request(user_id_int):
    return [str(owner_id) for owner_id in _resolve_workspace_owner_ids(user_id_int)]


def _artifact_error_response(result):
    if result.status == "db_unavailable":
        return jsonify({"success": False, "error": "데이터베이스 연결 실패"}), 500
    if result.status == "not_found":
        return jsonify({"success": False, "error": "아티팩트를 찾을 수 없습니다."}), 404
    if result.status == "forbidden":
        return jsonify({"success": False, "error": "접근 권한이 없습니다."}), 403
    return None


@artifact_ai_bp.route("/artifacts/<int:artifact_id>/edit_history", methods=["GET"])
@tier_required(["free"])
def list_artifact_edit_history(artifact_id):
    """아티팩트 수정 히스토리(적용 전/후 스냅샷) 목록 조회"""
    try:
        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        limit = request.args.get("limit", "50")
        try:
            limit = max(1, min(200, int(limit)))
        except Exception:
            limit = 50

        result = artifact_ai_service.list_edit_history(
            artifact_id=artifact_id,
            owner_ids=_get_owner_ids_for_request(user_id_int),
            limit=limit,
        )
        error_response = _artifact_error_response(result)
        if error_response:
            return error_response
        return jsonify({"success": True, "history": result.data}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@artifact_ai_bp.route("/artifacts/<int:artifact_id>/edit_history", methods=["POST"])
@tier_required(["free"])
def create_artifact_edit_history(artifact_id):
    """아티팩트 수정 히스토리(적용 전/후 스냅샷) 저장"""
    try:
        data = request.json or {}
        before_markdown = (data.get("before_markdown") or "").strip()
        after_markdown = (data.get("after_markdown") or "").strip()
        prompt = (data.get("prompt") or "").strip()
        source = (data.get("source") or "").strip()
        selection_from = data.get("selection_from")
        selection_to = data.get("selection_to")

        if before_markdown == "" or after_markdown == "":
            return jsonify({"success": False, "error": "before_markdown / after_markdown가 필요합니다."}), 400

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        result = artifact_ai_service.create_edit_history(
            artifact_id=artifact_id,
            owner_ids=_get_owner_ids_for_request(user_id_int),
            user_id=user_id_int,
            before_markdown=before_markdown,
            after_markdown=after_markdown,
            prompt=prompt,
            source=source,
            selection_from=selection_from,
            selection_to=selection_to,
        )
        error_response = _artifact_error_response(result)
        if error_response:
            return error_response
        return jsonify({"success": True, "history": result.data}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@artifact_ai_bp.route("/artifacts/<int:artifact_id>/modify", methods=["POST"])
@tier_required(["free"])
def modify_artifact_text(artifact_id):
    """
    선택된 텍스트를 AI로 수정하는 엔드포인트
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "요청 데이터가 필요합니다."}), 400

        selected_text = data.get("selected_text", "").strip()
        user_prompt = (data.get("user_prompt") or data.get("modification_prompt") or "").strip()
        full_context = data.get("full_context", "").strip()
        selected_markdown_hint = (data.get("selected_markdown_hint") or "").strip()

        if not selected_text:
            return jsonify({"success": False, "error": "selected_text가 필요합니다."}), 400
        if not user_prompt:
            return jsonify({"success": False, "error": "user_prompt가 필요합니다."}), 400

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        result = artifact_ai_service.modify_artifact_text(
            artifact_id=artifact_id,
            owner_ids=_get_owner_ids_for_request(user_id_int),
            user_id=user_id_int,
            selected_text=selected_text,
            user_prompt=user_prompt,
            full_context=full_context,
            selected_markdown_hint=selected_markdown_hint,
        )
        error_response = _artifact_error_response(result)
        if error_response:
            return error_response
        if result.status == "llm_failed":
            return jsonify({"success": False, "error": result.error}), 500
        if result.status == "incomplete_response":
            return jsonify({"success": False, "error": result.error}), 500
        if result.status == "partial_response":
            return jsonify({"success": False, "error": result.error}), 200

        return jsonify({"success": True, **result.data})

    except Exception as e:
        print(f"[ERROR] modify_artifact_text 예외 발생: artifact_id={artifact_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": f"서버 오류가 발생했습니다: {str(e)}"}), 500
