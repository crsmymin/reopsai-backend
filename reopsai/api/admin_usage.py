"""Admin usage, LLM usage, token ledger, and model pricing endpoints."""

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity

from reopsai.api import admin as admin_module
from reopsai.shared.auth import tier_required


def _usage_date_filters():
    start_date = admin_module._parse_usage_date(request.args.get("start_date"))
    end_date = admin_module._parse_usage_date(request.args.get("end_date"))
    if request.args.get("start_date") and start_date is None:
        return None, None, jsonify({"success": False, "error": "start_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
    if request.args.get("end_date") and end_date is None:
        return None, None, jsonify({"success": False, "error": "end_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
    return start_date, end_date, None, None


@admin_module.admin_bp.route("/api/admin/teams/<int:team_id>/usage", methods=["GET"])
@tier_required(["super"])
def get_team_usage(team_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        start_at = admin_module._parse_iso_date(request.args.get("start_at"))
        end_at = admin_module._parse_iso_date(request.args.get("end_at"))
        if request.args.get("start_at") and start_at is None:
            return jsonify({"success": False, "error": "start_at은 ISO datetime 형식이어야 합니다."}), 400
        if request.args.get("end_at") and end_at is None:
            return jsonify({"success": False, "error": "end_at은 ISO datetime 형식이어야 합니다."}), 400

        result = admin_module.admin_usage_service.get_team_usage(team_id=team_id, start_at=start_at, end_at=end_at)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "팀을 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 팀 사용량 조회 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies/<int:company_id>/usage", methods=["GET"])
@tier_required(["super"])
def get_company_usage(company_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        start_at = admin_module._parse_iso_date(request.args.get("start_at"))
        end_at = admin_module._parse_iso_date(request.args.get("end_at"))
        if request.args.get("start_at") and start_at is None:
            return jsonify({"success": False, "error": "start_at은 ISO datetime 형식이어야 합니다."}), 400
        if request.args.get("end_at") and end_at is None:
            return jsonify({"success": False, "error": "end_at은 ISO datetime 형식이어야 합니다."}), 400

        result = admin_module.admin_usage_service.get_company_usage(company_id=company_id, start_at=start_at, end_at=end_at)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 회사 사용량 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/users/<int:user_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_user_llm_usage(user_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = admin_module._usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date, end_date, error_response, error_status = _usage_date_filters()
        if error_response is not None:
            return error_response, error_status

        result = admin_module.admin_usage_service.get_user_llm_usage(
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
        admin_module.log_error(e, f"Admin - 사용자 LLM 사용량 조회 실패 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies/<int:company_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_company_llm_usage(company_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = admin_module._usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date, end_date, error_response, error_status = _usage_date_filters()
        if error_response is not None:
            return error_response, error_status

        result = admin_module.admin_usage_service.get_company_llm_usage(
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
        admin_module.log_error(e, f"Admin - 회사 LLM 사용량 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/teams/<int:team_id>/llm-usage", methods=["GET"])
@tier_required(["super"])
def get_team_llm_usage(team_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        period = admin_module._usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date, end_date, error_response, error_status = _usage_date_filters()
        if error_response is not None:
            return error_response, error_status

        result = admin_module.admin_usage_service.get_team_llm_usage(
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
        admin_module.log_error(e, f"Admin - 팀 LLM 사용량 조회 실패 (team_id: {team_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies/<int:company_id>/token-balance", methods=["GET"])
@tier_required(["super"])
def get_company_token_balance_route(company_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        result = admin_module.admin_usage_service.get_company_token_balance(company_id=company_id)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        if result.status == "not_found":
            return jsonify({"success": False, "error": "회사를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, f"Admin - 회사 토큰 잔액 조회 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/companies/<int:company_id>/token-topups", methods=["POST"])
@tier_required(["super"])
def create_company_token_topup(company_id: int):
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        data = request.json or {}
        weighted_tokens = admin_module._to_int_or_none(data.get("weighted_tokens"))
        note = (data.get("note") or "").strip() or None
        if not weighted_tokens or weighted_tokens <= 0:
            return jsonify({"success": False, "error": "weighted_tokens는 1 이상의 정수여야 합니다."}), 400
        created_by = admin_module._to_int_or_none(get_jwt_identity())

        result = admin_module.admin_usage_service.create_company_token_topup(
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
        admin_module.log_error(e, f"Admin - 회사 토큰 충전 실패 (company_id: {company_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/llm-model-prices", methods=["GET"])
@tier_required(["super"])
def list_llm_model_prices():
    try:
        if not admin_module._ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        provider = (request.args.get("provider") or "").strip().lower()
        active_only = (request.args.get("active_only") or "1").strip() != "0"
        result = admin_module.admin_usage_service.list_model_prices(provider=provider, active_only=active_only)
        if result.status == "db_unavailable":
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, "Admin - LLM 모델 가격 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_module.admin_bp.route("/api/admin/llm-usage-events/expired", methods=["DELETE"])
@tier_required(["super"])
def delete_expired_llm_usage_events():
    try:
        retention_days = admin_module._to_int_or_none(request.args.get("retention_days")) or 90
        if retention_days < 1:
            return jsonify({"success": False, "error": "retention_days는 1 이상의 정수여야 합니다."}), 400
        result = admin_module.admin_usage_service.delete_expired_llm_usage_events(retention_days=retention_days)
        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        admin_module.log_error(e, "Admin - 만료된 LLM 원본 이벤트 삭제 실패")
        return jsonify({"success": False, "error": str(e)}), 500
