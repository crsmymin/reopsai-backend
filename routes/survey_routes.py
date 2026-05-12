"""
설문 진단/생성 Blueprint.

app.py에서 분리됨. URL prefix: /api
"""
import threading
import traceback

from flask import Blueprint, jsonify, request

from reopsai_backend.application.survey_service import survey_service
from reopsai_backend.shared.auth import tier_required
from utils.usage_metering import build_llm_usage_context, run_with_llm_usage_context

survey_bp = Blueprint("survey", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# 설문 진단 엔드포인트
# ---------------------------------------------------------------------------

@survey_bp.route("/survey-diagnoser/diagnose", methods=["POST"])
@tier_required(["free"])
def survey_diagnoser_diagnose():
    try:
        data = request.json or {}
        result = survey_service.diagnose_survey(survey_text=data.get("survey_text", ""))
        return jsonify({"success": True, "response": result.data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@survey_bp.route("/survey-diagnoser/generate-draft", methods=["POST"])
@tier_required(["free"])
def survey_diagnoser_generate_draft():
    try:
        data = request.json or {}
        result = survey_service.generate_draft(
            survey_text=data.get("survey_text", ""),
            item_to_fix=data.get("item_to_fix", ""),
        )
        return jsonify({"success": True, "response": result.data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@survey_bp.route("/survey-diagnoser/polish-plan", methods=["POST"])
@tier_required(["free"])
def survey_diagnoser_polish_plan():
    try:
        data = request.json or {}
        result = survey_service.polish_plan(
            survey_text=data.get("survey_text", ""),
            confirmed_survey=data.get("confirmed_survey", {}),
        )
        return jsonify({"success": True, "response": result.data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# 스크리너(설문) 생성 엔드포인트
# ---------------------------------------------------------------------------

@survey_bp.route("/survey/create-and-generate", methods=["POST"])
@tier_required(["free"])
def survey_create_and_generate():
    """스크리너(설문) artifact 생성 + 백그라운드 생성"""
    try:
        data = request.json or {}
        study_id = data.get("study_id")
        research_plan = data.get("research_plan", "")

        try:
            study_id_int = int(study_id)
        except Exception:
            return jsonify({"success": False, "error": "유효하지 않은 study_id입니다."}), 400

        result = survey_service.create_survey_generation(study_id=study_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결 실패"}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "연구를 찾을 수 없습니다"}), 404
        if result.status == "project_not_found":
            return jsonify({"success": False, "error": "프로젝트 정보를 찾을 수 없습니다"}), 404

        artifact_id = result.data["artifact_id"]
        if artifact_id is None:
            return jsonify({"success": False, "error": "스크리너 저장소 생성에 실패했습니다."}), 500

        llm_usage_context = build_llm_usage_context(feature_key="survey_generation")
        thread = threading.Thread(
            target=lambda: run_with_llm_usage_context(
                llm_usage_context,
                survey_service.generate_survey_background,
                artifact_id=artifact_id,
                research_plan=research_plan,
                project_keywords=result.data["project_keywords"],
            )
        )
        thread.start()

        return jsonify({"success": True, "artifact_id": artifact_id})

    except Exception as e:
        print(f"[ERROR] Survey artifact 생성 실패: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
