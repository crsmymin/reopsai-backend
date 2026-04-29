import os
import threading
import uuid
from typing import Dict, Iterable, List, Optional, Set
from flask import Flask, g, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity, get_jwt, verify_jwt_in_request
from services.gemini_service import gemini_service
from services.openai_service import openai_service
# [수정] 모든 프롬프트 클래스를 analysis_prompts.py에서 가져오도록 통일 (관리 편의성)
from prompts.analysis_prompts import (
    PlanGeneratorPrompts, DiagnosisPrompts, GenerationPrompts, 
    SurveyBuilderPrompts, ScreenerPrompts, SurveyDiagnosisPrompts, 
    SurveyGenerationPrompts, GuidelineGeneratorPrompts, KeywordExtractionPrompts
)
# [신규] 개선된 VectorDB 서비스 임포트
from rag_system.improved.improved_vector_db_service import VectorDBServiceWrapper
# [신규] API 로깅 시스템 임포트
from api_logger import (
    log_api_call, log_rag_search, log_data_processing, 
    log_llm_call, log_error, log_response, log_performance,
    log_step_search, log_rag_quality_check,
    # 새로운 깔끔한 로깅 함수들
    log_user_request, log_keyword_extraction, log_rag_search_clean,
    log_expert_analysis, log_analysis_complete, log_step_search_clean
)
# [신규] 디버깅 및 요청 추적 시스템 임포트
from debug_utils import (
    track_request, track_step, complete_track, get_stats, 
    log_performance_issue, analyze_error_patterns, request_tracker
)
import pandas as pd
import io
import json
import traceback
import time
import re
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import func, select



app = Flask(__name__)
PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/auth/enterprise/change-password",
    "/api/auth/business/change-password",
    "/api/profile",
}

# CORS 설정을 환경변수 기반으로 적용
from config import Config
try:
    from db.engine import init_engine as init_sqlalchemy_engine, session_scope
    from db.models.core import Artifact, Project, Study
    from db.repositories.workspace_repository import WorkspaceRepository
except Exception:
    init_sqlalchemy_engine = None
    session_scope = None
    Artifact = None
    Project = None
    Study = None
    WorkspaceRepository = None

app.config.from_object(Config)

jwt = JWTManager(app)

# JWT 에러 핸들러 추가 (디버깅용)
@jwt.invalid_token_loader
def invalid_token_callback(error):
    print(f"❌ Invalid JWT Token: {error}")
    return jsonify({'error': 'Invalid token', 'message': str(error)}), 422

@jwt.unauthorized_loader
def unauthorized_callback(error):
    print(f"❌ Unauthorized (No JWT): {error}")
    try:
        print(
            "JWT request debug:",
            {
                "path": request.path,
                "host": request.host,
                "origin": request.headers.get("Origin"),
                "cookie_names": sorted(list(request.cookies.keys())),
                "has_access_cookie": "access_token_cookie" in request.cookies,
            },
        )
    except Exception:
        pass
    return jsonify({'error': 'Missing Authorization Header', 'message': str(error)}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    print("❌ Expired JWT Token")
    return jsonify({'error': 'Token has expired', 'message': 'Please log in again'}), 401

@jwt.revoked_token_loader
def revoked_token_callback(jwt_header, jwt_payload):
    print("❌ Revoked JWT Token")
    return jsonify({'error': 'Token has been revoked'}), 401

CORS(app, 
     resources={
         r"/api/*": {
             "origins": Config.ALLOWED_ORIGINS,
             "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
             "allow_headers": ["Content-Type", "Authorization", "Accept", "X-User-ID", "x-user-id"],
             "supports_credentials": True,
             "max_age": 86400
         }
     },
     automatic_options=True,  # OPTIONS 요청 자동 처리
     intercept_exceptions=False)

SQLA_ENABLED = False


@app.before_request
def enforce_enterprise_password_change():
    g.request_id = getattr(g, "request_id", None) or uuid.uuid4().hex
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt() or {}
        if (
            request.method != "OPTIONS"
            and claims.get("password_reset_required")
            and (request.path or "") not in PASSWORD_CHANGE_ALLOWED_PATHS
        ):
            return jsonify({"error": "Password change required"}), 403
    except Exception:
        return None
    return None


@app.before_request
def enforce_business_llm_quota():
    try:
        if request.method == "OPTIONS":
            return None
        verify_jwt_in_request(optional=True)
        claims = get_jwt() or {}
        company_id = claims.get("company_id")
        if claims.get("account_type") != "business" or not company_id:
            return None
        feature_key = classify_feature_key(request.path or "")
        if not feature_key:
            return None
        try:
            company_id_int = int(company_id)
        except Exception:
            return None
        if is_company_quota_exceeded(company_id_int):
            return jsonify(
                {
                    "success": False,
                    "error": "quota_exceeded",
                    "message": "기업의 사용 가능한 weighted token 한도를 초과했습니다.",
                    "remaining_weighted_tokens": 0,
                }
            ), 402
    except Exception:
        return None
    return None

# --- [신규] SQLAlchemy 엔진 초기화 ---
try:
    if not init_sqlalchemy_engine:
        print("ℹ️ SQLAlchemy package not ready; engine initialization skipped")
    elif Config.DATABASE_URL:
        init_sqlalchemy_engine(validate_connection=True)
        SQLA_ENABLED = True
        print("✅ SQLAlchemy engine initialized")
    else:
        print("ℹ️ DATABASE_URL not set; SQLAlchemy engine initialization skipped")
except Exception as e:
    print(f"❌ SQLAlchemy engine initialization failed: {e}")


# --- [SEO/보안] 보안 헤더 설정 ---
@app.after_request
def set_security_headers(response):
    """보안 헤더 설정 - SEO 점검 항목 개선"""
    # HSTS (HTTP Strict Transport Security) - HTTPS 강제
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    
    # X-Frame-Options (클릭재킹 방지)
    response.headers['X-Frame-Options'] = 'DENY'
    
    # X-Content-Type-Options (MIME 스니핑 방지)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    
    # X-XSS-Protection (레거시 브라우저용)
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Referrer-Policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    return response


# --- [신규] 개선된 Vector DB 서비스 초기화 ---
# services/vector_service.py 싱글턴 사용
from services.vector_service import vector_service

# --- 유틸리티 함수 임포트 (분리된 모듈) ---
from utils.idempotency import (
    _cleanup_idempotency_cache, _reserve_idempotency_entry,
    _complete_idempotency_entry, _fail_idempotency_entry, _respond_from_entry,
)
from utils.keyword_utils import (
    KEYWORD_STOPWORDS, _refine_extracted_keywords,
    fetch_project_keywords,
)
from utils.request_utils import _extract_request_user_id, _resolve_owner_ids_sqlalchemy
from utils.llm_utils import parse_llm_json_response
from utils.usage_metering import classify_feature_key, is_company_quota_exceeded



# --- API 엔드포인트 ---
# 간단한 헬스체크 엔드포인트 (Docker healthcheck 및 로드밸런서용)
@app.route('/health')
def health():
    try:
        return {"status": "ok"}, 200
    except Exception:
        return {"status": "error"}, 500

# --- [신규] Blueprint 등록 (app.py 하나로 실행, 라우트만 분리) ---
from routes.auth import auth_bp, tier_required, get_primary_team_id_for_user
from routes.screener import screener_bp
from routes.study import study_bp
from routes.generator import generator_bp
from routes.demo import demo_bp
from routes.artifact_ai import artifact_ai_bp
from routes.b2b import b2b_bp
from routes.ai_persona import ai_persona_bp
app.register_blueprint(auth_bp)
app.register_blueprint(screener_bp)
app.register_blueprint(study_bp)
app.register_blueprint(generator_bp)
app.register_blueprint(demo_bp)
app.register_blueprint(artifact_ai_bp)
app.register_blueprint(b2b_bp)
app.register_blueprint(ai_persona_bp)
# 개발 전용 평가기 (plan/survey/guideline/report 범용 테스트기 판때기)
if os.getenv('FLASK_ENV') == 'development':
    from routes.dev_evaluator import dev_evaluator_bp
    app.register_blueprint(dev_evaluator_bp)
    print("✅ Dev Evaluator Blueprint 등록됨 (개발 환경)")

# ⭐ Admin Blueprint 등록 (모든 환경)
from routes.admin import admin_bp
app.register_blueprint(admin_bp)
print("✅ Admin Blueprint 등록됨")

# --- [리팩터링] 새 Blueprint 등록 ---
from routes.workspace import workspace_bp
from routes.survey_routes import survey_bp
from routes.guideline_routes import guideline_bp
from routes.plan_routes import plan_bp

app.register_blueprint(workspace_bp)
app.register_blueprint(survey_bp)
app.register_blueprint(guideline_bp)
app.register_blueprint(plan_bp)
print("✅ 리팩터링 Blueprint 등록됨 (workspace, survey, guideline, plan)")


if __name__ == '__main__':
    # [수정] DB 구축 코드는 vector_db_service.py의 __main__으로 이동
    # 앱 실행 전에 `python vector_db_service.py`를 꼭 실행하세요.
    if vector_service is None:
        print("="*50)
        print("경고: Vector DB 서비스가 초기화되지 않았습니다.")
        print("앱이 정상 작동하지 않을 수 있습니다.")
        print("터미널에서 'python vector_db_service.py'를 먼저 실행하여 DB를 구축하세요.")
        print("="*50)
    print(f"Starting Flask server on port {Config.PORT}...")
    app.run(debug=Config.DEBUG, port=Config.PORT, host='0.0.0.0')
