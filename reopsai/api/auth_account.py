"""Account deletion endpoint with CORS handling."""

import traceback

from flask import jsonify, make_response, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from reopsai.api import auth as auth_module


@auth_module.auth_bp.route("/api/auth/account", methods=["DELETE", "OPTIONS"])
def delete_account():
    """
    계정 삭제 API
    - 사용자의 모든 프로젝트, 스터디, 아티팩트 삭제
    - 사용자 정보 삭제
    """
    try:
        if request.method == "OPTIONS":
            response = make_response("", 200)
            return auth_module._apply_account_delete_cors(response)
    except Exception:
        traceback.print_exc()
        response = make_response("", 500)
        return auth_module._apply_account_delete_cors(response)

    try:
        verify_jwt_in_request()
    except Exception:
        response = make_response(jsonify({"success": False, "error": "인증이 필요합니다."}), 401)
        return auth_module._apply_account_delete_cors(response)

    try:
        user_id = get_jwt_identity()
        if not user_id:
            response = make_response(jsonify({"success": False, "error": "인증 정보가 없습니다."}), 401)
            return auth_module._apply_account_delete_cors(response)

        result = auth_module.auth_service.delete_account(user_id=user_id)
        if result.status == "db_unavailable":
            response = make_response(
                jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500
            )
            return auth_module._apply_account_delete_cors(response)

        response = make_response(
            jsonify(
                {
                    "success": True,
                    "message": "계정이 성공적으로 삭제되었습니다.",
                    "deleted_projects": result.data["deleted_projects"],
                    "deleted_studies": result.data["deleted_studies"],
                    "deleted_artifacts": result.data["deleted_artifacts"],
                }
            ),
            200,
        )
        return auth_module._apply_account_delete_cors(response)
    except Exception as exc:
        auth_module.log_error(exc, "계정 삭제")
        response = make_response(
            jsonify({"success": False, "error": f"계정 삭제 중 오류가 발생했습니다: {str(exc)}"}), 500
        )
        return auth_module._apply_account_delete_cors(response)
