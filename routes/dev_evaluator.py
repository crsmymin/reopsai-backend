"""
개발 전용 산출물 평가 API (범용 테스트기 판때기).
FLASK_ENV=development 일 때만 사용. plan / survey / guideline / report 등 확장 가능.
"""
import os
from flask import Blueprint, request, jsonify
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import Artifact
from services.dev_evaluator_service import run_evaluation

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

    # plan + final + artifact_id → DB에서 content 조회
    if artifact_type == "plan" and stage == "final" and payload.get("artifact_id"):
        aid = payload.get("artifact_id")
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "artifact_id는 숫자여야 합니다."}), 400
        if session_scope is not None:
            try:
                with session_scope() as db_session:
                    artifact = db_session.execute(
                        select(Artifact).where(Artifact.id == aid).limit(1)
                    ).scalar_one_or_none()
                    if artifact:
                        payload = {**payload, "content": (artifact.content or "")}
            except Exception:
                # 개발 전용 API이므로 조회 실패 시 기존 payload 그대로 진행
                pass

    result = run_evaluation(
        artifact_type, stage, payload, criteria, evaluation_mode
    )
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result), 200
