"""
B2B(Business) company management routes.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity

from api_logger import log_error
from db.engine import session_scope
from reopsai_backend.application.b2b_service import DEFAULT_BUSINESS_PASSWORD, b2b_service
from reopsai_backend.shared.auth import tier_required


b2b_bp = Blueprint("b2b", __name__, url_prefix="/api/b2b")


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


def _get_identity_int():
    identity = get_jwt_identity()
    try:
        return int(identity) if identity is not None else None
    except Exception:
        return identity


def _get_company_id_claim():
    claims = get_jwt() or {}
    company_id = claims.get("company_id")
    try:
        return int(company_id) if company_id else None
    except Exception:
        return None


def _require_db():
    return session_scope is not None


def _require_user_id():
    user_id_int = _get_identity_int()
    if not user_id_int:
        return None, (jsonify({"success": False, "error": "사용자 정보를 확인할 수 없습니다."}), 401)
    return int(user_id_int), None


@b2b_bp.route("/membership/usage", methods=["GET"])
@tier_required(["enterprise"])
def b2b_get_membership_usage():
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        period = _usage_period()
        if not period:
            return jsonify({"success": False, "error": "period는 daily 또는 monthly여야 합니다."}), 400

        start_date = _parse_usage_date(request.args.get("start_date"))
        end_date = _parse_usage_date(request.args.get("end_date"))
        if request.args.get("start_date") and start_date is None:
            return jsonify({"success": False, "error": "start_date는 YYYY-MM-DD 형식이어야 합니다."}), 400
        if request.args.get("end_date") and end_date is None:
            return jsonify({"success": False, "error": "end_date는 YYYY-MM-DD 형식이어야 합니다."}), 400

        result = b2b_service.get_membership_usage(
            user_id=user_id_int,
            company_id_claim=_get_company_id_claim(),
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "멤버십 사용량은 회사 owner만 조회할 수 있습니다."}), 403
        if result.status == "company_not_found":
            return jsonify({"success": False, "error": "회사 정보를 찾을 수 없습니다."}), 404

        return jsonify({"success": True, **result.data}), 200
    except Exception as e:
        log_error(e, "B2B - 멤버십 사용량 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team", methods=["GET"])
@tier_required(["enterprise"])
def b2b_get_my_team():
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        result = b2b_service.get_my_team(user_id=user_id_int, company_id_claim=_get_company_id_claim())
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "company_not_found":
            return jsonify({"success": False, "error": "회사 정보를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, **result.data})
    except Exception as e:
        log_error(e, "B2B - 회사 정보 조회 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members", methods=["POST"])
@tier_required(["enterprise"])
def b2b_add_team_member():
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        role = (data.get("role") or "member").strip().lower()
        department = (data.get("department") or "").strip() or None
        if role not in {"owner", "member"}:
            role = "member"
        if not email:
            return jsonify({"success": False, "error": "이메일이 필요합니다."}), 400

        result = b2b_service.add_team_member(
            user_id=user_id_int,
            company_id_claim=_get_company_id_claim(),
            email=email,
            role=role,
            department=department,
        )
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "회사 멤버 추가는 owner만 가능합니다."}), 403
        if result.status == "user_not_found":
            return jsonify({"success": False, "error": "해당 이메일로 가입된 사용자가 없습니다."}), 404
        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 회사 멤버 추가 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>", methods=["PUT"])
@tier_required(["enterprise"])
def b2b_update_team_member(member_user_id: int):
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        data = request.get_json() or {}
        result = b2b_service.update_team_member(
            user_id=user_id_int,
            company_id_claim=_get_company_id_claim(),
            member_user_id=member_user_id,
            data=data,
        )
        if result.status == "self_update":
            return jsonify({"success": False, "error": "본인 정보는 /api/auth/business/profile에서 수정해주세요."}), 400
        if result.status == "unknown_fields":
            return jsonify({"success": False, "error": f"수정할 수 없는 필드입니다: {result.data}"}), 400
        if result.status == "empty_update":
            return jsonify({"success": False, "error": "수정할 name 또는 department가 필요합니다."}), 400
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "멤버 정보 수정은 owner만 가능합니다."}), 403
        if result.status == "not_same_company":
            return jsonify({"success": False, "error": "같은 회사 소속 멤버만 수정할 수 있습니다."}), 403
        if result.status == "target_owner":
            return jsonify({"success": False, "error": "owner 계정은 이 API로 수정할 수 없습니다."}), 400
        if result.status == "user_not_found":
            return jsonify({"success": False, "error": "수정할 멤버를 찾을 수 없습니다."}), 404
        if result.status == "not_business":
            return jsonify({"success": False, "error": "기업 계정 멤버만 수정할 수 있습니다."}), 400
        if result.status == "empty_name":
            return jsonify({"success": False, "error": "name은 비워둘 수 없습니다."}), 400
        return jsonify({"success": True, "message": "멤버 정보가 수정되었습니다.", "user": result.data}), 200
    except Exception as e:
        log_error(e, "B2B - 멤버 정보 수정 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>/reset-password", methods=["POST"])
@tier_required(["enterprise"])
def b2b_reset_team_member_password(member_user_id: int):
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        result = b2b_service.reset_team_member_password(
            user_id=user_id_int,
            company_id_claim=_get_company_id_claim(),
            member_user_id=member_user_id,
        )
        if result.status == "self_reset":
            return jsonify({"success": False, "error": "본인 비밀번호는 이 API로 초기화할 수 없습니다."}), 400
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "비밀번호 초기화는 owner만 가능합니다."}), 403
        if result.status == "not_same_company":
            return jsonify({"success": False, "error": "같은 회사 소속 멤버만 초기화할 수 있습니다."}), 403
        if result.status == "target_owner":
            return jsonify({"success": False, "error": "owner 계정은 이 API로 초기화할 수 없습니다."}), 403
        if result.status == "user_not_found":
            return jsonify({"success": False, "error": "대상 사용자를 찾을 수 없습니다."}), 404
        if result.status == "super_account":
            return jsonify({"success": False, "error": "super 계정은 초기화할 수 없습니다."}), 403
        if result.status == "not_business":
            return jsonify({"success": False, "error": "기업 계정 멤버만 초기화할 수 있습니다."}), 400
        return jsonify(
            {
                "success": True,
                "message": "비밀번호가 초기화되었습니다.",
                "temporary_password": DEFAULT_BUSINESS_PASSWORD,
                "user": result.data,
            }
        ), 200
    except Exception as e:
        log_error(e, "B2B - 멤버 비밀번호 초기화 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def b2b_remove_team_member(member_user_id: int):
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        result = b2b_service.remove_team_member(
            user_id=user_id_int,
            company_id_claim=_get_company_id_claim(),
            member_user_id=member_user_id,
        )
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "멤버 삭제는 owner만 가능합니다."}), 403
        if result.status == "self_remove":
            return jsonify({"success": False, "error": "owner 본인은 삭제할 수 없습니다."}), 400
        if result.status == "target_owner":
            return jsonify({"success": False, "error": "owner 계정은 삭제할 수 없습니다."}), 400
        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 멤버 삭제 실패")
        return jsonify({"success": False, "error": str(e)}), 500


@b2b_bp.route("/team/members/<int:member_user_id>/role", methods=["POST"])
@tier_required(["enterprise"])
def b2b_change_team_member_role(member_user_id: int):
    try:
        if not _require_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int, error_response = _require_user_id()
        if error_response:
            return error_response

        data = request.get_json() or {}
        new_role = (data.get("role") or "member").strip().lower()
        result = b2b_service.change_team_member_role(
            user_id=user_id_int,
            company_id_claim=_get_company_id_claim(),
            member_user_id=member_user_id,
            new_role=new_role,
        )
        if result.status == "unsupported_role":
            return jsonify({"success": False, "error": "현재는 owner로 변경만 지원합니다."}), 400
        if result.status == "no_company":
            return jsonify({"success": False, "error": "이 계정에 연결된 회사가 없습니다."}), 404
        if result.status == "forbidden":
            return jsonify({"success": False, "error": "권한 변경은 owner만 가능합니다."}), 403
        if result.status == "self_role_change":
            return jsonify({"success": False, "error": "본인을 대상으로 권한을 변경할 수 없습니다."}), 400
        if result.status == "not_same_company":
            return jsonify({"success": False, "error": "같은 회사 소속 멤버만 owner로 변경할 수 있습니다."}), 403
        return jsonify({"success": True})
    except Exception as e:
        log_error(e, "B2B - 멤버 권한 변경 실패")
        return jsonify({"success": False, "error": str(e)}), 500
