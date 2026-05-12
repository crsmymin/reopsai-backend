"""
개발 전용 산출물 평가 API (범용 테스트기 판때기).
FLASK_ENV=development 일 때만 사용. plan / survey / guideline / report 등 확장 가능.
"""
import os
from flask import Blueprint, request, jsonify

from reopsai_backend.application.dev_evaluator_service import dev_evaluator_service

dev_evaluator_bp = Blueprint("dev_evaluator", __name__, url_prefix="/api/dev")


def _is_dev():
    return os.getenv("FLASK_ENV") == "development"


@dev_evaluator_bp.route("/evaluate", methods=["POST"])
def evaluate():
    """산출물 평가 실행. body: artifact_type, stage, payload, criteria"""
    if not _is_dev():
        return jsonify({"success": False, "error": "개발 환경에서만 사용 가능합니다."}), 404

    data = request.get_json() or {}
    artifact_type = (data.get("artifact_type") or "plan").strip().lower()
    stage = data.get("stage")
    payload = data.get("payload")
    criteria = data.get("criteria")
    evaluation_mode = (data.get("evaluation_mode") or "").strip() or None

    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "payload는 객체여야 합니다."}), 400
    if criteria is None:
        criteria = []
    if not isinstance(criteria, list):
        return jsonify({"success": False, "error": "criteria는 배열이어야 합니다."}), 400

    result = dev_evaluator_service.evaluate(
        artifact_type=artifact_type,
        stage=stage,
        payload=payload,
        criteria=criteria,
        evaluation_mode=evaluation_mode,
    )
    if result.status == "invalid_artifact_id":
        return jsonify({"success": False, "error": result.error}), 400
    if result.status != "ok":
        return jsonify(result.data), 400
    return jsonify(result.data), 200
