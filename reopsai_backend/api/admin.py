"""
Admin 전용 API 라우트
"""

import traceback
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from reopsai_backend.application.admin_backoffice_service import admin_backoffice_service
from reopsai_backend.application.admin_service import admin_service
from reopsai_backend.application.admin_usage_service import admin_usage_service
from reopsai_backend.shared.auth import tier_required


admin_bp = Blueprint("admin", __name__)
ALLOWED_PLAN_CODES = {"starter", "pro", "enterprise_plus"}
ALLOWED_USER_PLAN_CODES = {"free", "basic", "premium"}
USER_PLAN_CODE_ALIASES = {
    "starter": "free",
    "pro": "basic",
    "enterprise_plus": "premium",
}


def log_error(error, context=""):
    """에러 로깅"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] ❌ 에러 발생: {context}")
    print(f"에러 내용: {str(error)}")
    traceback.print_exc()


def _to_int_or_none(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _service_ready(service):
    return getattr(service, "db_ready", lambda: True)()


def _ensure_db():
    return (
        _service_ready(admin_service)
        and _service_ready(admin_usage_service)
        and _service_ready(admin_backoffice_service)
    )


def _parse_iso_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _parse_usage_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None


def _usage_period():
    period = (request.args.get("period") or "daily").strip().lower()
    return period if period in {"daily", "monthly"} else None


def _pagination_params():
    page = _to_int_or_none(request.args.get("page")) or 1
    per_page = _to_int_or_none(request.args.get("per_page")) or 20
    page = max(1, page)
    per_page = max(1, min(100, per_page))
    return page, per_page


def _validate_plan_code(plan_code, *, required=False):
    value = (plan_code or "").strip().lower()
    if not value:
        if required:
            return None
        return None
    return value if value in ALLOWED_PLAN_CODES else None


@admin_bp.route("/api/admin/enterprise/accounts", methods=["GET"])
@tier_required(["super"])
def list_enterprise_accounts():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        search = (request.args.get("search") or "").strip()
        plan_code_raw = (request.args.get("plan_code") or "").strip().lower()
        account_list_plan_codes = ALLOWED_PLAN_CODES | ALLOWED_USER_PLAN_CODES
        plan_code = plan_code_raw if plan_code_raw in account_list_plan_codes else None
        account_type = (request.args.get("account_type") or "").strip().lower()
        company_role = (request.args.get("company_role") or request.args.get("team_role") or "").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": "유효하지 않은 plan_code입니다."}), 400
        if account_type and account_type not in {"google", "business", "individual"}:
            return jsonify({"success": False, "error": "유효하지 않은 account_type입니다."}), 400
        if company_role and company_role not in {"owner", "member"}:
            return jsonify({"success": False, "error": "유효하지 않은 company_role입니다."}), 400
        page, per_page = _pagination_params()

        result = admin_service.list_enterprise_accounts(
            search=search,
            plan_code=plan_code,
            account_type=account_type,
            company_role=company_role,
            page=page,
            per_page=per_page,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify(result.data), 200
    except Exception as e:
        log_error(e, "Admin - 기업 계정 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/accounts", methods=["POST"])
@tier_required(["super"])
def create_enterprise_account():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip()
        company_name = (data.get("company_name") or "").strip()
        department = (data.get("department") or "").strip() or None

        if not all([email, name, company_name]):
            return jsonify({"success": False, "error": "email, name, company_name은 필수입니다."}), 400

        result = admin_service.create_enterprise_account(
            email=email,
            name=name,
            company_name=company_name,
            department=department,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "duplicate":
            return jsonify({"success": False, "error": "이미 존재하는 이메일입니다."}), 409
        return jsonify({"success": True, "account": result.data}), 201
    except Exception as e:
        log_error(e, "Admin - 기업 계정 생성")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/accounts/<int:account_id>", methods=["PUT"])
@tier_required(["super"])
def update_enterprise_account(account_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        result = admin_service.update_enterprise_account(account_id=account_id, data=data)
        if result.status == "empty_update":
            return jsonify({"success": False, "error": "수정할 name, company_name, department 또는 plan_code가 필요합니다."}), 400
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "계정을 찾을 수 없습니다."}), 404
        if result.status == "business_plan_forbidden":
            return jsonify({"success": False, "error": "기업형 계정의 플랜은 이 API에서 변경할 수 없습니다."}), 403
        if result.status == "non_user_plan_forbidden":
            return jsonify({"success": False, "error": "free/basic/premium 일반 계정만 플랜을 변경할 수 있습니다."}), 403
        if result.status == "invalid_user_plan":
            allowed = sorted(ALLOWED_USER_PLAN_CODES | set(USER_PLAN_CODE_ALIASES.keys()))
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {allowed}"}), 400
        return jsonify({"success": True, "account": result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 계정 수정 (account_id: {account_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/accounts/<int:account_id>/reset-password", methods=["POST"])
@tier_required(["super"])
def reset_enterprise_account_password(account_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_service.reset_enterprise_account_password(account_id=account_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "계정을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "message": "비밀번호가 0000으로 초기화되었습니다. 오너에게 알려주세요."}), 200
    except Exception as e:
        log_error(e, f"Admin - 기업 계정 비밀번호 초기화 (account_id: {account_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_admin_user(user_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        requester_id = _to_int_or_none(get_jwt_identity())
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

        result = admin_backoffice_service.delete_user(
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
        log_error(e, f"Admin - 사용자 삭제 실패 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams", methods=["GET"])
@tier_required(["super"])
def list_admin_teams():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        search = (request.args.get("search") or "").strip()
        plan_code = _validate_plan_code(request.args.get("plan_code"))
        enterprise_account_id = _to_int_or_none(request.args.get("enterprise_account_id"))
        status = (request.args.get("status") or "active").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400
        if request.args.get("enterprise_account_id") and enterprise_account_id is None:
            return jsonify({"success": False, "error": "enterprise_account_id가 올바르지 않습니다."}), 400
        if status not in {"active", "deleted", "all"}:
            return jsonify({"success": False, "error": "status는 active, deleted, all 중 하나여야 합니다."}), 400
        page, per_page = _pagination_params()

        result = admin_service.list_admin_teams(
            search=search,
            plan_code=plan_code,
            enterprise_account_id=enterprise_account_id,
            status=status,
            page=page,
            per_page=per_page,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify(result.data), 200
    except Exception as e:
        log_error(e, "Admin - 팀 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams", methods=["POST"])
@tier_required(["super"])
def create_admin_team():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        enterprise_account_id = _to_int_or_none(data.get("enterprise_account_id"))
        team_name = (data.get("team_name") or "").strip()
        description = (data.get("description") or "").strip()
        requested_plan = _validate_plan_code(data.get("plan_code"))

        if enterprise_account_id is None or not team_name:
            return jsonify({"success": False, "error": "enterprise_account_id와 team_name은 필수입니다."}), 400
        if data.get("plan_code") and not requested_plan:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400

        result = admin_service.create_admin_team(
            enterprise_account_id=enterprise_account_id,
            team_name=team_name,
            description=description,
            requested_plan=requested_plan,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "owner_not_found":
            return jsonify({"success": False, "error": "기업 계정을 찾을 수 없습니다."}), 404
        if result.status == "business_owner_forbidden":
            return jsonify({"success": False, "error": "business 계정은 개인용 team owner로 지정할 수 없습니다."}), 400
        return jsonify({"success": True, "team": result.data}), 201
    except Exception as e:
        log_error(e, "Admin - 팀 생성")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["DELETE"])
@tier_required(["super"])
def soft_delete_admin_team(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_service.soft_delete_admin_team(team_id=team_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
        return jsonify(
            {
                "success": True,
                "message": "이미 삭제 처리된 팀입니다." if result.data["was_deleted"] else "팀이 삭제 처리되었습니다.",
                "team": result.data["team"],
                "affected": result.data["affected"],
            }
        ), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 삭제 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users", methods=["GET"])
@tier_required(["super"])
def get_all_users_with_tier():
    """모든 사용자 조회 (tier 정보 및 통계 포함) - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_backoffice_service.list_users()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        log_error(e, "Admin - 사용자 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/tier", methods=["PUT"])
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

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_backoffice_service.update_user_tier(user_id=user_id_int, tier=new_tier)
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
        log_error(e, f"Admin - 사용자 tier 변경 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise", methods=["GET"])
@tier_required(["super"])
def get_user_enterprise_info(user_id):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_backoffice_service.get_user_enterprise_info(user_id=user_id_int)
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
        log_error(e, f"Admin - 사용자 엔터프라이즈 정보 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise/init-team", methods=["POST"])
@tier_required(["super"])
def init_enterprise_team_for_user(user_id):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        data = request.json or {}
        company_name = (data.get("company_name") or "").strip()
        department = (data.get("department") or "").strip() or None

        result = admin_backoffice_service.init_enterprise_team_for_user(
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
        log_error(e, f"Admin - 엔터프라이즈 팀 생성 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/enterprise/users", methods=["POST"])
@jwt_required()
def create_enterprise_user():
    """super 또는 company owner: 기업 계정 생성 + 임시 비밀번호 발급 + 회사 소속 지정"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip() or None
        company_id = _to_int_or_none(data.get("company_id"))
        department = (data.get("department") or "").strip() or None
        role = (data.get("role") or "member").strip().lower()
        if role not in {"owner", "member"}:
            role = "member"
        if not email:
            return jsonify({"success": False, "error": "email이 필요합니다."}), 400
        if not company_id:
            return jsonify({"success": False, "error": "company_id가 필요합니다."}), 400

        claims = get_jwt() or {}
        requester_tier = (claims.get("tier") or "").strip().lower()
        result = admin_service.create_enterprise_user(
            email=email,
            name=name,
            company_id=company_id,
            department=department,
            role=role,
            requester_id=_to_int_or_none(get_jwt_identity()),
            requester_tier=requester_tier,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "company_not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "권한이 없습니다. super 또는 회사 owner만 가능합니다."}), 403
        return jsonify({"success": True, **result.data}), 201
    except Exception as e:
        log_error(e, "Admin - 기업 사용자 생성 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>/plan", methods=["PUT"])
@tier_required(["super"])
def update_team_plan_code(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        data = request.json or {}
        plan_code = (data.get("plan_code") or "").strip().lower()
        if plan_code not in ALLOWED_PLAN_CODES:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(ALLOWED_PLAN_CODES)}"}), 400

        result = admin_service.update_team_plan_code(team_id=team_id, plan_code=plan_code)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "team_id": team_id, "plan_code": plan_code}), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 plan 변경 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>/usage", methods=["GET"])
@tier_required(["super"])
def get_team_usage(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        start_at = _parse_iso_date(request.args.get("start_at"))
        end_at = _parse_iso_date(request.args.get("end_at"))
        if request.args.get("start_at") and start_at is None:
            return jsonify({"success": False, "error": "start_at은 ISO datetime 형식이어야 합니다."}), 400
        if request.args.get("end_at") and end_at is None:
            return jsonify({"success": False, "error": "end_at은 ISO datetime 형식이어야 합니다."}), 400

        result = admin_usage_service.get_team_usage(team_id=team_id, start_at=start_at, end_at=end_at)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 사용량 조회 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/usage", methods=["GET"])
@tier_required(["super"])
def get_company_usage(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        start_at = _parse_iso_date(request.args.get("start_at"))
        end_at = _parse_iso_date(request.args.get("end_at"))
        if request.args.get("start_at") and start_at is None:
            return jsonify({"success": False, "error": "start_at은 ISO datetime 형식이어야 합니다."}), 400
        if request.args.get("end_at") and end_at is None:
            return jsonify({"success": False, "error": "end_at은 ISO datetime 형식이어야 합니다."}), 400

        result = admin_usage_service.get_company_usage(company_id=company_id, start_at=start_at, end_at=end_at)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 사용량 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


def _usage_date_filters():
    start_date = _parse_usage_date(request.args.get("start_date"))
    end_date = _parse_usage_date(request.args.get("end_date"))
    if request.args.get("start_date") and start_date is None:
        return None, None, jsonify({"success": False, "error": "start_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
    if request.args.get("end_date") and end_date is None:
        return None, None, jsonify({"success": False, "error": "end_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
    return start_date, end_date, None, None


@admin_bp.route("/api/admin/users/<int:user_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_user_llm_usage(user_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date, end_date, error_response, error_status = _usage_date_filters()
        if error_response is not None:
            return error_response, error_status

        result = admin_usage_service.get_user_llm_usage(
            user_id=user_id,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 사용자 LLM 사용량 조회 실패 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_company_llm_usage(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date, end_date, error_response, error_status = _usage_date_filters()
        if error_response is not None:
            return error_response, error_status

        result = admin_usage_service.get_company_llm_usage(
            company_id=company_id,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 LLM 사용량 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/teams/<int:team_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_team_llm_usage(team_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date, end_date, error_response, error_status = _usage_date_filters()
        if error_response is not None:
            return error_response, error_status

        result = admin_usage_service.get_team_llm_usage(
            team_id=team_id,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 팀 LLM 사용량 조회 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/token-balance", methods=["GET"])
@tier_required(["super"])
def get_company_token_balance_route(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        result = admin_usage_service.get_company_token_balance(company_id=company_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 토큰 잔액 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>/token-topups", methods=["POST"])
@tier_required(["super"])
def create_company_token_topup(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        data = request.json or {}
        weighted_tokens = _to_int_or_none(data.get("weighted_tokens"))
        note = (data.get("note") or "").strip() or None
        if not weighted_tokens or weighted_tokens <= 0:
            return jsonify({"success": False, "error": "weighted_tokens는 1 이상의 정수여야 합니다."}), 400
        created_by = _to_int_or_none(get_jwt_identity())

        result = admin_usage_service.create_company_token_topup(
            company_id=company_id,
            weighted_tokens=weighted_tokens,
            created_by=created_by,
            note=note,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 201
    except Exception as e:
        log_error(e, f"Admin - 회사 토큰 충전 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/llm-model-prices", methods=["GET"])
@tier_required(["super"])
def list_llm_model_prices():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        provider = (request.args.get("provider") or "").strip().lower()
        active_only = (request.args.get("active_only") or "1").strip() != "0"
        result = admin_usage_service.list_model_prices(provider=provider, active_only=active_only)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, "Admin - LLM 모델 가격 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/llm-usage-events/expired", methods=["DELETE"])
@tier_required(["super"])
def delete_expired_llm_usage_events():
    try:
        retention_days = _to_int_or_none(request.args.get("retention_days")) or 90
        if retention_days < 1:
            return jsonify({"success": False, "error": "retention_days는 1 이상의 정수여야 합니다."}), 400
        result = admin_usage_service.delete_expired_llm_usage_events(retention_days=retention_days)
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, "Admin - 만료된 LLM 원본 이벤트 삭제 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies", methods=["GET"])
@tier_required(["super"])
def list_admin_companies():
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        page, per_page = _pagination_params()
        search = (request.args.get("search") or "").strip()
        status = (request.args.get("status") or "").strip().lower()

        result = admin_service.list_admin_companies(
            page=page,
            per_page=per_page,
            search=search,
            status=status,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, "Admin - 회사 목록 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>", methods=["GET"])
@tier_required(["super"])
def get_admin_company_detail(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_service.get_admin_company_detail(company_id=company_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 상세 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/companies/<int:company_id>", methods=["PUT"])
@tier_required(["super"])
def update_admin_company(company_id: int):
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        status = (data.get("status") or "").strip().lower()
        if status not in {"active", "inactive"}:
            return jsonify({"success": False, "error": "status는 active 또는 inactive만 가능합니다."}), 400

        result = admin_service.update_admin_company(company_id=company_id, status=status)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "company": result.data}), 200
    except Exception as e:
        log_error(e, f"Admin - 회사 수정 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/stats", methods=["GET"])
@tier_required(["super"])
def get_admin_stats():
    """관리자 대시보드 통계 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_backoffice_service.get_admin_stats()
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        log_error(e, "Admin - 통계 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/projects", methods=["GET"])
@tier_required(["super"])
def get_user_projects(user_id):
    """특정 사용자의 프로젝트 목록 조회 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_backoffice_service.get_user_projects(user_id=user_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        log_error(e, f"Admin - 사용자 프로젝트 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/studies", methods=["GET"])
@tier_required(["super"])
def get_user_studies(user_id):
    """특정 사용자의 스터디 목록 조회 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        result = admin_backoffice_service.get_user_studies(user_id=user_id_int)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        log_error(e, f"Admin - 사용자 스터디 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/studies/<int:study_id>", methods=["GET"])
@tier_required(["super"])
def admin_get_study(study_id):
    """Admin 전용 - Study 조회 (권한 검증 없이)"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_backoffice_service.get_study(study_id=study_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "연구를 찾을 수 없습니다."}), 404
        return jsonify(result.data)
    except Exception as e:
        log_error(e, f"Admin - Study 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/studies/<int:study_id>/artifacts", methods=["GET"])
@tier_required(["super"])
def admin_get_study_artifacts(study_id):
    """Admin 전용 - Study의 Artifacts 조회 (권한 검증 없이)"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_backoffice_service.get_study_artifacts(study_id=study_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data})
    except Exception as e:
        log_error(e, f"Admin - Study Artifacts 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/feedback", methods=["POST"])
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

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id = get_jwt_identity()
        user_id_int = _to_int_or_none(user_id)
        study_id = data.get("study_id")
        study_name = data.get("study_name", "")

        result = admin_backoffice_service.submit_feedback(
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
        log_error(e, "피드백 저장")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/feedback/<int:feedback_id>", methods=["PATCH"])
@jwt_required()
def update_feedback_comment(feedback_id):
    """피드백 코멘트만 업데이트"""
    try:
        data = request.json or {}
        comment = data.get("comment", "")

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(get_jwt_identity())
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자입니다."}), 401

        result = admin_backoffice_service.update_feedback_comment(
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
        log_error(e, f"피드백 {feedback_id} 코멘트 업데이트")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/feedback", methods=["GET"])
@tier_required(["super"])
def get_feedback():
    """피드백 조회 - admin 전용, category 필터링 지원"""
    try:
        if not _ensure_db():
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

        result = admin_backoffice_service.list_feedback(category=category)
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
        log_error(e, "Admin - 피드백 조회")
        return jsonify({"success": False, "error": str(e)}), 500
