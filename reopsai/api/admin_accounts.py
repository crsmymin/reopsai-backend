"""Admin account, team, company, and enterprise-user endpoints."""

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from reopsai.api import admin as admin_module
from reopsai.shared.auth import tier_required


@admin_module.admin_bp.route("/api/admin/enterprise/accounts", methods=["GET"])
@tier_required(["super"])
def list_enterprise_accounts():
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        search = (request.args.get("search") or "").strip()
        plan_code_raw = (request.args.get("plan_code") or "").strip().lower()
        account_list_plan_codes = admin_module.ALLOWED_PLAN_CODES | admin_module.ALLOWED_USER_PLAN_CODES
        plan_code = plan_code_raw if plan_code_raw in account_list_plan_codes else None
        account_type = (request.args.get("account_type") or "").strip().lower()
        company_role = (request.args.get("company_role") or request.args.get("team_role") or "").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": "유효하지 않은 plan_code입니다."}), 400
        if account_type and account_type not in {"google", "business", "individual"}:
            return jsonify({"success": False, "error": "유효하지 않은 account_type입니다."}), 400
        if company_role and company_role not in {"owner", "member"}:
            return jsonify({"success": False, "error": "유효하지 않은 company_role입니다."}), 400
        page, per_page = admin_module._pagination_params()

        result = admin_module.admin_service.list_enterprise_accounts(
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
        admin_module.log_error(e, "Admin - 기업 계정 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/enterprise/accounts", methods=["POST"])
@tier_required(["super"])
def create_enterprise_account():
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip()
        company_name = (data.get("company_name") or "").strip()
        department = (data.get("department") or "").strip() or None

        if not all([email, name, company_name]):
            return jsonify({"success": False, "error": "email, name, company_name은 필수입니다."}), 400

        result = admin_module.admin_service.create_enterprise_account(
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
        admin_module.log_error(e, "Admin - 기업 계정 생성")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/enterprise/accounts/<int:account_id>", methods=["PUT"])
@tier_required(["super"])
def update_enterprise_account(account_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        result = admin_module.admin_service.update_enterprise_account(account_id=account_id, data=data)
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
            allowed = sorted(admin_module.ALLOWED_USER_PLAN_CODES | set(admin_module.USER_PLAN_CODE_ALIASES.keys()))
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {allowed}"}), 400
        return jsonify({"success": True, "account": result.data}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 계정 수정 (account_id: {account_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/enterprise/accounts/<int:account_id>/reset-password", methods=["POST"])
@tier_required(["super"])
def reset_enterprise_account_password(account_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_service.reset_enterprise_account_password(account_id=account_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "계정을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "message": "비밀번호가 0000으로 초기화되었습니다. 오너에게 알려주세요."}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 기업 계정 비밀번호 초기화 (account_id: {account_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/teams", methods=["GET"])
@tier_required(["super"])
def list_admin_teams():
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        search = (request.args.get("search") or "").strip()
        plan_code = admin_module._validate_plan_code(request.args.get("plan_code"))
        enterprise_account_id = admin_module._to_int_or_none(request.args.get("enterprise_account_id"))
        status = (request.args.get("status") or "active").strip().lower()
        if request.args.get("plan_code") and not plan_code:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(admin_module.ALLOWED_PLAN_CODES)}"}), 400
        if request.args.get("enterprise_account_id") and enterprise_account_id is None:
            return jsonify({"success": False, "error": "enterprise_account_id가 올바르지 않습니다."}), 400
        if status not in {"active", "deleted", "all"}:
            return jsonify({"success": False, "error": "status는 active, deleted, all 중 하나여야 합니다."}), 400
        page, per_page = admin_module._pagination_params()

        result = admin_module.admin_service.list_admin_teams(
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
        admin_module.log_error(e, "Admin - 팀 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/teams", methods=["POST"])
@tier_required(["super"])
def create_admin_team():
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        enterprise_account_id = admin_module._to_int_or_none(data.get("enterprise_account_id"))
        team_name = (data.get("team_name") or "").strip()
        description = (data.get("description") or "").strip()
        requested_plan = admin_module._validate_plan_code(data.get("plan_code"))

        if enterprise_account_id is None or not team_name:
            return jsonify({"success": False, "error": "enterprise_account_id와 team_name은 필수입니다."}), 400
        if data.get("plan_code") and not requested_plan:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(admin_module.ALLOWED_PLAN_CODES)}"}), 400

        result = admin_module.admin_service.create_admin_team(
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
        admin_module.log_error(e, "Admin - 팀 생성")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/teams/<int:team_id>", methods=["DELETE"])
@tier_required(["super"])
def soft_delete_admin_team(team_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_service.soft_delete_admin_team(team_id=team_id)
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
        admin_module.log_error(e, f"Admin - 팀 삭제 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/enterprise/users", methods=["POST"])
@jwt_required()
def create_enterprise_user():
    """super 또는 company owner: 기업 계정 생성 + 임시 비밀번호 발급 + 회사 소속 지정"""
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip() or None
        company_id = admin_module._to_int_or_none(data.get("company_id"))
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
        result = admin_module.admin_service.create_enterprise_user(
            email=email,
            name=name,
            company_id=company_id,
            department=department,
            role=role,
            requester_id=admin_module._to_int_or_none(get_jwt_identity()),
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
        admin_module.log_error(e, "Admin - 기업 사용자 생성 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/teams/<int:team_id>/plan", methods=["PUT"])
@tier_required(["super"])
def update_team_plan_code(team_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        data = request.json or {}
        plan_code = (data.get("plan_code") or "").strip().lower()
        if plan_code not in admin_module.ALLOWED_PLAN_CODES:
            return jsonify({"success": False, "error": f"유효하지 않은 plan_code입니다: {sorted(admin_module.ALLOWED_PLAN_CODES)}"}), 400

        result = admin_module.admin_service.update_team_plan_code(team_id=team_id, plan_code=plan_code)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "team_id": team_id, "plan_code": plan_code}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 팀 plan 변경 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies", methods=["GET"])
@tier_required(["super"])
def list_admin_companies():
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        page, per_page = admin_module._pagination_params()
        search = (request.args.get("search") or "").strip()
        status = (request.args.get("status") or "").strip().lower()

        result = admin_module.admin_service.list_admin_companies(
            page=page,
            per_page=per_page,
            search=search,
            status=status,
        )
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, "Admin - 회사 목록 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies/<int:company_id>", methods=["GET"])
@tier_required(["super"])
def get_admin_company_detail(company_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        result = admin_module.admin_service.get_admin_company_detail(company_id=company_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 회사 상세 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies/<int:company_id>", methods=["PUT"])
@tier_required(["super"])
def update_admin_company(company_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        data = request.json or {}
        status = (data.get("status") or "").strip().lower()
        if status not in {"active", "inactive"}:
            return jsonify({"success": False, "error": "status는 active 또는 inactive만 가능합니다."}), 400

        result = admin_module.admin_service.update_admin_company(company_id=company_id, status=status)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "company": result.data}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 회사 수정 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500
