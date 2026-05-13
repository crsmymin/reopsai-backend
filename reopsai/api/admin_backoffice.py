"""Admin backoffice, content inspection, and feedback endpoints."""

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from reopsai.api import admin as admin_module
from reopsai.shared.auth import tier_required


@admin_module.admin_bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_admin_user(user_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        requester_id = admin_module._to_int_or_none(get_jwt_identity())
        claims = get_jwt() or {}
        requester_tier = (claims.get("tier") or "").strip().lower()
        if requester_tier == "admin":
            requester_tier = "super"

        if claims.get("password_reset_required"):
            return jsonify({"success": False, "error": "비밀번호 변경 후 이용할 수 있습니다."}), 403
        if requester_id is None:
            return jsonify({"success": False, "error": "인증 정보를 확인할 수 없습니다."}), 401
        if requester_id == user_id:
            return jsonify({"success": False, "error": "현재 로그인한 계정은 이 API로 삭제할 수 없습니다."}), 400

        result = admin_module.admin_backoffice_service.delete_user(
            user_id=user_id,
            requester_id=requester_id,
            requester_tier=requester_tier,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "권한이 없습니다. super 또는 해당 회사 owner만 삭제할 수 있습니다."}), 403
        if result.status == "target_super_forbidden":
            return jsonify({"success": False, "error": "팀 owner는 super 계정을 삭제할 수 없습니다."}), 403
        if result.status == "target_owner_forbidden":
            return jsonify({"success": False, "error": "회사 owner 계정은 super만 삭제할 수 있습니다."}), 403

        return jsonify(
            {
                "success": True,
                "message": "사용자가 삭제되었습니다.",
                "deleted_user": result.data["deleted_user"],
                "affected": result.data["affected"],
            }
        ), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 사용자 삭제 실패 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users", methods=["GET"])
@tier_required(["super"])
def get_all_users_with_tier():
    """모든 사용자 조회 (tier 정보 및 통계 포함) - admin 전용"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_backoffice_service.list_users()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        admin_module.log_error(e, "Admin - 사용자 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users/<user_id>/tier", methods=["PUT"])
@tier_required(["super"])
def update_user_tier(user_id):
    """사용자 tier 변경 - admin 전용"""
    try:
        data = request.json or {}
        new_tier = (data.get("tier") or "").strip().lower()
        if new_tier == "admin":
            new_tier = "super"

        valid_tiers = ["free", "basic", "premium", "enterprise", "super"]
        if new_tier not in valid_tiers:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 tier입니다. 가능한 값: {valid_tiers}",
                }
            ), 400

        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = admin_module._to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_module.admin_backoffice_service.update_user_tier(user_id=user_id_int, tier=new_tier)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

        return jsonify(
            {
                "success": True,
                "message": f"사용자 tier가 {new_tier}로 변경되었습니다.",
                "user": result.data,
            }
        )
    except Exception as e:
        admin_module.log_error(e, f"Admin - 사용자 tier 변경 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users/<user_id>/enterprise", methods=["GET"])
@tier_required(["super"])
def get_user_enterprise_info(user_id):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = admin_module._to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_module.admin_backoffice_service.get_user_enterprise_info(user_id=user_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

        return jsonify(
            {
                "success": True,
                "user": result.data["user"],
                "tier": result.data["tier"],
                "company": result.data["company"],
            }
        )
    except Exception as e:
        admin_module.log_error(e, f"Admin - 사용자 엔터프라이즈 정보 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users/<user_id>/enterprise/init-team", methods=["POST"])
@tier_required(["super"])
def init_enterprise_team_for_user(user_id):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = admin_module._to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        data = request.json or {}
        company_name = (data.get("company_name") or "").strip()
        department = (data.get("department") or "").strip() or None

        result = admin_module.admin_backoffice_service.init_enterprise_team_for_user(
            user_id=user_id_int,
            company_name=company_name,
            department=department,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        if result.status == "already_exists":
            return jsonify(
                {
                    "success": True,
                    "message": "이미 대표 회사 멤버십이 존재합니다.",
                    "user": result.data["user"],
                    "company": result.data["company"],
                }
            )

        return jsonify(
            {
                "success": True,
                "message": "business 회사가 설정되고 사용자가 owner로 등록되었습니다.",
                "user": result.data["user"],
                "company": result.data["company"],
            }
        )
    except Exception as e:
        admin_module.log_error(e, f"Admin - 엔터프라이즈 팀 생성 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/stats", methods=["GET"])
@tier_required(["super"])
def get_admin_stats():
    """관리자 대시보드 통계 - admin 전용"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_backoffice_service.get_admin_stats()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        admin_module.log_error(e, "Admin - 통계 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users/<user_id>/projects", methods=["GET"])
@tier_required(["super"])
def get_user_projects(user_id):
    """특정 사용자의 프로젝트 목록 조회 - admin 전용"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = admin_module._to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_module.admin_backoffice_service.get_user_projects(user_id=user_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        admin_module.log_error(e, f"Admin - 사용자 프로젝트 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users/<user_id>/studies", methods=["GET"])
@tier_required(["super"])
def get_user_studies(user_id):
    """특정 사용자의 스터디 목록 조회 - admin 전용"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = admin_module._to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_module.admin_backoffice_service.get_user_studies(user_id=user_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        admin_module.log_error(e, f"Admin - 사용자 스터디 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/studies/<int:study_id>", methods=["GET"])
@tier_required(["super"])
def admin_get_study(study_id):
    """Admin 전용 - Study 조회 (권한 검증 없이)"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_backoffice_service.get_study(study_id=study_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "연구를 찾을 수 없습니다."}), 404
        return jsonify(result.data)
    except Exception as e:
        admin_module.log_error(e, f"Admin - Study 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/studies/<int:study_id>/artifacts", methods=["GET"])
@tier_required(["super"])
def admin_get_study_artifacts(study_id):
    """Admin 전용 - Study의 Artifacts 조회 (권한 검증 없이)"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_backoffice_service.get_study_artifacts(study_id=study_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        admin_module.log_error(e, f"Admin - Study Artifacts 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/feedback", methods=["POST"])
@jwt_required()
def submit_feedback():
    """피드백 저장 - 로그인한 사용자만 사용 가능"""
    try:
        data = request.json or {}
        category = data.get("category")
        vote = data.get("vote")
        comment = data.get("comment", "")

        valid_categories = ["plan", "screener", "guide", "participants"]
        if not category or category not in valid_categories:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 category입니다. 가능한 값: {valid_categories}",
                }
            ), 400
        if vote is None:
            return jsonify({"success": False, "error": "vote 값이 필요합니다. (true 또는 false)"}), 400

        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id = get_jwt_identity()
        user_id_int = admin_module._to_int_or_none(user_id)
        study_id = data.get("study_id")
        study_name = data.get("study_name", "")

        result = admin_module.admin_backoffice_service.submit_feedback(
            category=category,
            vote=vote,
            comment=comment,
            user_id=user_id_int,
            study_id=study_id,
            study_name=study_name,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        return jsonify(
            {
                "success": True,
                "message": "피드백이 저장되었습니다.",
                "feedback": result.data,
            }
        )
    except Exception as e:
        admin_module.log_error(e, "피드백 저장")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/feedback/<int:feedback_id>", methods=["PATCH"])
@jwt_required()
def update_feedback_comment(feedback_id):
    """피드백 코멘트만 업데이트"""
    try:
        data = request.json or {}
        comment = data.get("comment", "")

        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = admin_module._to_int_or_none(get_jwt_identity())
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자입니다."}), 401

        result = admin_module.admin_backoffice_service.update_feedback_comment(
            feedback_id=feedback_id,
            user_id=user_id_int,
            comment=comment,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "피드백을 찾을 수 없거나 권한이 없습니다."}), 404

        return jsonify(
            {
                "success": True,
                "message": "코멘트가 업데이트되었습니다.",
                "feedback": result.data,
            }
        )
    except Exception as e:
        admin_module.log_error(e, f"피드백 {feedback_id} 코멘트 업데이트")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/feedback", methods=["GET"])
@tier_required(["super"])
def get_feedback():
    """피드백 조회 - admin 전용, category 필터링 지원"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        category = request.args.get("category", None)
        valid_categories = ["plan", "screener", "guide", "participants"]
        if category and category not in valid_categories:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 category입니다. 가능한 값: {valid_categories}",
                }
            ), 400

        result = admin_module.admin_backoffice_service.list_feedback(category=category)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        return jsonify(
            {
                "success": True,
                "feedback": result.data["feedback"],
                "count": result.data["count"],
                "category": result.data["category"],
            }
        )
    except Exception as e:
        admin_module.log_error(e, "Admin - 피드백 조회")
        return jsonify({"success": False, "error": str(e)}), 500
