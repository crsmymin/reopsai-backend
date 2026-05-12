"""
AI 퍼소나(AI 인터뷰) 전용 API 라우터.
개발 중에는 B2B 모드에서만 사용. 라우터 분리로 기존 코드와 혼선 없이 확장.
"""

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from reopsai.shared.auth import tier_required


ai_persona_bp = Blueprint("ai_persona", __name__, url_prefix="/api/ai-persona")


@ai_persona_bp.route("/health", methods=["GET"])
@jwt_required()
@tier_required(["enterprise"])
def health():
    """B2B(엔터프라이즈) 전용 헬스체크. 라우터 동작 확인용."""
    return jsonify({"success": True, "service": "ai-persona"})
