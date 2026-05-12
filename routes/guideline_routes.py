"""
가이드라인 생성 Blueprint.

app.py에서 분리됨. URL prefix: /api
"""
import threading
import traceback

from flask import Blueprint, jsonify, request

from reopsai_backend.application.guideline_service import guideline_service
from reopsai_backend.shared.auth import tier_required
from utils.usage_metering import build_llm_usage_context, run_with_llm_usage_context

guideline_bp = Blueprint("guideline", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# 가이드라인 엔드포인트
# ---------------------------------------------------------------------------

@guideline_bp.route("/guideline/extract-methods", methods=["POST"])
@tier_required(["free"])
def guideline_extract_methods():
    try:
        data = request.json or {}
        research_plan = data.get("research_plan", "")
        result = guideline_service.extract_methods(
            research_plan=research_plan,
            temperature=0.0,
            require_success=False,
        )
        if result.status == "llm_failed":
            return jsonify({"success": False, "error": "LLM 응답 실패"}), 500
        return jsonify({"success": True, "methodologies": result.data["methodologies"]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@guideline_bp.route("/extract-methodologies", methods=["POST"])
@tier_required(["free"])
def extract_methodologies():
    """계획서에서 방법론 추출"""
    try:
        data = request.json or {}
        research_plan = data.get("research_plan", "")

        if not research_plan:
            return jsonify({"success": False, "error": "계획서가 비어있습니다"}), 400

        result = guideline_service.extract_methods(
            research_plan=research_plan,
            temperature=0.2,
            require_success=True,
        )
        if result.status == "llm_failed":
            return jsonify({"success": False, "error": "LLM 응답 실패"}), 500
        return jsonify({"success": True, "methodologies": result.data["methodologies"]})

    except Exception as e:
        print(f"[ERROR] 방법론 추출 실패: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@guideline_bp.route("/guideline/create-and-generate", methods=["POST"])
@tier_required(["free"])
def guideline_create_and_generate():
    """가이드라인 artifact 생성 + 백그라운드 생성"""
    try:
        data = request.json or {}
        study_id = data.get("study_id")
        research_plan = data.get("research_plan", "")
        methodologies = data.get("methodologies", [])

        try:
            study_id_int = int(study_id)
        except Exception:
            return jsonify({"success": False, "error": "유효하지 않은 study_id입니다."}), 400

        result = guideline_service.create_guideline_generation(study_id=study_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결 실패"}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "연구를 찾을 수 없습니다"}), 404
        if result.status == "project_not_found":
            return jsonify({"success": False, "error": "프로젝트 정보를 찾을 수 없습니다"}), 404

        artifact_id = result.data["artifact_id"]
        project_keywords = result.data["project_keywords"]
        llm_usage_context = build_llm_usage_context(feature_key="guideline_generation")

        thread = threading.Thread(
            target=lambda: run_with_llm_usage_context(
                llm_usage_context,
                guideline_service.generate_guideline_background,
                artifact_id=artifact_id,
                research_plan=research_plan,
                methodologies=methodologies,
                project_keywords=project_keywords,
            )
        )
        thread.start()

        return jsonify({"success": True, "artifact_id": artifact_id})

    except Exception as e:
        print(f"[ERROR] Guideline artifact 생성 실패: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
