import os
import threading
import uuid
from typing import Dict, Iterable, List, Optional, Set
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity, get_jwt
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
             "supports_credentials": False,
             "max_age": 86400
         }
     },
     automatic_options=True,  # OPTIONS 요청 자동 처리
     intercept_exceptions=False)

SQLA_ENABLED = False

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
# app이 실행될 때 개선된 Vector DB를 한 번만 로드합니다.
try:
    vector_service = VectorDBServiceWrapper(
        db_path="./chroma_db", 
        collection_name="ux_rag" # 개선된 서비스로 초기화
    )
    print("app.py: 개선된 VectorDBService 초기화 성공.")
except Exception as e:
    print(f"app.py: 치명적 오류! 개선된 VectorDBService 초기화 실패: {e}")
    # 실제 프로덕션에서는 DB 연결 실패 시 앱 실행을 중단해야 할 수 있습니다.
    vector_service = None 

KEYWORD_STOPWORDS = {
    "연구", "조사", "사용자", "고객", "분석", "목표", "문제", "해결", "정보",
    "제안", "방안", "성과", "전략", "항목", "결과", "체계", "프로세스", "개선",
    "방법", "관련", "데이터", "서비스", "제품", "기반", "영역", "활동", "요소",
    "활용", "진행", "필요", "대상", "이해", "확인", "경험", "도출", "정의",
    "analysis", "research", "study", "user", "customer", "problem", "goal",
    "objective", "method", "plan", "strategy", "service", "product", "process",
    "improvement", "result", "task", "insight"
}
KEYWORD_STOPWORDS_LOWER = {stop.lower() for stop in KEYWORD_STOPWORDS}

IDEMPOTENCY_TTL_SECONDS = 300
_idempotency_cache: Dict[str, Dict[str, object]] = {}
_idempotency_lock = threading.Lock()


def _cleanup_idempotency_cache(now: float) -> None:
    with _idempotency_lock:
        expired_keys = [
            key for key, entry in _idempotency_cache.items()
            if entry.get('expires_at', 0) < now
        ]
        for key in expired_keys:
            _idempotency_cache.pop(key, None)


def _reserve_idempotency_entry(key: str):
    now = time.time()
    _cleanup_idempotency_cache(now)

    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if entry:
            return entry, False

        event = threading.Event()
        entry = {
            'event': event,
            'response': None,
            'status': None,
            'error': None,
            'created_at': now,
            'expires_at': now + IDEMPOTENCY_TTL_SECONDS,
        }
        _idempotency_cache[key] = entry
        return entry, True


def _complete_idempotency_entry(key: str, response_data: Dict[str, object], status: int = 200) -> None:
    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if not entry:
            return
        entry['response'] = response_data
        entry['status'] = status
        entry['error'] = None
        entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
        entry['event'].set()


def _fail_idempotency_entry(key: str, error_data: Dict[str, object], status: int = 500) -> None:
    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if not entry:
            return
        entry['error'] = error_data
        entry['status'] = status
        entry['response'] = None
        entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
        entry['event'].set()


def _respond_from_entry(entry):
    event = entry.get('event')
    if event and not event.is_set():
        event.wait(timeout=15)
    with _idempotency_lock:
        if entry.get('response') is not None:
            entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
            return jsonify(entry['response']), entry.get('status', 200)
        if entry.get('error') is not None:
            entry['expires_at'] = time.time() + IDEMPOTENCY_TTL_SECONDS
            return jsonify(entry['error']), entry.get('status', 200)
    return jsonify({'success': False, 'error': '중복 요청이 아직 처리 중입니다. 잠시 후 다시 시도해주세요.'}), 409


def _clean_metadata_text(value: Optional[str], max_len: int = 180) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r'\s+', ' ', value).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip() + '…'
    return cleaned


def _refine_extracted_keywords(
    keywords: Iterable[str],
    extra_keywords: Optional[Iterable[str]] = None
) -> List[str]:
    seen: Set[str] = set()
    refined: List[str] = []

    def register(word: str) -> None:
        if len(word) < 2:
            return
        lower = word.lower()
        if lower in seen:
            return
        if lower in KEYWORD_STOPWORDS_LOWER:
            return
        seen.add(lower)
        refined.append(word)

    for kw in keywords or []:
        if not kw:
            continue
        register(kw.strip())

    for kw in extra_keywords or []:
        if not kw:
            continue
        register(str(kw).strip())

    return refined

# --- 프로젝트 키워드 헬퍼 함수 ---
def fetch_project_keywords(project_id):
    """
    프로젝트 키워드(도메인 태그)를 조회합니다.
    """
    keywords: List[str] = []
    if not project_id:
        return keywords

    if SQLA_ENABLED and session_scope and WorkspaceRepository:
        try:
            with session_scope() as db_session:
                project = WorkspaceRepository.get_project_by_id(db_session, int(project_id))
                if project:
                    raw_keywords = project.get('keywords') or []
                    if isinstance(raw_keywords, str):
                        keywords = [raw_keywords.strip()] if raw_keywords.strip() else []
                    elif isinstance(raw_keywords, list):
                        keywords = [
                            str(k).strip() for k in raw_keywords
                            if isinstance(k, (str, int, float)) and str(k).strip()
                        ]
                    return keywords
        except Exception as e:
            print(f"[WARN] SQLAlchemy 프로젝트 키워드 조회 실패 (project_id={project_id}): {e}")
    
    # Supabase fallback 제거: SQLAlchemy 미사용 환경에서는 빈 목록 반환
    return keywords


def _extract_request_user_id():
    user_id_header = request.headers.get('X-User-ID')
    if not user_id_header:
        return None, jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401
    try:
        return int(user_id_header), None, None
    except Exception:
        return user_id_header, None, None


def _resolve_owner_ids_sqlalchemy(user_id_int):
    owner_ids = [user_id_int]
    try:
        claims = get_jwt() or {}
    except Exception:
        claims = {}

    identity = get_jwt_identity()
    try:
        token_user_id = int(identity) if identity is not None else None
    except Exception:
        token_user_id = None

    tier = claims.get('tier')
    team_id = claims.get('team_id')

    if tier == 'enterprise' and session_scope and WorkspaceRepository:
        with session_scope() as db_session:
            if not team_id and token_user_id:
                team_id = WorkspaceRepository.get_primary_team_id_for_user(db_session, int(token_user_id))
            if team_id:
                member_ids = WorkspaceRepository.get_team_member_ids(db_session, int(team_id))
                if token_user_id and token_user_id not in member_ids:
                    member_ids.append(int(token_user_id))
                if member_ids:
                    owner_ids = member_ids

    return owner_ids

# --- 유틸리티 함수 ---
def parse_llm_json_response(raw_result):
    """LLM의 응답에서 JSON을 안전하게 파싱하는 함수"""
    if not raw_result or not raw_result.get('content'):
        raise ValueError('LLM 응답이 비어있습니다.')
    
    response_text = raw_result['content'].strip()
    
    # 코드 블록 제거 (json, python 등 모든 코드 블록)
    import re
    # ```json ... ``` 또는 ```python ... ``` 같은 코드 블록 제거
    response_text = re.sub(r'```(?:json|python)?\s*\n', '', response_text)
    response_text = re.sub(r'\n\s*```', '', response_text)
    response_text = response_text.strip()
    
    # 마크다운 헤더 제거 (##, ### 등)
    response_text = re.sub(r'^#+\s+.*$', '', response_text, flags=re.MULTILINE)
    response_text = response_text.strip()
    
    # 첫 번째 { 찾아서 시작
    start_idx = response_text.find('{')
    if start_idx != -1:
        response_text = response_text[start_idx:]
    
    # 마지막 } 찾아서 끝내기
    end_idx = response_text.rfind('}')
    if end_idx != -1:
        response_text = response_text[:end_idx+1]

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        # JSON 내 이스케이프 문자 문제 해결 시도
        print(f"⚠️ JSON 파싱 1차 실패: {e}")

        try:
            # 백슬래시 이스케이프 처리 (이미 이스케이프된 것은 제외)
            import re
            # \s, \d, \n 등 잘못된 이스케이프를 \\s, \\d, \\n으로 변경
            # 하지만 이미 올바른 \", \\, \/ 등은 그대로 유지
            fixed_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response_text)
            print(f"🔧 이스케이프 수정 시도...")
            return json.loads(fixed_text)
        except json.JSONDecodeError as e2:
            print(f"❌ JSON 파싱 2차 실패: {e2}")
            # 추가 디버깅 정보 포함하여 에러 메시지 개선
            raise ValueError(f"LLM 응답 JSON 파싱 실패: {e}")

# --- API 엔드포인트 ---

@app.route('/')
def hello():
    return "백엔드 서버 작동 중! 🚀"

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

# =====================================================================
# == [신규] 원샷 계획서 생성기 (폼 기반)
# =====================================================================

@app.route('/api/study-helper/chat', methods=['POST'])
@tier_required(['free'])
def study_helper_chat():
    """연구 생성 폼의 챗봇 도우미 (스트리밍 응답)"""
    try:
        data = request.json
        user_message = data.get('message', '')
        context = data.get('context', {})
        mode = data.get('mode', 'general')  # 'general' | 'help'
        task = data.get('task')
        
        # 현재 폼 데이터를 컨텍스트로 활용
        current_form = context.get('currentForm', {})
        project_name = context.get('projectName', '프로젝트')
        
        # 컨텍스트 정보 구성
        context_info = f"""
현재 작성 중인 연구:
- 프로젝트: {project_name}
- 연구명: {current_form.get('studyName', '(미입력)')}
- 문제정의: {current_form.get('problemDefinition', '(미입력)')}
- 선택된 방법론: {', '.join(current_form.get('methodologies', [])) or '(미선택)'}
- 조사대상: {current_form.get('targetAudience', '(미입력)')}
- 희망일정: {current_form.get('timeline', '(미입력)')}
"""

        # 카테고리별 전문 프롬프트 정의
        category = context.get('category', 'general')
        
        def get_methodology_prompt():
            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 추천 방법론은 2~3개만 제시하되, 각 방법론당 1~2문장으로 간단히 설명.
- 진행 방식, 장단점 상세 설명 금지. 선택 이유만 간단히 언급.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
- 주의사항은 1~2문장으로 제한.
"""
            return f"""
당신은 UX 리서치 방법론 전문가입니다.

{CONCISE_POLICY}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 추천 방법론 2~3개 나열 (각 방법론당 1~2문장)
- 각 방법론의 선택 이유 간단히 언급
- 주의사항 1~2문장

답변:
"""

        def get_target_audience_prompt():
            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 2~3문장 또는 불릿(-) 3~5개로 전달.
- 대상자 정의와 모집 전략만 간단히 제시.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
            return f"""
당신은 UX 리서치 대상자 선정 전문가입니다.

{CONCISE_POLICY}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 대상자 정의와 모집 전략 (2~3문장 또는 불릿 3~5개)
- 구체적인 실행 방안 간단히 제시

답변:
"""

        def get_timeline_prompt():
            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 2~3문장으로 전달.
- 일정 계획과 타임라인만 간단히 제시.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
            return f"""
당신은 UX 리서치 프로젝트 관리 전문가입니다.

{CONCISE_POLICY}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 일정 계획 (2~3문장)
- 대략적인 타임라인과 주의사항 간단히 제시

답변:
"""

        def get_budget_prompt():
            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 3문장내외로 전달.
- 예산 배분과 핵심 포인트만 간단히 제시.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
            return f"""
당신은 UX 리서치 예산 계획 전문가입니다.

{CONCISE_POLICY}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 예산 계획과 핵심 포인트 (3문장내외)
- 비용 배분과 절약 방안 간단히 제시

답변:
"""

        def get_problem_definition_prompt():
            # 문제 정의 입력 값 확인
            problem_def = current_form.get('problemDefinition', '').strip()
            
            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 전달.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
            
            # 문제 정의 입력이 있는 경우 - 개선 제안
            if problem_def and len(problem_def) > 20:
                return f"""
당신은 UX 리서치 문제 정의 전문가입니다.

{CONCISE_POLICY}

사용자가 작성한 문제 정의:
{problem_def}

현재 상황:
{context_info}

사용자 질문: {user_message}

위 문제 정의를 검토하고 다음과 같이 답변하세요:
입력해주신 내용에 따르면 
- 문제 정의의 잘 된 부분 인정 (1~2문장)
- 구체적으로 보완하거나 명확히 할 부분 제안 (2~3문장)
- 필요시 개선 예시 제시

답변:
"""
            # 문제 정의 입력이 없는 경우 - 예시 제공
            else:
                return f"""
당신은 UX 리서치 문제 정의 전문가입니다.

{CONCISE_POLICY}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 좋은 문제 정의의 핵심 특징 설명 (2문장)
- 구체적인 예시 1~2개 제시

답변:
"""

        def get_general_prompt():
            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 2~3문장 또는 불릿(-) 3~5개로 전달.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
 - 목록이 아닐 경우 불릿(-)을 사용하지 말 것.
 - 표( | )와 헤딩( # )은 사용 금지. 단락/줄바꿈만 사용.
"""
            role_line = f"[도움 작업]: {task}" if task else ""
            return f"""
당신은 UX 리서치 전문가입니다.

{CONCISE_POLICY}
{role_line}

[컨텍스트]
{context_info}

[사용자 입력]
{user_message}

[정확한 응답만 출력]
"""

        # 카테고리별 프롬프트 선택
        prompt_functions = {
            'methodology': get_methodology_prompt,
            'target': get_target_audience_prompt,
            'timeline': get_timeline_prompt,
            'budget': get_budget_prompt,
            'problem_definition': get_problem_definition_prompt,
            'general': get_general_prompt
        }
        
        get_prompt = prompt_functions.get(category, get_general_prompt)
        helper_prompt = get_prompt()

        # (후처리 제거: LLM이 직접 최종 형식을 출력하도록 함)

        # 레거시 페이로드(formData 기반) 호환 처리
        if not user_message:
            # 구 버전에서 formData만 보낼 수 있음
            legacy_form = data.get('formData') or {}
            if legacy_form:
                user_message = "현재 폼 기반으로 간결 조언을 제공해 주세요."
                # 컨텍스트에 결합
                context_form = {
                    'currentForm': legacy_form,
                    'projectName': context.get('projectName', '프로젝트')
                }
                # context_info 재생성
                current_form = context_form.get('currentForm', {})
                project_name = context_form.get('projectName', '프로젝트')
                context_info = f"""
현재 작성 중인 연구:
- 프로젝트: {project_name}
- 연구명: {current_form.get('studyName', '(미입력)')}
- 문제정의: {current_form.get('problemDefinition', '(미입력)')}
- 선택된 방법론: {', '.join(current_form.get('methodologies', [])) or '(미선택)'}
- 조사대상: {current_form.get('targetAudience', '(미입력)')}
- 희망일정: {current_form.get('timeline', '(미입력)')}
"""
                helper_prompt = get_prompt()

        # 생성 설정
        generation_config = {"temperature": 0.2, "max_output_tokens": 1000, "top_p": 0.9}
        if mode == 'help':
            generation_config = {"temperature": 0.1, "max_output_tokens": 1000, "top_p": 0.8}

        # 스트리밍 응답 생성
        def generate_streaming_response():
            try:
                result = openai_service.generate_response(helper_prompt, generation_config)
                if result['success']:
                    content = result['content']
                    # 문장 단위로 스트리밍
                    words = content.split(' ')
                    for i, word in enumerate(words):
                        chunk_data = {
                            'content': word + (' ' if i < len(words) - 1 else ''),
                            'done': i == len(words) - 1
                        }
                        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                        time.sleep(0.02)  # 더 빠른 출력
                else:
                    error_data = {'error': '응답 생성에 실패했습니다.', 'done': True}
                    yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_data = {'error': str(e), 'done': True}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        return app.response_class(
            generate_streaming_response(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*'
            }
        )
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generator/create-plan-oneshot', methods=['POST'])
@tier_required(['free'])
def generator_create_plan_oneshot():
    """원샷 계획서 생성기 - study 먼저 생성 후 즉시 반환, 계획서는 백그라운드 생성"""
    idempotency_key = None
    idempotency_completed = False
    created_study_id = None
    created_artifact_id = None

    def _parse_date(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
            except Exception:
                return None

    def _cleanup_created_records():
        nonlocal created_study_id, created_artifact_id
        if not session_scope:
            return
        try:
            with session_scope() as db_session:
                if created_artifact_id:
                    artifact = db_session.execute(
                        select(Artifact).where(Artifact.id == int(created_artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if artifact:
                        db_session.delete(artifact)
                    created_artifact_id = None
                if created_study_id:
                    study = db_session.execute(
                        select(Study).where(Study.id == int(created_study_id)).limit(1)
                    ).scalar_one_or_none()
                    if study:
                        db_session.delete(study)
                    created_study_id = None
        except Exception as cleanup_error:
            log_error(cleanup_error, "생성 실패 후 정리 작업 실패")

    def _fail_with(message: str, status: int = 500, cleanup: bool = False):
        nonlocal idempotency_completed
        if cleanup:
            _cleanup_created_records()
        error_payload = {'success': False, 'error': message}
        if idempotency_key:
            _fail_idempotency_entry(idempotency_key, error_payload, status)
            idempotency_completed = True
        return jsonify(error_payload), status

    try:
        if not SQLA_ENABLED or not session_scope:
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        log_api_call('/api/generator/create-plan-oneshot', 'POST', data)
        form_data = data.get('formData') or {}
        project_id = data.get('projectId')
        request_id = data.get('requestId') or data.get('request_id') or uuid.uuid4().hex

        problem_definition = (form_data.get('problemDefinition') or '').strip()
        study_name = (form_data.get('studyName') or '').strip()
        methodologies = form_data.get('methodologies') or []

        if not problem_definition:
            return jsonify({'success': False, 'error': '문제 정의는 필수입니다.'}), 400
        if not study_name:
            return jsonify({'success': False, 'error': '연구명은 필수입니다.'}), 400
        if not project_id:
            return jsonify({'success': False, 'error': 'projectId는 필수입니다.'}), 400

        try:
            project_id_int = int(project_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': '유효하지 않은 projectId 입니다.'}), 400

        user_id_header = request.headers.get('X-User-ID') or request.headers.get('x-user-id')
        if not user_id_header:
            return jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401
        try:
            user_id_int = int(user_id_header)
        except Exception:
            return jsonify({'success': False, 'error': '유효하지 않은 사용자 ID입니다.'}), 400

        idempotency_key = f"{user_id_int}:{project_id_int}:{request_id}"
        idempotency_entry, is_new_request = _reserve_idempotency_entry(idempotency_key)
        if not is_new_request:
            return _respond_from_entry(idempotency_entry)

        try:
            claims = get_jwt() or {}
        except Exception:
            claims = {}
        tier = claims.get('tier') or 'free'

        with session_scope() as db_session:
            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == project_id_int).limit(1)
            ).scalar_one_or_none()
            if owner_id is None:
                return _fail_with('프로젝트 정보를 찾을 수 없습니다.', 404)
            if int(owner_id) != int(user_id_int):
                return _fail_with('접근 권한이 없습니다.', 403)

            if tier == 'free':
                owned_project_ids = db_session.execute(
                    select(Project.id).where(Project.owner_id == user_id_int)
                ).scalars().all()
                study_count = 0
                if owned_project_ids:
                    study_count = db_session.execute(
                        select(func.count()).select_from(Study).where(Study.project_id.in_(owned_project_ids))
                    ).scalar_one() or 0
                plan_count = db_session.execute(
                    select(func.count()).select_from(Artifact).where(
                        Artifact.artifact_type == 'plan',
                        Artifact.owner_id == user_id_int
                    )
                ).scalar_one() or 0
                if study_count >= 1:
                    return _fail_with('Free 플랜에서는 스터디는 1개까지만 생성할 수 있습니다.', 403)
                if plan_count >= 1:
                    return _fail_with('Free 플랜에서는 계획서는 1개까지만 생성할 수 있습니다.', 403)

            project_keywords = fetch_project_keywords(project_id_int)
            study = Study(
                project_id=project_id_int,
                name=study_name,
                initial_input=problem_definition,
                keywords=methodologies,
                methodologies=methodologies,
                participant_count=int(form_data.get('participantCount')) if form_data.get('participantCount') else None,
                start_date=_parse_date(form_data.get('startDate')),
                end_date=_parse_date(form_data.get('endDate')),
                timeline=form_data.get('timeline') or None,
                budget=form_data.get('budget') or None,
                target_audience=form_data.get('targetAudience') or None,
                additional_requirements=form_data.get('additionalRequirements') or None,
            )
            db_session.add(study)
            db_session.flush()
            db_session.refresh(study)
            created_study_id = study.id

            artifact = Artifact(
                study_id=study.id,
                artifact_type='plan',
                content='',
                owner_id=int(owner_id),
                status='pending',
            )
            db_session.add(artifact)
            db_session.flush()
            db_session.refresh(artifact)
            created_artifact_id = artifact.id

            study_id = study.id
            study_slug = study.slug or ''
            artifact_id = artifact.id

        def generate_plan_background():
            try:
                log_expert_analysis("백그라운드 계획서 생성", f"시작: artifact_id={artifact_id}")
                response = handle_oneshot_parallel_experts(form_data, project_keywords)
                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if not target:
                        return
                    if response.get('success'):
                        target.content = response.get('final_plan', '')
                        target.status = 'completed'
                        log_analysis_complete()
                        log_data_processing(
                            "계획서 생성 완료",
                            {"artifact_id": artifact_id, "study_id": study_id},
                            "백그라운드 계획서 생성 성공",
                        )
                    else:
                        bg_session.delete(target)
            except Exception as e:
                log_error(e, f"백그라운드 계획서 생성 오류: artifact_id={artifact_id}, study_id={study_id}")
                try:
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            bg_session.delete(target)
                except Exception as delete_error:
                    log_error(delete_error, f"생성 오류 후 artifact 삭제 실패: artifact_id={artifact_id}")

        thread = threading.Thread(target=generate_plan_background, daemon=True)
        thread.start()

        response_payload = {
            'success': True,
            'study_id': study_id,
            'study_slug': study_slug,
            'artifact_id': artifact_id,
            'request_id': request_id,
            'message': '연구가 생성되었습니다. 계획서를 생성하고 있습니다...'
        }
        _complete_idempotency_entry(idempotency_key, response_payload, 200)
        idempotency_completed = True
        return jsonify(response_payload)
    except Exception as e:
        log_error(e, "원샷 계획서 생성")
        _cleanup_created_records()
        if idempotency_key and not idempotency_completed:
            _fail_idempotency_entry(idempotency_key, {'success': False, 'error': str(e)}, 500)
        return jsonify({'success': False, 'error': str(e)}), 500


# =====================================================================
# == [실험] ConversationStudyMaker (카드 누적형) - 추천 + 원샷 최종 생성
# =====================================================================

def _analyze_previous_step_selections(ledger_cards, step_int):
    """이전 단계에서 선택된 내용 분석"""
    analysis = {
        "selected_methodologies": [],
        "selected_goals": [],
        "selected_audiences": [],
        "selected_context": [],
    }
    
    for card in ledger_cards:
        if not isinstance(card, dict):
            continue
        card_type = str(card.get("type", "")).lower()
        title = str(card.get("title", "")).strip()
        content = str(card.get("content", "")).strip()
        
        if "methodology" in card_type:
            analysis["selected_methodologies"].append({
                "title": title,
                "content": content
            })
        elif "goal" in card_type or "hypothesis" in card_type or "question" in card_type:
            analysis["selected_goals"].append({
                "title": title,
                "content": content
            })
        elif "audience" in card_type or "quota" in card_type or "screener" in card_type:
            analysis["selected_audiences"].append({
                "title": title,
                "content": content
            })
        elif "context" in card_type or "project_context" in card_type:
            analysis["selected_context"].append({
                "title": title,
                "content": content
            })
    
    return analysis


def _ledger_cards_to_context_text(ledger_cards: object, max_chars: int = 12000) -> str:
    """프론트에서 누적한 카드(ledger)를 LLM 입력에 쓰기 좋은 텍스트로 직렬화."""
    if not isinstance(ledger_cards, list):
        return ""

    chunks: List[str] = []
    for idx, card in enumerate(ledger_cards):
        if not isinstance(card, dict):
            continue
        status = str(card.get("status", "") or "").strip()
        # selected/edited 카드 위주로 쓰되, 실험 단계에서는 전부 포함 (status가 비어있는 경우도 있음)
        card_type = str(card.get("type", "") or "note").strip()
        title = str(card.get("title", "") or "").strip()
        content = str(card.get("content", "") or "").strip()
        because = str(card.get("because", "") or "").strip()

        if not (title or content):
            continue

        chunk = f"""[CARD {idx + 1}]
type: {card_type}
status: {status or "unknown"}
title: {title or "(no title)"}
content:
{content}
"""
        if because:
            chunk += f"because: {because}\n"
        chunks.append(chunk)

    text = "\n\n".join(chunks).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[TRUNCATED]"
    return text


def _extract_selected_methodologies_from_ledger(ledger_cards: object) -> List[str]:
    if not isinstance(ledger_cards, list):
        return []

    methods: List[str] = []
    for card in ledger_cards:
        if not isinstance(card, dict):
            continue
        if str(card.get("type", "")).strip() != "methodology_set":
            continue
        fields = card.get("fields") if isinstance(card.get("fields"), dict) else {}
        raw_methods = fields.get("methods")
        if isinstance(raw_methods, list):
            for m in raw_methods:
                if isinstance(m, str) and m.strip():
                    methods.append(m.strip())
    # de-dupe preserving order
    seen: Set[str] = set()
    out: List[str] = []
    for m in methods:
        if m.lower() in seen:
            continue
        seen.add(m.lower())
        out.append(m)
    return out


def _safe_parse_json_object(raw: object) -> Optional[dict]:
    """
    LLM 응답에서 JSON 객체를 안전하게 추출한다.
    - 기존 `parse_llm_json_response`는 {"content": "..."} 형태(dict)를 기대하므로,
      여기서는 str/dict 모두 처리하도록 래핑한다.
    """
    try:
        # 1) 기존 호환: dict 형태면 기존 파서 사용
        if isinstance(raw, dict):
            parsed = parse_llm_json_response(raw)
            return parsed if isinstance(parsed, dict) else None

        # 2) 문자열이면 JSON 블록을 직접 추출/파싱
        if not isinstance(raw, str):
            return None

        text = raw.strip()
        if not text:
            return None

        # 코드 블록(JSON) 우선 추출
        json_str = None
        import re
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text, re.DOTALL)
        if m:
            json_str = m.group(1)
        else:
            # 첫 번째 { ... 마지막 }까지 (가장 보수적인 방식)
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = text[start:end + 1]

        if not json_str:
            return None

        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, dict) else None

    except Exception:
        return None


@app.route('/api/conversation/message', methods=['POST'])
@tier_required(['free'])
def send_conversation_message():
    """대화형 리서치 생성기 - 사용자 메시지 처리 및 AI 응답 생성."""
    start_time = time.time()
    try:
        data = request.json or {}
        log_api_call('/api/conversation/message', 'POST', data)

        step = data.get('step', 0)
        mode = data.get('mode', 'recommend')
        conversation = data.get('conversation', []) or []
        ledger_cards = data.get('ledger_cards', []) or []
        project_id = data.get('projectId')

        try:
            step_int = int(step)
        except (TypeError, ValueError):
            step_int = 0

        ledger_text = _ledger_cards_to_context_text(ledger_cards, max_chars=4000)
        
        # 최근 대화를 더 강조하기 위해 최근 메시지 우선 추출
        conversation_text = "\n".join(
            [
                f"{msg.get('type', 'user')}: {msg.get('content', '')}"
                for msg in conversation
                if isinstance(msg, dict)
            ]
        )
        
        # 최근 사용자 입력 추출 (마지막 3개 메시지)
        recent_user_messages = [
            msg.get('content', '') 
            for msg in conversation[-6:]  # 최근 6개 메시지 (user + ai 쌍)
            if isinstance(msg, dict) and msg.get('type') == 'user'
        ][-3:]  # 최근 사용자 메시지 3개
        recent_user_input = "\n".join(recent_user_messages) if recent_user_messages else ""

        combined_input = f"""[LEDGER]
{ledger_text}

[CONVERSATION]
{conversation_text}
""".strip()

        # 프로젝트 키워드 (선택)
        project_keywords: List[str] = []
        try:
            if project_id is not None:
                project_keywords = fetch_project_keywords(int(project_id))
        except Exception:
            project_keywords = []

        # RAG 검색: 별도 요약 함수 의존성을 두지 않고, 입력 자체를 안전 길이로 잘라 키워드 추출
        concise_source = combined_input[:5000]
        keywords = extract_contextual_keywords_from_input(concise_source)
        if project_keywords:
            keywords = _refine_extracted_keywords(keywords, project_keywords)
        log_keyword_extraction(keywords)

        # Step별 RAG 검색 설정 (대화 단계에 맞는 참고자료를 더 정확히 주입)
        # - Step0~3: topics 필터를 명확히 적용
        # - Step4(추가 요구사항): 상황 의존적이라 topics 필터를 걸지 않고, 쿼리 접두어로만 유도
        RAG_TOPICS_BY_STEP = {
            0: ["조사목적", "연구목표", "리서치질문", "계획서"],
            1: ["가설", "리서치질문", "연구질문"],
            2: ["방법론", "방법", "방법 설계"],
            3: ["대상자", "참가자모집", "스크리너"],
            4: None,  # Step4는 topic 필터 미적용
        }
        RAG_QUERY_PREFIX_BY_STEP = {
            0: "리서치 배경, 상황, 조사 목적, 계획서(배경/상황)",
            1: "목적, 연구 목표, 리서치 질문, 연구질문, 가설",
            2: "방법론, 방법, 방법 설계, 세션 설계",
            3: "대상자, 참가자모집, 스크리너",
            4: "추가 요구사항, 제약조건, task 설계, 시나리오, 관찰 포인트, 편향 제거, 리스크",
        }

        step_topics = RAG_TOPICS_BY_STEP.get(step_int, RAG_TOPICS_BY_STEP[0])
        rag_prefix = RAG_QUERY_PREFIX_BY_STEP.get(step_int, RAG_QUERY_PREFIX_BY_STEP[0])
        rag_query = f"UX 리서치 계획서 설계 ({rag_prefix}): {', '.join(keywords)}"

        rag_results = vector_service.improved_service.hybrid_search(
            query_text=rag_query,
            principles_n=2,
            examples_n=2,
            topics=step_topics,
            domain_keywords=project_keywords
        )
        log_step_search_clean("conversation-maker-recommend", rag_query, rag_results, "카드 후보 생성용 컨텍스트")

        principles_context = vector_service.improved_service.context_optimization(
            rag_results["principles"], max_length=1200
        )
        examples_context = vector_service.improved_service.context_optimization(
            rag_results["examples"], max_length=1000
        )

        # 이전 단계에서 선택된 내용 분석
        previous_analysis = _analyze_previous_step_selections(ledger_cards, step_int)
        
        step_goal_map = {
            0: "[상황값 명확화] 이 단계에서는 리서치를 시작하게 된 배경과 상황을 명확히 하는 것이 목표입니다. 핵심 맥락(리스크/사용 맥락/검증할 화면·기능)을 먼저 파악하여 컨텍스트를 고해상도로 만들기. 사용자가 이 단계에서 '어떤 상황에서 어떤 문제를 해결하려는지'를 구체적으로 생각할 수 있도록 도와주세요.",
            1: "[목적값 명확화] 이 단계에서는 리서치의 목적, 연구 질문, 가설을 명확히 하는 것이 목표입니다. 목표/연구질문/가설 후보 카드를 많이 생성하여 사용자가 '이번 조사로 무엇을 결정하고 싶은지'를 구체적으로 생각할 수 있도록 도와주세요.",
            2: "[방법론값 명확화] 이 단계에서는 리서치 방법론과 세션 설계를 명확히 하는 것이 목표입니다. 이전 단계에서 선택한 목적/가설을 바탕으로 방법론/세션 설계 후보 카드를 생성하여 사용자가 '어떤 방법으로 조사할지'를 구체적으로 생각할 수 있도록 도와주세요.",
            3: "[대상값 명확화] 이 단계에서는 조사 대상과 스크리너 기준을 명확히 하는 것이 목표입니다. 대상/쿼터/스크리너(필수/제외) 후보 카드를 생성하여 사용자가 '누구를 대상으로 조사할지'를 구체적으로 생각할 수 있도록 도와주세요.",
            4: "[추가 요구사항 명확화] 이 단계에서는 지금까지 수집한 정보를 종합 분석하여, 리서치 설계를 더욱 구체화하기 위해 필요한 추가 요구사항을 판단하고 제안합니다. 예: UT/IDI의 경우 task/시나리오, 특정 기능/화면 집중 관찰 포인트, 편향 제거 고려사항, 추가 제약사항 등. 사용자가 '추가로 무엇을 고려해야 하는지'를 구체적으로 생각할 수 있도록 도와주세요.",
        }
        step_goal = step_goal_map.get(step_int, step_goal_map[0])
        
        # 단계별 맞춤형 컨텍스트 생성
        context_summary = ""
        if step_int == 2:  # 방법론 단계
            if previous_analysis["selected_goals"]:
                goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:3]])
                context_summary += f"이미 설정된 목적: {goals_text}\n"
            if previous_analysis["selected_methodologies"]:
                methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
                context_summary += f"⚠️ 이미 선택된 방법론이 있습니다: {methods_text}\n"
                context_summary += "이 경우, 선택된 방법론의 세부 설계나 추가 방법론 제안에 집중하세요.\n"
        
        elif step_int == 3:  # 대상 단계
            if previous_analysis["selected_methodologies"]:
                methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
                context_summary += f"선택된 방법론: {methods_text}\n"
                # 방법론에 따라 대상 선정 기준 제안
                has_ut = any("ut" in m["title"].lower() or "usability" in m["title"].lower() or "사용성" in m["title"].lower()
                             for m in previous_analysis["selected_methodologies"])
                has_interview = any("interview" in m["title"].lower() or "인터뷰" in m["title"].lower() 
                                   for m in previous_analysis["selected_methodologies"])
                
                if has_ut:
                    context_summary += "UT의 경우: 경험 유무, 사용 빈도, 숙련도가 중요한 기준입니다.\n"
                if has_interview:
                    context_summary += "인터뷰의 경우: 페르소나, 세그먼트, 행동 패턴이 중요한 기준입니다.\n"
            if previous_analysis["selected_goals"]:
                goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:2]])
                context_summary += f"설정된 목적: {goals_text}\n"
        
        elif step_int == 4:  # 추가 요구사항 단계
            # 지금까지의 모든 선택을 종합하여 추가로 필요한 정보를 판단하도록 컨텍스트 제공
            context_summary += "이 단계는 지금까지 수집한 정보를 바탕으로, 리서치 설계를 더욱 구체화하기 위해 필요한 추가 요구사항을 수집하는 단계입니다.\n"
            if previous_analysis["selected_methodologies"]:
                methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
                context_summary += f"선택된 방법론: {methods_text}\n"
                has_ut = any("ut" in m["title"].lower() or "usability" in m["title"].lower() or "사용성" in m["title"].lower()
                             for m in previous_analysis["selected_methodologies"])
                if has_ut:
                    context_summary += "→ UT/IDI 방법론이 선택되었으므로, task/시나리오나 관찰 포인트가 필요할 수 있습니다.\n"
            if previous_analysis["selected_goals"]:
                goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:2]])
                context_summary += f"설정된 목적: {goals_text}\n"
            if previous_analysis["selected_audiences"]:
                audiences_text = ", ".join([a["title"] for a in previous_analysis["selected_audiences"][:2]])
                context_summary += f"설정된 대상: {audiences_text}\n"
            context_summary += "→ 현재 설계에서 보완이 필요한 부분(예: 특정 기능/화면 집중, 편향 제거, 추가 제약사항 등)을 판단하여 질문하고 카드를 생성하세요.\n"
            context_summary += "→ **중요: Step4(추가 요구사항)는 '추론'이 핵심입니다. LEDGER에 없는 사실을 단정하지는 말되, 부족한 정보/리스크를 추론해 카드로 제안하세요. (카드 0개 금지)\n"

        schema_hint = {
            "draft_cards": [
                {
                    "id": "string",
                    "type": "project_context|research_goal|hypothesis|scope_item|audience_segment|quota_plan|screener_rule|methodology_set|task|analysis_plan|note",
                    "title": "string",
                    "content": "string",
                    "because": "string",
                    "fields": {},
                    "tags": ["string"]
                }
            ],
            "next_question": {
                "title": "string",
                "content": "string",
                "because": "string"
            },
            "message": "string"
        }

        interrogation_rules = ""
        if step_int == 0:
            interrogation_rules = """
[Step0 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요. (사용자가 답하면 다음 추천이 더 뾰족해지는 질문)
- **질문 생성 시 필수: CONVERSATION에 있는 사용자 입력 내용을 구체적으로 분석하여, 입력 내용에서 부족한 정보를 파악하고 질문하세요.**
- 질문은 존댓말로, 자연스럽고 열린 형태로 작성하세요.
- 질문 형식 예시 : "[사용자 입력 내용 요약]'을 보면, [부족한 정보]를 더 알 수 있으면 리서치 설계가 더 구체화할 수 있을 것 같아요. [구체적인 질문]"
- 질문 주제 우선순위:
  1) 검증하고 싶은 화면/기능/시나리오 (구체적으로)
  2) 타겟 유저의 대표 사용 상황이나 맥락
  3) 이번 조사에서 가장 중요한 리스크나 고려사항
- draft_cards는 "선택/누적 가능한 내용 카드"만 생성하세요. 질문형/물음표 문장은 절대 draft_cards에 넣지 마세요.
- **draft_cards 생성 개수 (매우 중요)**: 
  * **최소 5개, 최대 10개를 반드시 생성하세요.**
  * 사용자 입력이 짧거나 간단해도, 입력 내용에서 추론 가능한 다양한 상황/컨텍스트를 생성해야 합니다.
  * 사용자가 명시적으로 언급하지 않은 부분도 유추하여 카드를 생성하세요.
- **유저 입력 기반 추론 강화 (핵심)**:
  * 사용자 입력의 각 문장, 각 키워드, 각 맥락에서 파생될 수 있는 다양한 상황을 추론하세요.
  * **LEDGER와 도메인 정보 활용**: LEDGER에 선택된 카드가 있다면, 그것과 사용자 입력을 연결하여 통합적인 관점에서 추론하세요. 도메인 정보(프로젝트 키워드, 참고 원칙/예시)를 활용하여 해당 도메인에 특화된 맥락을 추가하세요.
  * 예시: 사용자가 "신규 기능 테스트"라고 입력했다면 → 
    - 신규 기능의 배경/목적 (왜 만들었는지 추론)
    - 신규 기능의 타겟 사용자 (누구를 위한 것인지 추론)
    - 신규 기능의 사용 맥락/시나리오 (언제/어디서 사용하는지 추론)
    - 신규 기능과 기존 기능의 관계 (대체/보완/확장 추론)
    - 신규 기능 검증 시 고려해야 할 제약사항 (기술적/비즈니스적 리스크 추론)
    - 신규 기능과 관련된 경쟁 대안/비교 대상 추론
  * 사용자가 "사용성 개선"이라고 입력했다면 →
    - 어떤 화면/플로우의 사용성인지 (전체/특정 부분 추론)
    - 현재 사용성 문제의 징후/증거 (CS 피드백, 데이터, 관찰 등 추론)
    - 사용성 개선의 목표/기대 효과 추론
    - 사용성 개선이 필요한 사용자 그룹 (신규/기존, 헤비/라이트 등 추론)
    - 사용성 개선 시 고려해야 할 제약사항 (기술적/비즈니스적 리스크 추론)
  * 이렇게 하나의 입력에서도 최소 6개 이상의 다양한 상황/컨텍스트 카드를 생성할 수 있어야 합니다.
  * **카드 content는 풍부하게**: 각 카드의 content 필드에 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 구체적으로 작성하세요.
- 선택지는 너무 일반적이지 않고 주제에서 벗어나지 않게 뾰족하고 구체적인 요소들로 많이 생성하세요. (6~10개 권장)
- **핵심 의도**: 사용자가 말하지 않은 부분도 "가능성의 범위"를 열어두되, 아무말 대잔치가 되지 않게 '상황에 실제로 자주 생기는 분기'만 과감하게 제시하세요.
  * 예: 타겟이 B2C인지 B2B인지, 신규/기존 유저인지, 사용 빈도(헤비/라이트)인지, 특정 기능 플로우인지 전체 경험인지, 온라인/오프라인 맥락, 기기/채널, 경쟁 대안 등
- **draft_cards 작성 금지 규칙**:
  * title/content는 **절대 '?'로 끝내지 마세요.** (질문은 next_question에만)
  * content는 1~3줄로, "관찰 가능한 사실/상황/제약" 위주로 작성하되, **추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 풍부하게** 작성하세요. (추상적 표현 금지)
- **절대 규칙 - 카드 타입 제한**: Step 0에서는 **오직 project_context와 scope_item 타입만** 생성하세요. 다른 타입(research_goal, hypothesis, methodology_set, audience_segment, quota_plan, screener_rule, task, analysis_plan, note 등)은 절대 생성하지 마세요.
"""
        elif step_int == 1:
            interrogation_rules = """
[Step1 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요. (사용자가 답하면 다음 추천이 더 뾰족해지는 질문)
- **질문 생성 시 필수: LEDGER와 CONVERSATION을 구체적으로 분석하여 부족한 정보를 파악하고 질문하세요.**
- 질문은 존댓말로, 자연스럽고 열린 형태로 작성하세요.
- 질문 형식: "'[선택한 목적 카드 제목]'을 설정하셨는데, 이 목적을 달성하기 위해 [부족한 정보]를 더 알 수 있으면 좋겠어요. [구체적인 질문]"
- draft_cards는 "선택/누적 가능한 내용 카드"만 생성하세요. 질문형/물음표 문장은 절대 draft_cards에 넣지 마세요.
- draft_cards는 최소 7개, 최대 10개 생성하세요. (0개는 금지)
- **카드 구성 강제**:
  * research_goal 타입: 최소 3개
  * hypothesis 타입: 최소 4개
- **추론 확장 강화 (핵심)**:
  * 사용자 입력과 LEDGER를 바탕으로, 명시적으로 말하지 않은 부분도 적극적으로 추론하여 카드를 생성하세요.
  * LEDGER에 선택된 카드가 있다면, 그것과 사용자 입력을 연결하여 통합적인 관점에서 목적/가설을 추론하세요.
  * 도메인 정보(프로젝트 키워드, 참고 원칙/예시)를 활용하여 해당 도메인에 특화된 목적이나 가설을 추론하세요.
  * 예: 사용자가 "사용성 개선"이라고 입력했고, LEDGER에 "B2B 서비스" 카드가 있다면 → "B2B 서비스의 사용성 개선 시 고려사항" (구매 결정자 vs 실제 사용자, 기업 내부 승인 프로세스 등)을 추론하여 목적/가설 카드 생성
- 선택지는 너무 일반적이지 않고 주제에서 벗어나지 않게 뾰족하고 구체적인 요소들로 많이 생성하세요.
- **가설 품질 기준 (매우 중요)**:
  * **검증 대상 확인 필수**: CONVERSATION과 LEDGER를 분석하여 검증하고자 하는 대상이 사용자(유저)인지, 서비스/기능인지, 비즈니스 전략인지 등을 먼저 파악하세요.
  * **사용자(유저) 대상 가설 생성 규칙 (매우 중요)**:
    - 검증 대상이 사용자(유저)인 경우, 가설은 **추론적이고 상황 기반**으로 작성하세요.
    - 형식: "[특정 상황/맥락]에서 [타겟 사용자]는 [어떤 기능/니즈/행동]이 있을 것이다 / [어떤 행동]을 할 것이다"
    - 예시:
      * "모바일 앱에서 신규 사용자가 첫 로그인 시 온보딩 튜토리얼을 완료하지 않고 바로 메인 화면으로 이동하려는 니즈가 있을 것이다"
      * "주문 취소를 시도하는 사용자들은 취소 사유 입력 단계에서 상세한 이유를 적기보다는 간단한 선택지로 빠르게 처리하고 싶어할 것이다"
      * "야간 시간대에 서비스를 이용하는 사용자들은 다크모드 전환 기능을 더 자주 사용할 것이다"
      * "장바구니에 상품을 담고도 결제를 완료하지 않는 사용자들은 가격 비교나 배송 정보 확인을 위해 다른 탭으로 이동할 것이다"
    - 핵심: 사용자의 **상황/맥락**을 먼저 제시하고, 그 상황에서의 **기능/니즈/행동**을 추론하는 형태로 작성하세요.
    - "~할 것이다", "~있을 것이다" 같은 추론적 표현을 적극 활용하되, 구체적인 상황과 행동을 포함하세요.
  * **일반 가설 품질 기준** (사용자 대상이 아닌 경우):
    * **검증 가능한 형태 필수**: "~일 것이다" 같은 단순 선언 금지. 조건/행동/결과가 명확히 드러나야 함.
    * **비교축 권장**: 가능하면 비교축을 넣으세요. (예: 신규 vs 기존, A 플로우 vs B 플로우, 모바일 vs 데스크탑)
    * **구체성 필수**: 추상적인 표현 금지. 예: "사용자 경험이 개선될 것이다" (X) → "신규 온보딩 플로우 적용 시 첫 사용자의 작업 완료율이 기존 대비 20% 이상 증가할 것이다" (O)
  * **공통 금지 사항**:
    - "~수 있다", "~가능하다" 같은 모호한 표현 금지 (단, 사용자 대상 가설에서 "~있을 것이다"는 추론적 표현으로 허용)
    - "사용자 만족도가 높을 것이다" 같은 주관적 판단만 있는 가설 금지
    - "기능이 좋을 것이다" 같은 일반론 금지
  * **좋은 가설 예시 (사용자 대상)**:
    - "[상황]에서 [사용자]는 [기능/니즈]가 있을 것이다" 형식: "모바일 앱에서 신규 사용자가 첫 로그인 시 온보딩 튜토리얼을 건너뛰고 바로 메인 화면으로 이동하려는 니즈가 있을 것이다"
    - "[상황]에서 [사용자]는 [행동]을 할 것이다" 형식: "주문 취소를 시도하는 사용자들은 취소 사유 입력 단계에서 상세한 이유를 적기보다는 간단한 선택지로 빠르게 처리하고 싶어할 것이다"
  * **좋은 가설 예시 (일반)**:
    - "베타 버전의 [구체적 기능]을 경험한 사용자가 정식 출시 후 해당 기능 사용 빈도가 베타 미경험 사용자 대비 2배 이상 높을 것이다"
    - "모바일 앱의 [A 화면] 탐색 패턴이 데스크탑의 [B 화면] 탐색 패턴과 차이를 보이며, 모바일 사용자는 평균 3단계 이내에 목표 지점에 도달한다"
- **draft_cards 작성 금지 규칙**:
  * title/content는 **절대 '?'로 끝내지 마세요.** (질문은 next_question에만)
  * content는 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 풍부하게 작성하세요.
- **절대 규칙 - 카드 타입 제한**: Step 1에서는 **오직 research_goal과 hypothesis 타입만** 생성하세요. 다른 타입(project_context, scope_item, methodology_set, audience_segment, quota_plan, screener_rule, task, analysis_plan, note 등)은 절대 생성하지 마세요.
"""
        elif step_int == 2:
            interrogation_rules = """
[Step2 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요. (사용자가 답하면 다음 추천이 더 뾰족해지는 질문)
- **질문 생성 시 필수: LEDGER와 CONVERSATION을 구체적으로 분석하여 부족한 정보를 파악하고 질문하세요.**
- 질문은 존댓말로, 자연스럽고 열린 형태로 작성하세요.
- 질문 형식: "'[선택한 방법론 카드 제목]'을 선택하셨는데, 이 방법론으로 조사할 때 [부족한 정보]를 더 알 수 있으면 세부 설계가 더 명확해질 것 같아요. [구체적인 질문]"
- draft_cards는 "선택/누적 가능한 내용 카드"만 생성하세요. 질문형/물음표 문장은 절대 draft_cards에 넣지 마세요.
- draft_cards는 최소 2개, 최대 5개 생성하세요. (0개는 금지)
- **핵심 의도**: “많이 나열”이 아니라, 상황/목적/가설에 맞는 **적은 수의 방법론**을 제시하고, 각 방법론의 **장단점/리스크/준비물/운영 팁**을 함께 제공하세요.
- **각 methodology_set 카드 content 포맷(권장)**:
  - 언제 적합: …
  - 장점: …
  - 리스크/주의: …
  - 운영/세팅 체크: (시간/인원/리서처 수/툴/리크루팅 난이도)
- **draft_cards 작성 금지 규칙**:
  * title/content는 **절대 '?'로 끝내지 마세요.** (질문은 next_question에만)
  * content는 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 풍부하게 작성하세요. 위 포맷을 참고하되, 각 방법론에 대한 구체적인 추론 내용을 포함하세요.
- **절대 규칙 - 카드 타입 제한**: Step 2에서는 **오직 methodology_set 타입만** 생성하세요. 다른 타입(project_context, scope_item, research_goal, hypothesis, audience_segment, quota_plan, screener_rule, task, analysis_plan, note 등)은 절대 생성하지 마세요.
"""
        elif step_int == 3:
            interrogation_rules = """
[Step3 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요. (사용자가 답하면 다음 추천이 더 뾰족해지는 질문)
- **질문 생성 시 필수: LEDGER와 CONVERSATION을 구체적으로 분석하여 부족한 정보를 파악하고 질문하세요.**
- 질문은 존댓말로, 자연스럽고 열린 형태로 작성하세요.
- 질문 형식: "'[선택한 대상 카드 제목]'을 설정하셨는데, 이 대상을 더 정확히 모집하기 위해 [부족한 정보]를 더 알 수 있으면 좋겠어요. [구체적인 질문]"
- draft_cards는 "선택/누적 가능한 내용 카드"만 생성하세요. 질문형/물음표 문장은 절대 draft_cards에 넣지 마세요.
- draft_cards는 최소 6개, 최대 10개 생성하세요. (0개는 금지)
- **카드 구성 강제**:
  * audience_segment 타입: 최소 2개
  * quota_plan 타입: 최소 2개 (균형 요소/쿼터 축을 반드시 포함)
  * screener_rule 타입: 최소 2개 (필수/제외 조건 모두 포함되게)
- **핵심 의도**: “그럴듯한 타겟”을 1개 던지는 게 아니라, 이 상황에서 반드시 고려해야 할 **필수요소/균형요소/제외요소**를 다양하게 다뤄서 모집이 바로 가능하게 하세요.
- **screener_rule 품질 기준**:
  * 질문 형태로 끝내지 말고(물음표 금지), **규칙/조건 형태**로 작성하세요.
  * 예: "포함: 최근 30일 내 [행동] 경험 / 제외: 업계 종사자, 내부 직원"
- **draft_cards 작성 금지 규칙**:
  * title/content는 **절대 '?'로 끝내지 마세요.** (질문은 next_question에만)
  * content는 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 풍부하게 작성하세요.
- **절대 규칙 - 카드 타입 제한**: Step 3에서는 **오직 audience_segment, quota_plan, screener_rule 타입만** 생성하세요. 다른 타입(project_context, scope_item, research_goal, hypothesis, methodology_set, task, analysis_plan, note 등)은 절대 생성하지 마세요.
"""
        elif step_int == 4:
            interrogation_rules = """
[Step4 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요. (사용자가 답하면 다음 추천이 더 뾰족해지는 질문)
- **질문 생성 시 필수: LEDGER와 CONVERSATION을 구체적으로 분석하여, 지금까지의 모든 선택을 종합하여 부족한 정보를 파악하고 질문하세요.**
- 질문은 존댓말로, 자연스럽고 열린 형태로 작성하세요.
LEDGER의 실제 목적·방법론·대상 카드명을 구체적으로 넣고, 이번 리서치에서 진짜로 부족한 1~2가지([부족한 정보]+[구체적 질문])만 짚어서 자연스럽게 질문하세요.
- draft_cards는 "선택/누적 가능한 내용 카드"만 생성하세요. 질문형/물음표 문장은 절대 draft_cards에 넣지 마세요.
- **핵심 의도(매우 중요)**: Step4는 사용자가 '준 정보'만 재진술하면 실패입니다. **추론해서 만들어내세요.**
  * LEDGER에 없는 사실을 단정하지 말고, "가정/가능성"으로 제시하세요.
  * 카드 내용에 "가정:" / "확인 필요:" 같은 표기를 써도 되지만 **물음표(?)는 쓰지 마세요.**
  * **LEDGER와 도메인 정보를 활용한 풍부한 추론**: LEDGER의 모든 선택된 카드들(목적, 방법론, 대상 등)을 종합 분석하여, 빠지기 쉬운 추가 요구사항/리스크/제약/운영 요소를 추론하세요. 도메인 정보(프로젝트 키워드, 참고 원칙/예시)를 활용하여 해당 도메인에 특화된 추가 요구사항을 추론하세요.
- draft_cards는 **최소 3개, 최대 8개** 생성하세요. (0개 금지)
- **중요 - scope_item 고려**: Step 0에서 다루지 못했거나, Step 1~3에서 개발된 내용(목적, 방법론, 대상 등)을 바탕으로 추가로 필요한 조사 범위나 제약사항이 있다면 scope_item 타입으로 추천하세요.
  * 예: 특정 기능/화면에 집중해야 하는 경우
  * 예: 편향을 제거하기 위한 추가 제약사항
  * 예: 방법론이나 대상 설정에 따라 새로 생긴 조사 범위 제한
- **카드 타입**: task, analysis_plan, scope_item, note 타입을 사용 가능합니다.
  * UT/IDI 방법론이 선택된 경우: task, analysis_plan 타입 우선 고려
  * 특정 기능/화면 집중이 필요한 경우: task, scope_item 타입 고려
  * 편향 제거나 추가 제약이 필요한 경우: scope_item, note 타입 고려
- **카드 개수 가이드**:
  * 기본: 2~8개 생성 (0개는 지양)
  * UT/IDI가 선택되어 있으면: task 최소 3개 + analysis_plan 최소 1개를 포함하세요.
- **draft_cards 작성 금지 규칙**:
  * title/content는 **절대 '?'로 끝내지 마세요.** (질문은 next_question에만)
  * content는 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 풍부하게 작성하세요.
"""

        # 이전 단계 결과 요약 생성
        previous_summary = ""
        if context_summary:
            previous_summary = f"\n[이전 단계 결과 분석]\n{context_summary}\n"
        
        # 이전 단계에서 선택된 카드가 있는지 확인
        has_previous_selections = len(ledger_cards) > 0
        
        # 단계 전환 모드인 경우 (conversation이 비어있을 때)
        is_step_transition = not conversation_text.strip()
        transition_hint = ""
        if is_step_transition:
            if has_previous_selections:
                # 각 단계의 기본 목적을 간단히 요약
                step_purpose_map = {
                    0: "리서치를 시작하게 된 배경이나 상황",
                    1: "리서치의 목적, 연구 질문, 또는 가설",
                    2: "리서치의 방법론이나 설계에 대한 정보",
                    3: "조사 대상이나 스크리너 기준",
                    4: "리서치 설계를 더욱 구체화하기 위해 필요한 추가 정보"
                }
                step_purpose = step_purpose_map.get(step_int, "이번 단계의 정보")
                
                transition_hint = f"""
[단계 전환 모드 - 이전 단계 선택 있음]
- 사용자가 이전 단계에서 선택한 카드들을 기반으로 이번 단계를 시작합니다.
- **중요**: 프론트엔드에서 이미 기본 프롬프트가 표시되므로, message 필드는 선택사항입니다.
- message 필드를 생성하는 경우: 이전 단계 선택을 구체적으로 언급하고, 이번 단계({step_purpose})로 자연스럽게 이어지도록 하세요.
- 예시: "1,2단계에서 '사용성 테스트(UT)' 방법론을 선택하셨네요. UT 세부 설계를 위해..."
- 예시: "목적 단계에서 '신규 사용자 온보딩 개선'을 설정하셨으니, 이제 방법론을..."
- 예시: "이미 방법론이 선택되어 있으니, 추가 방법론이나 세부 설계에 집중해보면 좋겠어요."
- message 필드를 생성하지 않아도 됩니다 (기본 프롬프트가 이미 표시되므로).
"""
            else:
                # 이전 단계 선택이 없는 경우 (1단계에서 바로 2단계로 넘어온 경우 등)
                transition_hint = """
[단계 전환 모드 - 새 단계 시작]
- 사용자가 이전 단계에서 아직 카드를 선택하지 않았습니다.
- 이번 단계의 기본 프롬프트를 따르고, 이전 단계 선택을 언급하지 마세요.
- message 필드에서 이번 단계의 목적과 필요성을 자연스럽게 안내하세요.
- 예시: "이제 목적과 연구 질문을 설정해보시면 좋겠어요."
- 예시: "방법론을 선택하기 전에, 시간/리소스 제약이나 정성/정량 우선순위를 알려주시면 도움이 됩니다."
"""
        
        prompt = f"""
당신은 시니어 UX 리서처입니다. 사용자가 선택해서 누적한 카드(LEDGER)와 대화 내용(CONVERSATION)을 근거로, 다음 단계를 더 뾰족하게 만들 후보 카드를 생성하세요.

**[역할 및 맥락]**
- 당신은 리서치 설계를 도와주는 AI 어시스턴트입니다.
- 모든 메시지는 리서치 설계자(서비스 사용자)에게 직접 말하는 형식으로 작성하세요.
- **next_question 생성 시 필수: LEDGER와 CONVERSATION을 구체적으로 분석하여 부족한 정보를 파악하고 질문하세요.**
- next_question의 because 필드는, 이 질문에 답변해주시면 어떤 도움이 되는지 자연스럽게 설명하세요.

[중요한 원칙]
- **⚠️ 매우 중요: 최근 사용자 입력에 최우선 집중하되, 적극적으로 추론하여 확장하세요**
  * CONVERSATION의 **가장 최근 사용자 입력**이 가장 중요합니다. 이 입력을 기반으로 새로운 카드를 생성하세요.
  * **추론 확장이 핵심**: 사용자가 명시적으로 말하지 않은 부분도, 입력 내용에서 파생될 수 있는 맥락, 시나리오, 고려사항, 리스크, 기회 등을 적극적으로 추론하여 카드로 생성하세요.
  * 예시: 사용자가 "신규 기능 테스트"라고 입력했다면 → 
    - 신규 기능의 배경/목적 (왜 만들었는지 추론)
    - 신규 기능의 타겟 사용자 세그먼트 (누구를 위한 것인지 추론)
    - 신규 기능의 사용 맥락/시나리오 (언제/어디서 사용하는지 추론)
    - 신규 기능과 기존 기능의 관계 (대체/보완/확장 추론)
    - 신규 기능 검증 시 고려해야 할 제약사항 (기술적/비즈니스적 리스크 추론)
    - 신규 기능과 관련된 경쟁 대안/비교 대상 추론
  * **LEDGER와 도메인 정보를 활용한 풍부한 추론**: LEDGER에 있는 선택된 카드들과 도메인 정보(프로젝트 키워드, 참고 원칙/예시)를 참고하여, 사용자 입력을 더 풍부하게 확장하세요.
    - LEDGER의 선택된 카드들과 사용자 입력을 연결하여, 통합적인 관점에서 새로운 카드를 생성하세요.
    - 도메인 정보(프로젝트 키워드, 참고 원칙/예시)를 활용하여 해당 도메인에 특화된 맥락이나 고려사항을 추론하세요.
    - 예: 사용자가 "사용성 개선"이라고 입력했고, LEDGER에 "B2B 서비스" 카드가 있다면 → "B2B 서비스의 사용성 개선 시 고려사항" (구매 결정자 vs 실제 사용자, 기업 내부 승인 프로세스 등)을 추론하여 카드 생성
- **컨텍스트 종합 분석**: CONVERSATION 전체를 종합해서 핵심 키워드와 맥락을 파악하세요. 최근 입력을 중심으로 하되, 대화 초반부터 끝까지 전체 맥락을 통합해서 이해하세요.
  * 예: 사용자가 "베타서비스가 있다"고 시작했고, 나중에 "홈 as-is to-be" 예시를 제시했다면, "홈"에만 집중하지 말고 "베타서비스"라는 핵심 맥락과 함께 종합해서 고려하세요.
- **중복 방지 (완화된 기준)**: 
  * 완전히 동일한 내용의 카드는 생성하지 마세요.
  * 하지만 같은 주제라도 **새로운 관점, 맥락, 시나리오, 고려사항**이 있으면 새로운 카드로 생성하세요.
  * 예: "사용성 개선"이라는 카드가 LEDGER에 있어도, 최근 입력에서 "모바일 앱의 특정 플로우"라는 새로운 맥락이 있으면 → "모바일 앱 [특정 플로우] 사용성 개선" 카드를 새로 생성 가능
- **풍부한 추론을 통한 카드 생성**: 
  * 사용자 입력의 각 문장, 각 키워드, 각 맥락에서 파생될 수 있는 다양한 상황을 추론하세요.
  * LEDGER의 선택된 카드들과 연결하여, 통합적인 관점에서 새로운 카드를 생성하세요.
  * 도메인 정보를 활용하여 해당 도메인에 특화된 맥락을 추가하세요.
- 후보는 "과감하게" 구체적으로 제시하세요. (유저가 취사선택할 수 있어야 함)
- 교과서 설명 금지. 일반론 금지. 추상적 표현 금지.
- 각 선택지는 너무 일반적이지 않고 주제에서 벗어나지 않게 뾰족하고 구체적인 요소로 생성하세요.
- 이 단계는 사용자가 조사에 대한 생각을 키워나갈 수 있는 대화 공간입니다. 선택지를 통해 사용자가 더 깊이 생각하고 결정할 수 있도록 도와주세요.
- 표( | ) 금지. 마크다운 불필요.
- 오직 JSON 하나만 출력하세요.

{previous_summary}

[현재 목표]
{step_goal}

{interrogation_rules}

{transition_hint}

[CONVERSATION - 지금까지의 대화 (⚠️ 최근 입력에 집중하세요)]
{conversation_text if conversation_text.strip() else "(새 단계 시작)"}

[LEDGER - 지금까지 선택된 카드들 (참고용)]
{ledger_text if ledger_text.strip() else "(선택된 카드 없음)"}

[참고 원칙]
{principles_context}

[참고 예시]
{examples_context}

[출력 요구사항]
- message 필드: 
  * Step 4 (추가 요구사항)의 경우, 지금까지의 모든 선택을 종합하여 "지금까지 A(목적), B(방법론), C(대상)를 설정하셨으니, 리서치 설계를 더욱 구체화하기 위해 추가로 필요한 정보를 함께 고려해보면 좋겠어요."와 같이 작성하세요.
  * 이전 단계에서 선택된 카드가 있는 경우에 선택 내용을 구체적으로 언급하세요.
  * "지금까지 A와 B를 결정하셨고, 이제 C를 고민하고 계시는 것 같아요. D와 E도 함께 고려해보시면 더 좋은 리서치 설계가 될 것 같습니다."와 같이 구체적으로 작성하세요.
  * **추론 확장**: 사용자 입력과 LEDGER를 바탕으로, 추가로 고려할 수 있는 맥락이나 시나리오를 추론하여 언급하세요.
  * LEDGER에 명시적으로 없는 내용이라도, 사용자 입력과 LEDGER를 바탕으로 합리적으로 추론할 수 있는 내용은 언급 가능합니다.
- next_question 필드: 
  * **매우 중요: LEDGER와 CONVERSATION을 구체적으로 분석하여 부족한 정보를 파악하고, 선택한 카드나 입력 내용을 구체적으로 언급하면서 질문하세요.**
  * **질문 생성 방법 (매우 중요 - 컨텍스트 종합 분석)**:
    1. **CONVERSATION 전체를 먼저 종합 분석**: 대화 초반부터 최근까지 전체 맥락을 파악하세요. 핵심 키워드(예: "베타서비스", "신규 기능", "기존 서비스 개선" 등)와 사용자의 의도를 통합적으로 이해하세요.
    2. **LEDGER 분석**: 선택된 카드들의 title과 content를 분석하세요.
    3. **중복 체크**: 이미 CONVERSATION에서 다뤘거나 LEDGER에 있는 내용과 겹치는 질문은 하지 마세요.
    4. **통합 관점**: 최근 입력만 보지 말고, 전체 대화 맥락에서 **부족하거나 더 구체화할 수 있는 정보**를 파악하세요.
    5. **구체적 언급**: 선택된 카드나 CONVERSATION의 핵심 키워드를 구체적으로 언급하면서 "이런 정보를 더 알 수 있으면 좋겠다"는 식으로 질문하세요.
  * **단계별 질문 예시:**
    - Step 0: "입력하신 '[사용자 입력 내용 요약]'을 보면, [부족한 정보]를 더 알 수 있으면 리서치 설계가 더 구체적이 될 것 같아요. [구체적인 질문]"
    - Step 1: "'[선택한 목적 카드 제목]'을 설정하셨는데, 이 목적을 달성하기 위해 [부족한 정보]를 더 알 수 있으면 좋겠어요. [구체적인 질문]"
    - Step 2: "'[선택한 방법론 카드 제목]'을 선택하셨는데, 이 방법론으로 조사할 때 [부족한 정보]를 더 알 수 있으면 세부 설계가 더 명확해질 것 같아요. [구체적인 질문]"
    - Step 3: "'[선택한 대상 카드 제목]'을 설정하셨는데, 이 대상을 더 정확히 모집하기 위해 [부족한 정보]를 더 알 수 있으면 좋겠어요. [구체적인 질문]"
    - Step 4: "지금까지 '[목적 요약]', '[방법론 요약]', '[대상 요약]'을 설정하셨는데, 리서치 설계를 더욱 구체화하기 위해 [부족한 정보]를 더 알 수 있으면 좋겠어요. [구체적인 질문]"
  * **절대 금지:**
    - 일반적이고 추상적인 질문 금지 (예: "추가로 알려주실 수 있나요?")
    - 선택된 카드나 입력 내용을 언급하지 않는 질문 금지
    - LEDGER나 CONVERSATION에 없는 내용을 가정한 질문 금지
  * 질문의 이유(because)도 포함하세요. 이유는 "이 정보를 알면 [구체적인 도움]을 받을 수 있어서" 형식으로 작성하세요.
- draft_cards 필드: 
  * **⚠️ 매우 중요: 최근 사용자 입력 기반 추론 확장 카드 생성**
    * CONVERSATION의 **가장 최근 사용자 입력**을 분석하여 새로운 카드를 생성하세요.
    * **추론 확장이 핵심**: 사용자가 명시적으로 말하지 않은 부분도, 입력 내용에서 파생될 수 있는 맥락, 시나리오, 고려사항, 리스크, 기회 등을 적극적으로 추론하여 카드로 생성하세요.
    * **LEDGER와 도메인 정보 활용**: LEDGER의 선택된 카드들과 도메인 정보(프로젝트 키워드, 참고 원칙/예시)를 참고하여, 사용자 입력을 더 풍부하게 확장하세요.
      - LEDGER의 선택된 카드들과 사용자 입력을 연결하여, 통합적인 관점에서 새로운 카드를 생성하세요.
      - 도메인 정보를 활용하여 해당 도메인에 특화된 맥락이나 고려사항을 추론하세요.
      - 예: 사용자가 "사용성 개선"이라고 입력했고, LEDGER에 "B2B 서비스" 카드가 있다면 → "B2B 서비스의 사용성 개선 시 고려사항" (구매 결정자 vs 실제 사용자, 기업 내부 승인 프로세스 등)을 추론하여 카드 생성
    * LEDGER에 이미 있는 카드와 유사해도, 최근 입력의 새로운 맥락이나 관점이 있으면 새로운 카드로 생성하세요.
    * 이전에 생성된 카드를 단순히 반복하지 말고, 최근 입력에서 파악한 새로운 정보를 반영하고, 추론을 통해 확장하세요.
    * **카드 content 필드는 풍부하게**: title을 단순 반복하지 말고, 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 구체적으로 작성하세요.
  * **단계별 카드 타입 제한 (절대 규칙 - 반드시 준수):**
    - Step 0 (상황 컨텍스트): **오직 project_context, scope_item 타입만** 생성하세요. 다른 모든 타입은 절대 생성하지 마세요.
    - Step 1 (목적/가설): **오직 research_goal, hypothesis 타입만** 생성하세요. 다른 모든 타입은 절대 생성하지 마세요.
    - Step 2 (방법론): **오직 methodology_set 타입만** 생성하세요. 다른 모든 타입은 절대 생성하지 마세요.
    - Step 3 (대상): **오직 audience_segment, quota_plan, screener_rule 타입만** 생성하세요. 다른 모든 타입은 절대 생성하지 마세요.
    - Step 4 (추가 요구사항): **지금까지의 설계 맥락을 종합 분석하여, 가장 필요한 추가 정보를 판단하여 카드 생성**
      * **중요 - scope_item 고려**: Step 0에서 다루지 못했거나, Step 1~3에서 개발된 내용(목적, 방법론, 대상 등)을 바탕으로 추가로 필요한 조사 범위나 제약사항이 있다면 scope_item 타입으로 추천하세요.
      * **Step4는 '추론'이 핵심**입니다. 지금까지의 선택을 바탕으로, 빠지기 쉬운 추가 요구사항/리스크/제약/운영 요소를 **추론해서 카드로 제안**하세요.
      * **카드 0개 금지**: 최소 3개 이상은 반드시 생성하세요.
      * UT/IDI 방법론이 선택된 경우: task, analysis_plan 타입 우선 고려
      * 특정 기능/화면 집중이 필요한 경우: task, scope_item 타입 고려
      * 편향 제거나 추가 제약이 필요한 경우: scope_item, note 타입 고려
      * 필요에 따라 task, analysis_plan, scope_item, note 타입을 사용 가능하지만, 현재 단계에 맞는 타입 위주로 생성하세요.
  * **매우 중요 - 절대 규칙**: 
    * 각 단계에서는 해당 단계에 명시된 카드 타입만 생성하세요. 
    * 다른 단계의 카드 타입은 절대 생성하지 마세요.
    * 예를 들어, Step 2에서는 methodology_set만 생성하고, scope_item, project_context, research_goal, hypothesis, audience_segment, quota_plan, screener_rule 등은 절대 생성하지 마세요.
    * Step 3에서는 audience_segment, quota_plan, screener_rule만 생성하고, scope_item, methodology_set 등은 절대 생성하지 마세요.
  * 이전 단계의 선택이 있는 경우, 그 선택과 일관성 있게 카드를 생성하되, 최근 입력의 새로운 맥락을 반영하세요.
  * 선택/누적 가능한 구체적인 카드들을 생성하세요.
  * **카드 구조 (매우 중요):**
    - title 필드: 간결하고 명확한 제목 (예: "신규 기능의 사용성 검증")
    - content 필드: title보다 더 세부적인 설명이나 구체적인 내용 + 추론한 맥락, 시나리오, 고려사항, 리스크 등을 포함하여 풍부하게 작성
      * 예: "정식 버전에서 추가될 신규 기능이 사용자의 기대에 부합하는지를 검증하기 위한 목표. 사용자가 실제로 해당 기능을 사용하면서 어떤 경험을 하는지, 기대와의 차이는 무엇인지 등을 확인합니다. [추론 확장] 특히 신규 기능이 기존 워크플로우와 통합될 때 발생할 수 있는 학습 곡선이나 사용 패턴 변화, 기존 기능과의 충돌 가능성 등도 함께 관찰해야 합니다."
      * content는 title의 단순 반복이 아니라, title을 더 구체적으로 설명하거나 확장한 내용 + 사용자 입력과 LEDGER/도메인 정보를 바탕으로 추론한 추가 맥락, 시나리오, 고려사항을 포함해야 합니다.
  * **Step 1 hypothesis 타입 카드 생성 시 특별 주의사항:**
    - CONVERSATION과 LEDGER를 분석하여 검증 대상이 사용자(유저)인지 확인하세요.
    - 사용자(유저) 대상인 경우: "[상황/맥락]에서 [타겟 사용자]는 [기능/니즈/행동]이 있을 것이다 / [행동]을 할 것이다" 형식의 추론적 가설을 생성하세요.
      * 예시 title: "신규 사용자의 온보딩 건너뛰기 니즈"
      * 예시 content: "모바일 앱에서 신규 사용자가 첫 로그인 시 온보딩 튜토리얼을 완료하지 않고 바로 메인 화면으로 이동하려는 니즈가 있을 것이다. 이는 사용자가 서비스의 핵심 기능을 빠르게 경험하고 싶어하기 때문일 수 있다."
    - 사용자 대상이 아닌 경우: 기존 가설 품질 기준(검증 가능한 형태, 비교축, 구체성)을 따르세요.
  * Step 0~3: 최소 1개 이상, 최대 10개까지 생성하세요. (0개는 금지)
  * Step 4: **최소 3개 이상** 생성하세요. (0개 금지)
  * Step 0~3: 선택지는 너무 일반적이지 않고 주제에서 벗어나지 않게 뾰족하고 구체적인 요소들로 많이 생성하세요. (5~10개 권장)

[출력 스키마 예시]
{json.dumps(schema_hint, ensure_ascii=False, indent=2)}
"""

        llm_result = openai_service.generate_response(prompt, {"temperature": 0.4})
        if not llm_result.get('success'):
            raise Exception(llm_result.get('error', 'LLM 호출 실패'))

        parsed = _safe_parse_json_object(llm_result.get('content', '')) or {}
        draft_cards = parsed.get("draft_cards", [])
        next_question = parsed.get("next_question")
        missing_questions = parsed.get("missing_questions", [])
        message = parsed.get("message", "추천을 생성했습니다. 필요한 카드만 선택해 누적하세요.")

        # 각 단계에 허용된 카드 타입 정의
        def get_allowed_card_types(step):
            if step == 0:
                return ["project_context", "scope_item"]
            elif step == 1:
                return ["research_goal", "hypothesis"]
            elif step == 2:
                return ["methodology_set"]
            elif step == 3:
                return ["audience_segment", "quota_plan", "screener_rule"]
            elif step == 4:
                return ["task", "analysis_plan", "scope_item", "note"]  # Step 4는 유연하게
            return []
        
        # draft_cards에 섞인 질문형 카드를 제거하고, next_question 후보로 승격
        # 또한 각 단계에 맞지 않는 카드 타입도 필터링
        extracted_question = None
        allowed_types = get_allowed_card_types(step_int)
        if isinstance(draft_cards, list):
            filtered_cards = []
            for c in draft_cards:
                if not isinstance(c, dict):
                    continue
                c_type = str(c.get("type") or "").lower()
                c_title = str(c.get("title") or "").strip()
                c_content = str(c.get("content") or "").strip()
                
                # 질문형 카드 제거
                is_question_like = ("question" in c_type) or c_title.endswith("?") or c_content.endswith("?")
                if is_question_like and extracted_question is None:
                    extracted_question = {
                        "title": c_title or "추가 질문",
                        "content": c_content or c_title,
                        "because": str(c.get("because") or "").strip()
                    }
                    continue
                if is_question_like:
                    continue
                
                # Step 0~3: 허용된 타입만 통과 (Step 4는 유연하게)
                if step_int < 4:
                    type_matches = any(allowed_type in c_type for allowed_type in allowed_types)
                    if not type_matches:
                        # 허용되지 않은 타입은 제외 (로그는 남기지 않음, 조용히 필터링)
                        continue
                
                filtered_cards.append(c)
            draft_cards = filtered_cards

        # next_question가 없으면(구버전) missing_questions 또는 draft_cards에서 추출한 질문을 사용
        if not isinstance(next_question, dict):
            if isinstance(missing_questions, list) and len(missing_questions) > 0:
                q0 = missing_questions[0] if isinstance(missing_questions[0], dict) else {}
                next_question = {
                    "title": (q0.get("title") or "추가 질문"),
                    "content": (q0.get("content") or q0.get("title") or ""),
                    "because": ""
                }
            elif extracted_question is not None:
                next_question = extracted_question
            else:
                next_question = None

        # 응답 호환성: 더 이상 missing_questions를 UI로 사용하지 않으므로 비워서 반환
        missing_questions = []

        response_payload = {
            "success": True,
            "draft_cards": draft_cards if isinstance(draft_cards, list) else [],
            "missing_questions": [],
            "next_question": next_question,
            "message": message,
            "step": step_int,
            "mode": mode,
        }

        duration = time.time() - start_time
        log_performance("send_conversation_message", duration, f"step_{step_int}")
        return jsonify(response_payload)

    except Exception as e:
        log_error(e, "Conversation message 오류")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/generator/conversation-maker/finalize-oneshot', methods=['POST'])
@tier_required(['free'])
def conversation_maker_finalize_oneshot():
    """카드 누적형 ConversationStudyMaker - Study+pending plan artifact 생성 후 백그라운드 계획서 생성."""
    start_time = time.time()
    idempotency_key = None
    idempotency_completed = False
    created_study_id = None
    created_artifact_id = None

    try:
        if not SQLA_ENABLED or not session_scope:
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        log_api_call('/api/generator/conversation-maker/finalize-oneshot', 'POST', data)

        project_id = data.get('projectId')
        study_name = (data.get('studyName') or '').strip()
        ledger_cards = data.get('ledger_cards') or []
        request_id = data.get('requestId') or data.get('request_id') or uuid.uuid4().hex

        if not project_id:
            return jsonify({"success": False, "error": "projectId는 필수입니다."}), 400
        if not study_name:
            return jsonify({"success": False, "error": "studyName은 필수입니다."}), 400
        if not isinstance(ledger_cards, list) or len(ledger_cards) == 0:
            return jsonify({"success": False, "error": "ledger_cards가 비어있습니다."}), 400

        user_id_header = request.headers.get('X-User-ID') or request.headers.get('x-user-id')
        if not user_id_header:
            return jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401
        try:
            user_id_int = int(user_id_header)
        except Exception:
            return jsonify({'success': False, 'error': '유효하지 않은 사용자 ID입니다.'}), 400

        try:
            project_id_int = int(project_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': '유효하지 않은 projectId 입니다.'}), 400

        idempotency_key = f"{user_id_int}:{project_id_int}:{request_id}"
        idempotency_entry, is_new_request = _reserve_idempotency_entry(idempotency_key)
        if not is_new_request:
            return _respond_from_entry(idempotency_entry)

        def cleanup_created_records():
            nonlocal created_study_id, created_artifact_id
            try:
                with session_scope() as db_session:
                    if created_artifact_id:
                        artifact = db_session.execute(
                            select(Artifact).where(Artifact.id == int(created_artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if artifact:
                            db_session.delete(artifact)
                        created_artifact_id = None
                    if created_study_id:
                        study = db_session.execute(
                            select(Study).where(Study.id == int(created_study_id)).limit(1)
                        ).scalar_one_or_none()
                        if study:
                            db_session.delete(study)
                        created_study_id = None
            except Exception as cleanup_error:
                log_error(cleanup_error, "생성 실패 후 정리 작업 실패")

        def fail_with(message: str, status: int = 500, cleanup_fn=None):
            nonlocal idempotency_completed
            if cleanup_fn:
                try:
                    cleanup_fn()
                except Exception as cleanup_error:
                    log_error(cleanup_error, f"실패 처리 중 정리 작업 실패: {message}")
            error_payload = {'success': False, 'error': message}
            _fail_idempotency_entry(idempotency_key, error_payload, status)
            idempotency_completed = True
            return jsonify(error_payload), status

        try:
            claims = get_jwt()
        except Exception:
            claims = {}
        tier = (claims or {}).get('tier') or 'free'
        project_keywords = fetch_project_keywords(project_id_int)
        ledger_text = _ledger_cards_to_context_text(ledger_cards, max_chars=12000)
        selected_methods = _extract_selected_methodologies_from_ledger(ledger_cards)

        try:
            ledger_json = json.dumps(ledger_cards, ensure_ascii=False)
        except Exception:
            ledger_json = "[]"

        with session_scope() as db_session:
            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == project_id_int).limit(1)
            ).scalar_one_or_none()
            if owner_id is None:
                return fail_with('프로젝트 정보를 찾을 수 없습니다.', 404)
            if int(owner_id) != int(user_id_int):
                return fail_with('접근 권한이 없습니다.', 403)

            if tier == 'free':
                owned_project_ids = db_session.execute(
                    select(Project.id).where(Project.owner_id == user_id_int)
                ).scalars().all()
                study_count = 0
                if owned_project_ids:
                    study_count = db_session.execute(
                        select(func.count()).select_from(Study).where(Study.project_id.in_(owned_project_ids))
                    ).scalar_one() or 0
                plan_count = db_session.execute(
                    select(func.count()).select_from(Artifact).where(
                        Artifact.artifact_type == 'plan',
                        Artifact.owner_id == user_id_int
                    )
                ).scalar_one() or 0
                if study_count >= 1:
                    return fail_with('Free 플랜에서는 스터디는 1개까지만 생성할 수 있습니다.', 403)
                if plan_count >= 1:
                    return fail_with('Free 플랜에서는 계획서는 1개까지만 생성할 수 있습니다.', 403)

            study = Study(
                project_id=project_id_int,
                name=study_name,
                initial_input=(ledger_text[:800] + '…') if len(ledger_text) > 800 else ledger_text,
                keywords=selected_methods,
                methodologies=selected_methods,
                additional_requirements=f"[CONTEXT_PACK_JSON]\n{ledger_json}",
            )
            db_session.add(study)
            db_session.flush()
            db_session.refresh(study)
            created_study_id = study.id

            artifact = Artifact(
                study_id=study.id,
                artifact_type='plan',
                content='',
                owner_id=int(owner_id),
                status='pending',
            )
            db_session.add(artifact)
            db_session.flush()
            db_session.refresh(artifact)
            created_artifact_id = artifact.id

            study_id = study.id
            study_slug = study.slug or ''
            artifact_id = artifact.id

        def generate_plan_background():
            try:
                log_expert_analysis("ConversationStudyMaker 최종계획서", f"시작: artifact_id={artifact_id}")

                # RAG 검색 (ledger 기반)
                keywords = extract_contextual_keywords_from_input(ledger_text)
                if project_keywords:
                    keywords = _refine_extracted_keywords(keywords, project_keywords)
                log_keyword_extraction(keywords)

                rag_query = f"조사 계획서, 연구 설계, 사용성 테스트, 인터뷰, 세션 설계, 분석 계획: {', '.join(keywords)}"
                rag_results = vector_service.improved_service.hybrid_search(
                    query_text=rag_query,
                    principles_n=8,
                    examples_n=4,
                    topics=["조사목적", "연구목표", "방법론", "대상자", "계획서", "사용성테스트", "인터뷰", "조사 설계", "리서치질문"],
                    domain_keywords=project_keywords
                )
                log_step_search_clean("conversation-maker-finalize", rag_query, rag_results, "최종 계획서 생성 컨텍스트")

                principles_context = vector_service.improved_service.context_optimization(
                    rag_results["principles"], max_length=1800
                )
                examples_context = vector_service.improved_service.context_optimization(
                    rag_results["examples"], max_length=1300
                )

                final_prompt = f"""
당신은 15년차 시니어 UX 리서처입니다. 아래 '선택된 카드(LEDGER)'를 1차 근거로 삼아, 실무자가 바로 실행 가능한 **리서치 설계 프레임**을 작성하세요.

[이번 버전 범위]
- 스크리너 설문/가이드라인/상세 Task 설계는 포함하지 않습니다. (별도 기능에서 생성)
- 대신 "무엇을 검증/관찰할지"의 프레임(관찰 포인트, 성공 신호, 위험요인, 세션 구성)을 명확히 합니다.

[중요 규칙]
- LEDGER에 없는 사실을 '있는 것처럼' 만들지 마세요. 부족한 정보는 마지막에 '추가로 확인할 질문'으로 명시하세요.
- 교과서형 일반론 금지. 추상적 문장 금지. 이 프로젝트 맥락(베타/마이그레이션/CS 등)에 맞춰 구체화하세요.
- 표( | ) 금지.
- 인사/서론/확인 멘트 없이 바로 결과물로 시작.

[프로젝트 키워드]
{', '.join(project_keywords) if project_keywords else '(없음)'}

[사용자가 선택한 방법론(있다면)]
{', '.join(selected_methods) if selected_methods else '(미확정)'}

[선택된 카드(LEDGER)]
{ledger_text}

[참고 원칙]
{principles_context}

[참고 예시]
{examples_context}

[출력 형식]
# [프로젝트 명] 리서치 계획서

## 1. 리서치 배경
## 2. 리서치 목표 및 검증 가설
## 3. 검증 대상 및 범위
## 4. 대상자 설계
## 5. 리서치 방법 및 세션 시나리오
## 6. 주요 지표
## 7. 관찰 및 분석 프레임
## 8. 예상 산출물
"""

                final_result = gemini_service.generate_response(
                    final_prompt,
                    {"temperature": 0.3},
                    model_name="gemini-2.5-pro"
                )

                if not final_result.get('success'):
                    raise Exception(final_result.get('error', '최종 생성 실패'))

                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if not target:
                        return
                    target.content = final_result.get('content', '')
                    target.status = 'completed'

                log_analysis_complete()
                log_data_processing("ConversationStudyMaker 계획서 생성 완료", {"artifact_id": artifact_id, "study_id": study_id}, "성공")

            except Exception as e:
                log_error(e, f"ConversationStudyMaker 계획서 생성 실패: artifact_id={artifact_id}, study_id={study_id}")
                try:
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            target.status = 'failed'
                            target.content = f'❌ 생성 실패: {str(e)}'
                except Exception as delete_error:
                    log_error(delete_error, f"ConversationStudyMaker 실패 후 artifact 업데이트 실패: artifact_id={artifact_id}")

        thread = threading.Thread(target=generate_plan_background, daemon=True)
        thread.start()

        response_payload = {
            'success': True,
            'study_id': study_id,
            'study_slug': study_slug,
            'artifact_id': artifact_id,
            'request_id': request_id,
            'message': '연구가 생성되었습니다. 계획서를 생성하고 있습니다...'
        }
        _complete_idempotency_entry(idempotency_key, response_payload, 200)
        idempotency_completed = True
        duration = time.time() - start_time
        log_performance("conversation_maker_finalize_oneshot", duration)
        return jsonify(response_payload)

    except Exception as e:
        log_error(e, "ConversationStudyMaker finalize 오류")
        try:
            if 'cleanup_created_records' in locals():
                cleanup_created_records()
        except Exception as cleanup_error:
            log_error(cleanup_error, "예외 처리 중 정리 작업 실패")
        if idempotency_key and not idempotency_completed:
            _fail_idempotency_entry(idempotency_key, {'success': False, 'error': str(e)}, 500)
        return jsonify({'success': False, 'error': str(e)}), 500


def handle_oneshot_parallel_experts(form_data, project_keywords: Optional[List[str]] = None):
    """원샷 방식: 폼 데이터 기반으로 8개 전문가 병렬 호출 → Pro 모델로 최종 취합"""
    try:
        import concurrent.futures
        project_keywords = [
            kw for kw in (project_keywords or []) if isinstance(kw, str) and kw.strip()
        ]
        
        # 폼 데이터에서 정보 추출
        problem_definition = form_data.get('problemDefinition', '')
        study_name = form_data.get('studyName', '')
        methodologies = form_data.get('methodologies', [])  # 사용자가 선택한 방법론
        target_audience = form_data.get('targetAudience', '')
        participant_count = form_data.get('participantCount', '')
        start_date = form_data.get('startDate', '')
        timeline = form_data.get('timeline', '')
        budget = form_data.get('budget', '')
        additional_requirements = form_data.get('additionalRequirements', '')
        
        # 통합 입력 텍스트 생성
        combined_input = f"""
연구명: {study_name}

문제 정의: {problem_definition}

선택된 방법론: {', '.join(methodologies) if methodologies else '(AI가 추천)'}
조사 대상: {target_audience if target_audience else '(AI가 추천)'}
참여 인원: {str(participant_count) + '명' if participant_count else '(AI가 추천)'}
시작 예정일: {start_date if start_date else '(미정)'}
연구 기간: {timeline if timeline else '(AI가 추천)'}
추가 요청사항: {additional_requirements if additional_requirements else '(없음)'}
"""
        
        # RAG 검색: 입력 요약 + 도메인 키워드
        keywords = extract_contextual_keywords_from_input(problem_definition)
        if project_keywords:
            keywords = _refine_extracted_keywords(keywords, project_keywords)
        log_keyword_extraction(keywords)
        
        rag_query = f"조사 계획서, 연구 설계: {', '.join(keywords)}"
        rag_results = vector_service.improved_service.hybrid_search(
            query_text=rag_query,
            principles_n=5,
            examples_n=4,
            topics=["조사목적", "연구목표", "방법론", "대상자", "일정", "예산", "계획서"],
            domain_keywords=project_keywords
        )
        
        log_step_search_clean("원샷-RAG검색", rag_query, rag_results, "전문가 호출용 컨텍스트")
        
        principles_context = vector_service.improved_service.context_optimization(
            rag_results["principles"], max_length=2500
        )
        examples_context = vector_service.improved_service.context_optimization(
            rag_results["examples"], max_length=2000
        )

        # 방법론 전문가 먼저 실행
        log_expert_analysis("방법론 전문가", "우선 실행")
        methodology_prompt = GenerationPrompts.prompt_generate_methodology_fit(
            combined_input, principles_context, examples_context
        )
        methodology_result = openai_service.generate_response(methodology_prompt, {"temperature": 0.4})
        
        if not methodology_result['success']:
            raise Exception(f"방법론 전문가 호출 실패: {methodology_result.get('error')}")
        
        methodology_expert_result = {
            'expert': '방법론 적합성',
            'content': methodology_result['content'],
            'success': True
        }
        
        methodology_result_content = methodology_result['content']  # 방법론 결과 내용
        
        # 나머지 7개 전문가 프롬프트 함수 (방법론 결과 포함 + 일정 전문가)
        expert_configs = [
            ("연구 목표", GenerationPrompts.prompt_generate_research_goal),
            ("핵심 질문", GenerationPrompts.prompt_generate_core_questions),
            ("조사 대상", GenerationPrompts.prompt_generate_target_audience),
            ("참여자 기준", GenerationPrompts.prompt_generate_participant_criteria),
            ("분석 방법", GenerationPrompts.prompt_generate_analysis_method),
            ("일정 및 타임라인", GenerationPrompts.prompt_generate_timeline),
            ("액션 플랜", GenerationPrompts.prompt_generate_action_plan)
        ]
        
        # 전문가 병렬 호출 (방법론 결과 포함)
        def call_expert(expert_name, prompt_func):
            try:
                # 방법론 결과를 포함한 combined_input 생성
                combined_input_with_methodology = f"""{combined_input}

**[방법론 전문가 결과]**
{methodology_result_content}
"""
                
                # 전문가별로 방법론 결과 활용 방식이 다름
                if expert_name == "분석 방법":
                    # 분석 방법은 methodology_result 파라미터로 받음
                    prompt = prompt_func(combined_input, methodology_result_content, principles_context, examples_context)
                else:
                    # 나머지는 combined_input에 방법론 결과가 포함됨
                    prompt = prompt_func(combined_input_with_methodology, principles_context, examples_context)
                
                result = openai_service.generate_response(prompt, {"temperature": 0.3})
                if result['success']:
                    return {'expert': expert_name, 'content': result['content'], 'success': True}
                return {'expert': expert_name, 'error': result.get('error'), 'success': False}
            except Exception as e:
                return {'expert': expert_name, 'error': str(e), 'success': False}
        
        log_expert_analysis("7개 전문가", "병렬 호출 시작 (방법론 결과 포함 + 일정 전문가)")
        
        # 병렬 실행
        expert_results = [methodology_expert_result]  # 방법론 결과 먼저 추가
        with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
            futures = [executor.submit(call_expert, name, func) for name, func in expert_configs]
            for future in concurrent.futures.as_completed(futures):
                expert_results.append(future.result())
        
        # 성공한 전문가만 필터
        successful_experts = [r for r in expert_results if r['success']]
        
        if len(successful_experts) < 7:  # 방법론 포함하여 최소 8개 (방법론 + 7개 전문가)
            raise Exception(f"전문가 호출 실패: {len(successful_experts)}/8 성공")
        
        # 전문가 결과 취합
        expert_outputs = "\n\n".join([
            f"### {r['expert']} 분석:\n{r['content']}" 
            for r in successful_experts
        ])

        #방법론 처리 로직 추가
        if methodologies and len(methodologies) > 0:
            methodology_instruction = f"""
**✅ 사용자가 선택한 방법론: {', '.join(methodologies)}**

**⚠️ 매우 중요한 방법론 필터링 규칙:**
1. **오직 선택된 방법론만 사용:** 위에 나열된 방법론만 계획서에 포함하세요.
2. **선택되지 않은 방법론 완전 제외:** 전문가가 추천한 다른 모든 방법론은 언급조차 하지 마세요.
3. **대상자 통합:** 선택된 방법론이 여러 개라도, 대상자는 하나의 통합된 그룹으로 구성하세요.
   - 각 방법론별로 대상자를 따로 구분하지 마세요.
   - 예: "인터뷰용 대상자 그룹 A, 사용성 테스트용 대상자 그룹 B" ❌
   - 예: "대상자 그룹: 인터뷰와 사용성 테스트를 함께 수행할 수 있는 통합 그룹" ✅
4. **일정 통합:** 선택된 방법론이 여러 개라도, 일정은 하나의 통합된 일정으로 작성하세요.
   - 방법론별로 일정을 분리하지 마세요.
   - 예: "2주차: 심층 인터뷰, 3주차: 사용성 테스트" ❌
   - 예: "2-3주차: 심층 인터뷰 및 사용성 테스트 동시 진행" ✅
5. **조사 방법 섹션:** 선택된 방법론들에 해당하는 조사 방법만 기술하세요.
"""
        else:
            methodology_instruction = """
**⚠️ 방법론 미선택 안내:**
- 사용자가 방법론을 선택하지 않았으므로, 연구 목표에 가장 적합한 단일 방법론만 추천하세요.
- 여러 방법론을 나열하지 말고, 가장 적합한 1개 혹은 함께 수행하기 적절한 2개의 방법론만 선택하여 계획서를 작성하세요.
- 다른 방법론들은 언급하지 마세요.
"""
        
        # Pro 모델로 최종 통합
        final_prompt = f"""
8명의 전문가가 분석한 내용을 하나의 완전한 조사 계획서로 통합하세요.

**중요: 어떠한 서론, 인사말, 확인 메시지 없이 바로 결과물로 시작하세요.**
**절대로 '네,', '알겠습니다', '전문가로서', '~하겠습니다' 같은 응답으로 시작하지 마세요.**

{methodology_instruction}

원본 요청:
{combined_input}

전문가들의 분석:
{expert_outputs}

위 내용을 다음 구조로 **완전한 마크다운 계획서**로 작성하세요:
**중요:**
- 전문가 분석을 최대한 활용하되, 자연스럽게 통합
- 실무진이 바로 실행 가능한 수준으로 구체적으로 작성
- 마크다운 형식 준수
- 숫자 사이에 -를 넣는 경우 마크다운 형식이 잘못 출력되지않도록 주의하세요. (20-30대 의 경우 2030에 줄이 그어져 나오는 오류)
- 표( | )는 절대 사용 금지.

**출력형식**
# [프로젝트 명] 리서치 계획서

## 1. 배경 및 목적
## 2. 연구 질문 및 가설
## 3. 리서치 방법론
## 4. 대상 및 모집 기준
## 5. 일정
## 6. 데이터 수집 및 분석 방법
## 7. 예상 결과 및 활용 방안

"""
        
        log_expert_analysis("최종통합", "Pro 모델로 취합")
        final_result = gemini_service.generate_response(
            final_prompt, 
            {"temperature": 0.3},
            model_name="gemini-2.5-pro"
        )
        
        if final_result['success']:
            log_analysis_complete()
            return {
                'success': True,
                'final_plan': final_result['content'],
                'expert_count': len(successful_experts),
                'generation_type': 'parallel_experts'
            }
        else:
            raise Exception("최종 통합 실패")
            
    except Exception as e:
        log_error(e, "원샷 전문가 병렬 처리")
        return {'success': False, 'error': str(e)}

def extract_contextual_keywords_from_input(text):
    """사용자 입력에서 맥락적으로 중요한 키워드들을 모두 추출"""
    try:
        print(f"[DEBUG] 키워드 추출 시작 - 입력 길이: {len(text)}")
        prompt = KeywordExtractionPrompts.extract_contextual_keywords_prompt(text)
        
        response = openai_service.generate_response(prompt, {"temperature": 0.1})
        
        if response['success']:
            keywords = [kw.strip() for kw in response['content'].split(',') if kw.strip()]
            refined = _refine_extracted_keywords(keywords)
            print(f"[DEBUG] LLM 키워드 추출 성공: {refined}")
            return refined
        else:
            print(f"[DEBUG] LLM 실패 - 폴백 사용: {response}")
            # LLM 실패시 폴백: 기존 방식 사용
            fallback = [word for word in text.split() if len(word) > 2][:10]
            return _refine_extracted_keywords(fallback)
            
    except Exception as e:
        print(f"[DEBUG] 키워드 추출 오류: {e}")
        # 폴백: 기존 방식
        fallback = [word for word in text.split() if len(word) > 2][:10]
        return _refine_extracted_keywords(fallback)

def create_concise_summary_for_rag(conversation_or_text, previous_summaries=None, step_name=""):
    """RAG 검색용 간결한 요약 생성 - LLM 기반 키워드 추출 사용"""
    # 이전 단계 요약에서 핵심 키워드 추출
    if previous_summaries:
        previous_texts = []
        for step, summary in previous_summaries.items():
            previous_texts.append(f"{step}: {summary}")
        
        previous_text = " ".join(previous_texts)
        previous_keywords = extract_contextual_keywords_from_input(previous_text)
        previous_context = f"이전 단계: {', '.join(previous_keywords)}"
    else:
        previous_context = ""
    
    # 현재 대화 또는 텍스트에서 핵심 키워드 추출
    if isinstance(conversation_or_text, str):
        # 텍스트가 직접 전달된 경우
        current_text = conversation_or_text
    else:
        # 대화 객체 배열이 전달된 경우
        current_texts = []
        for msg in conversation_or_text:
            if msg['type'] == 'user':
                current_texts.append(msg['content'])
        current_text = " ".join(current_texts)
    
    if current_text:
        current_keywords = extract_contextual_keywords_from_input(current_text)
        current_context = f"현재 {step_name}: {', '.join(current_keywords)}"
    else:
        current_context = ""
    
    # 간결한 요약 생성
    if previous_context and current_context:
        concise_summary = f"{previous_context} | {current_context}"
    elif current_context:
        concise_summary = current_context
    else:
        concise_summary = step_name
    
    return concise_summary

# # =====================================================================
# # == 디버깅 및 모니터링 API
# # =====================================================================

@app.route('/api/debug/stats', methods=['GET'])
@tier_required(['free'])
def debug_get_stats():
    """요청 통계 및 에러 분석"""
    try:
        stats = get_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/analyze-errors', methods=['GET'])
@tier_required(['free'])
def debug_analyze_errors():
    """에러 패턴 분석"""
    try:
        analyze_error_patterns()
        return jsonify({'success': True, 'message': '에러 분석 완료 (콘솔 확인)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/health', methods=['GET'])
@tier_required(['free'])
def debug_health_check():
    """서버 상태 확인"""
    try:
        health_info = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'vector_service': vector_service is not None,
            'gemini_service': gemini_service is not None,
            'active_requests': len(request_tracker.active_requests),
            'completed_requests': len(request_tracker.completed_requests)
        }
        return jsonify({'success': True, 'health': health_info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

"""
LEGACY (removed): Plan diagnoser endpoints.

The old `/api/diagnoser/*` endpoints were intentionally removed alongside the frontend legacy pages cleanup.
If this feature is reintroduced, implement it as a dedicated blueprint module instead of leaving large commented blocks in `app.py`.
"""



# =====================================================================
# == App 5: 설문 진단 API들 (신규 추가)
# =====================================================================

def get_survey_principles():
    """
    [개선] 설문 진단 전문가를 위해 개선된 RAG로 설문 원칙을 가져옵니다.
    """
    if vector_service:
        # 하이브리드 검색으로 설문 원칙만 가져오기 (예시는 불필요)
        rag_results = vector_service.improved_service.hybrid_search(
            query_text="설문조사 설계의 모든 원칙",
            principles_n=20,  # 진단용이므로 원칙을 더 많이
            examples_n=0,     # 진단이므로 예시는 불필요
            topics=["설문", "설계"]
        )
        
        # RAG 검색 로그 추가
        log_step_search("설문진단원칙", "설문조사 설계의 모든 원칙", rag_results, "설문 진단용 원칙 수집")
        log_rag_quality_check("설문진단원칙", "설문조사 설계의 모든 원칙", rag_results)
        
        # 컨텍스트 최적화
        principles_context = vector_service.improved_service.context_optimization(
            rag_results["principles"],
            max_length=2000
        )
        
        return principles_context
    else:
        # DB 연결 실패 시 비상용 텍스트
        return "참고할 설문 원칙을 DB에서 로드하는 데 실패했습니다."

@app.route('/api/survey-diagnoser/diagnose', methods=['POST'])
@tier_required(['free'])
def survey_diagnoser_diagnose():
    try:
        data = request.json
        survey_text = data.get('survey_text', '')
        
        # 새로운 깔끔한 로그 - 사용자 요청
        log_user_request("설문 진단하기", survey_text)
        
        # [수정] get_survey_principles()가 이제 RAG DB에서 원칙을 가져옵니다.
        principles = get_survey_principles() 

        expert_prompt_functions = [
            SurveyDiagnosisPrompts.prompt_diagnose_clarity,
            SurveyDiagnosisPrompts.prompt_diagnose_terminology,
            SurveyDiagnosisPrompts.prompt_diagnose_leading_questions,
            SurveyDiagnosisPrompts.prompt_diagnose_options_mec,
            SurveyDiagnosisPrompts.prompt_diagnose_flow
        ]
        
        # 새로운 깔끔한 로그 - 키워드 추출
        keywords = extract_contextual_keywords_from_input(survey_text)
        log_keyword_extraction(keywords)
        
        # 새로운 깔끔한 로그 - RAG 검색 결과
        log_step_search_clean("설문진단", f"설문 진단 {keywords}", {"principles": principles}, "설문 품질 진단")
        
        # 병렬 처리로 5개 전문가 동시 호출
        import concurrent.futures
        
        expert_names = [
            "명확성/간결성", "용어 사용", "유도 질문", "보기의 상호배타성/포괄성", "논리적 순서/스크리너 배치"
        ]
        
        # 새로운 깔끔한 로그 - 진단 전문가 호출 시작
        for expert_name in expert_names:
            log_expert_analysis(expert_name, "진단중")
        
        def call_survey_expert_diagnosis(i, expert_name):
            """개별 설문 진단 전문가 함수"""
            try:
                prompt = expert_prompt_functions[i](survey_text, principles)
                raw_result = openai_service.generate_response(prompt, {"temperature": 0.1})
                parsed_json_object = parse_llm_json_response(raw_result)
                return parsed_json_object
            except Exception as e:
                return {
                    "check_item_key": expert_prompt_functions[i].__name__.replace('prompt_diagnose_', ''),
                    "pass": False,
                    "reason": f"진단 중 오류 발생: {str(e)}",
                    "quote": ""
                }
        
        # ThreadPoolExecutor를 사용한 병렬 처리
        diagnosis_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # 모든 전문가를 병렬로 호출
            future_to_expert = {
                executor.submit(call_survey_expert_diagnosis, i, expert_names[i]): i
                for i in range(len(expert_prompt_functions))
            }
            
            # 결과 수집 (순서대로 정렬)
            expert_results = [None] * len(expert_prompt_functions)
            for future in concurrent.futures.as_completed(future_to_expert):
                expert_index = future_to_expert[future]
                try:
                    result = future.result()
                    expert_results[expert_index] = result
                except Exception as exc:
                    expert_results[expert_index] = {
                        "check_item_key": expert_prompt_functions[expert_index].__name__.replace('prompt_diagnose_', ''),
                        "pass": False,
                        "reason": f"병렬 처리 중 오류 발생: {str(exc)}",
                        "quote": ""
                    }
            
            # 순서대로 결과 추가
            diagnosis_results = expert_results
        
        # 새로운 깔끔한 로그 - 진단 완료
        log_analysis_complete()
            
        return jsonify({'success': True, 'response': diagnosis_results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/survey-diagnoser/generate-draft', methods=['POST'])
@tier_required(['free'])
def survey_diagnoser_generate_draft():
    try:
        data = request.json
        survey_text = data.get('survey_text', '')
        item_to_fix = data.get('item_to_fix', '')
        
        # [수정] '진단'과 동일하게 RAG DB에서 원칙을 가져옵니다.
        principles = get_survey_principles()
        
        prompt = SurveyGenerationPrompts.prompt_generate_survey_draft(survey_text, item_to_fix, principles)
        raw_result = openai_service.generate_response(prompt, {"temperature": 0.5})
        parsed_json = parse_llm_json_response(raw_result)
        response_data = { "draft": parsed_json.get("draft_suggestions", []) }

        return jsonify({'success': True, 'response': response_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/survey-diagnoser/polish-plan', methods=['POST'])
@tier_required(['free'])
def survey_diagnoser_polish_plan():
    try:
        data = request.json
        survey_text = data.get('survey_text', '')
        confirmed_survey = data.get('confirmed_survey', {})
        survey_type = data.get('survey_type', 'screener')

        confirmed_fixes_json = json.dumps(confirmed_survey, ensure_ascii=False, indent=2)

        prompt = SurveyGenerationPrompts.prompt_polish_survey(survey_text, confirmed_fixes_json)
        raw_result = openai_service.generate_response(prompt, {"temperature": 0.3})
        
        # --- [수정] Markdown 텍스트 대신 JSON을 파싱하여 반환 ---
        parsed_json = parse_llm_json_response(raw_result)
        return jsonify({'success': True, 'response': parsed_json}) # (수정)
        # -----------------------------------------------------
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =====================================================================
# == App 6: 가이드라인 생성 API (신규 추가)
# =====================================================================

@app.route('/api/guideline/extract-methods', methods=['POST'])
@tier_required(['free'])
def guideline_extract_methods():
    try:
        data = request.json
        research_plan = data.get('research_plan', '')
        prompt = GuidelineGeneratorPrompts.prompt_extract_methodologies(research_plan)
        
        # 정확한 JSON 추출을 위해 temperature 0.0 설정
        raw_result = openai_service.generate_response(prompt, {"temperature": 0.0})
        parsed_json = parse_llm_json_response(raw_result) # { "methodologies": [...] }
        
        return jsonify({'success': True, 'methodologies': parsed_json.get('methodologies', [])})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/extract-methodologies', methods=['POST'])
@tier_required(['free'])
def extract_methodologies():
    """계획서에서 방법론 추출"""
    try:
        data = request.json
        research_plan = data.get('research_plan', '')
        
        if not research_plan:
            return jsonify({'success': False, 'error': '계획서가 비어있습니다'}), 400
        
        # 방법론 추출 프롬프트
        prompt = GuidelineGeneratorPrompts.prompt_extract_methodologies(research_plan)
        
        result = openai_service.generate_response(prompt, {"temperature": 0.2})
        
        if result['success']:
            # JSON 파싱
            parsed = parse_llm_json_response(result)
            methodologies = parsed.get('methodologies', [])
            
            return jsonify({'success': True, 'methodologies': methodologies})
        else:
            return jsonify({'success': False, 'error': 'LLM 응답 실패'}), 500
            
    except Exception as e:
        print(f"[ERROR] 방법론 추출 실패: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

###스크리너설문생성기###
@app.route('/api/survey/create-and-generate', methods=['POST'])
@tier_required(['free'])
def survey_create_and_generate():
    """스크리너(설문) artifact 생성 + 백그라운드 생성"""
    try:
        if not SQLA_ENABLED or not session_scope:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        study_id = data.get('study_id')
        research_plan = data.get('research_plan', '')

        try:
            study_id_int = int(study_id)
        except Exception:
            return jsonify({'success': False, 'error': '유효하지 않은 study_id입니다.'}), 400

        # 1. pending artifact 먼저 생성
        with session_scope() as db_session:
            study_obj = db_session.execute(
                select(Study).where(Study.id == study_id_int).limit(1)
            ).scalar_one_or_none()
            if not study_obj:
                return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다'}), 404

            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == study_obj.project_id).limit(1)
            ).scalar_one_or_none()
            if owner_id is None:
                return jsonify({'success': False, 'error': '프로젝트 정보를 찾을 수 없습니다'}), 404

            artifact_obj = Artifact(
                study_id=study_id_int,
                artifact_type='survey',
                content='',
                status='pending',
                owner_id=int(owner_id),
            )
            db_session.add(artifact_obj)
            db_session.flush()
            db_session.refresh(artifact_obj)
            artifact_id = artifact_obj.id
            project_id_for_keywords = study_obj.project_id

        project_keywords = fetch_project_keywords(project_id_for_keywords)

        if artifact_id is None:
            return jsonify({'success': False, 'error': '스크리너 저장소 생성에 실패했습니다.'}), 500
        
        # 2. 백그라운드에서 변수 분석 artifact도 함께 생성
        def generate_in_background():
            try:
                # Step 1: 변수 추출
                print(f"[Survey Gen] Step 1: 변수 추출 시작")
                
                variables_prompt = ScreenerPrompts.prompt_analyze_plan(research_plan)
                variables_result = openai_service.generate_response(variables_prompt, {"temperature": 0.3})
                
                if not variables_result['success']:
                    raise Exception('변수 추출 실패')
                
                variables_data = parse_llm_json_response(variables_result)
                key_variables = variables_data.get('key_variables', [])
                balance_variables = variables_data.get('balance_variables', [])
                target_groups = variables_data.get('target_groups', [])

                # Step 1.5: '필수 조건'을 객관적 행동 지표(screening_criteria)로 정규화
                print(f"[Survey Gen] Step 1.5: 스크리닝 기준(행동 지표) 정규화")
                screening_criteria = []
                try:
                    criteria_prompt = ScreenerPrompts.prompt_normalize_screening_criteria(
                        research_plan=research_plan,
                        key_variables_json=json.dumps(key_variables, ensure_ascii=False, indent=2),
                    )
                    criteria_result = openai_service.generate_response(criteria_prompt, {"temperature": 0.2})
                    if criteria_result.get('success'):
                        criteria_data = parse_llm_json_response(criteria_result)
                        screening_criteria = criteria_data.get('screening_criteria', []) or []
                except Exception as e:
                    # 정규화 실패해도 설문 생성은 계속 진행 (fallback: 빈 배열)
                    print(f"[Survey Gen] Step 1.5 경고 - 정규화 실패(계속 진행): {e}")
                
                # Step 1 완료: 변수 분석 결과 먼저 표시
                survey_data = {
                    'key_variables': key_variables,
                    'balance_variables': balance_variables,
                    'target_groups': target_groups,
                    'screening_criteria': screening_criteria,
                    'form_elements': []  # 새로운 형식 사용
                }
                
                partial_content = f"<!-- SURVEY_DATA\n{json.dumps(survey_data, ensure_ascii=False, indent=2)}\n-->\n\n"
                partial_content += "# 스크리너 설문\n\n"
                partial_content += "## 📝 설문 문항\n\n"
                partial_content += "_문항을 생성하고 있습니다..._\n\n"

                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if target:
                        target.content = partial_content
                print(f"[Survey Gen] Step 1 완료 - 변수 분석 결과 표시")
                
                # Step 2: 문항 구조 생성
                print(f"[Survey Gen] Step 2: 문항 구조 생성")
                
                # RAG 검색
                rag_query = f"""
                조사 계획서: {research_plan}
                핵심 변수: {json.dumps(key_variables, ensure_ascii=False)}
                ---
                위 계획서와 변수에 가장 적합한 설문 문항 구조 및 예시
                """
                
                keywords = extract_contextual_keywords_from_input(research_plan)
                expanded_query = vector_service.improved_service.query_expansion(f"설문조사 설계 {research_plan}")
                
                rag_results = vector_service.improved_service.hybrid_search(
                    query_text=expanded_query,
                    principles_n=4,
                    examples_n=3,
                    topics=["설문", "스크리너"],
                    domain_keywords=project_keywords
                )
                
                rules_context_str = vector_service.improved_service.context_optimization(rag_results["principles"], max_length=1200)
                examples_context_str = vector_service.improved_service.context_optimization(rag_results["examples"], max_length=800)
                
                structure_prompt = SurveyBuilderPrompts.prompt_generate_survey_structure(
                    research_plan_content=research_plan,
                    key_variables_json=json.dumps(key_variables, ensure_ascii=False, indent=2),
                    balance_variables_json=json.dumps(balance_variables, ensure_ascii=False, indent=2),
                    screening_criteria_json=json.dumps(screening_criteria, ensure_ascii=False, indent=2),
                    rules_context_str=rules_context_str,
                    examples_context_str=examples_context_str
                )
                
                structure_result = gemini_service.generate_response(structure_prompt,{"temperature": 0.2}, model_name="gemini-2.5-pro")
                
                if not structure_result['success']:
                    raise Exception('문항 구조 생성 실패')
                
                structure_data = parse_llm_json_response(structure_result)
                blocks = structure_data.get('blocks', []) or []
                # 새로운 형식(form_elements) 또는 기존 형식(questions) 지원
                form_elements = structure_data.get('form_elements', [])
                questions_list = structure_data.get('questions', [])
                
                # form_elements가 있으면 우선 사용, 없으면 questions 사용
                if form_elements:
                    questions_list = form_elements
                    print(f"[Survey Gen] 새로운 형식(form_elements) 사용: {len(form_elements)}개 문항")
                else:
                    print(f"[Survey Gen] 기존 형식(questions) 사용: {len(questions_list)}개 문항")

                # blocks가 없으면 최소 블록 세트 생성 (하위호환/안전장치)
                if not isinstance(blocks, list) or len(blocks) == 0:
                    blocks = [
                        {"id": "intro", "title": "Block 0: 안내/동의", "kind": "intro", "ai_comment": "조사 안내와 동의 확인입니다."},
                        {"id": "A_qualification", "title": "Block A: 필수 자격 (Qualification)", "kind": "qualification", "ai_comment": "여기서 통과 못 하면 바로 종료됩니다."},
                        {"id": "B_demographics", "title": "Block B: 배경 정보 (Demographics)", "kind": "demographics", "ai_comment": "나중에 쿼터(성비/연령비) 맞출 때 쓰이는 변수들입니다."},
                        {"id": "C_open_ended", "title": "Block C: 심층 질문 (Open-ended)", "kind": "open_ended", "ai_comment": "인터뷰 대상자로 적합한지 판단할 때 읽어볼 내용입니다."},
                        {"id": "D_ops", "title": "Block D: 운영 정보 (Ops)", "kind": "ops", "ai_comment": "일정/연락 등 운영에 필요한 정보입니다."},
                    ]

                # 각 문항에 block_id가 없으면 기본 배정 (안전장치)
                default_block_id = "D_ops"
                try:
                    default_block_id = (blocks[-1] or {}).get("id") or default_block_id
                except Exception:
                    pass
                for q in questions_list:
                    if isinstance(q, dict) and not q.get('block_id'):
                        q['block_id'] = default_block_id
                
                # Step 3: 선택지(보기) 생성
                print(f"[Survey Gen] Step 3: 선택지 생성 시작")
                
                # 객관식 문항만 필터링 (기존 형식: type 필드, 새로운 형식: element 필드)
                all_select_questions = []
                for q in questions_list:
                    # 기존 형식: type 필드 사용
                    if q.get('type') and ('선택' in q.get('type') or '객관식' in q.get('type')):
                        all_select_questions.append(q)
                    # 새로운 형식: element 필드 사용
                    elif q.get('element') in ['RadioButtons', 'Checkboxes']:
                        all_select_questions.append(q)
                
                if all_select_questions:
                    # RAG 검색
                    rag_query = f"""
                    다음 질문 목록에 대한 '선택지(보기)' 예시를 찾아줘:
                    {json.dumps(all_select_questions, ensure_ascii=False, indent=2)}
                    ---
                    특히 '연령', '성별', '경험 유무', '사용 빈도' 등을 묻는 질문의 모범 답안 예시가 필요해.
                    """
                    
                    # Vector DB 검색 (예시 데이터만 검색)
                    relevant_context = vector_service.search(
                        query_text=rag_query, 
                        n_results=10,
                        filter_metadata={
                            "data_type": "예시"
                        },
                        domain_keywords=project_keywords
                    )
                    
                    # 선택지 생성 프롬프트
                    options_prompt = SurveyBuilderPrompts.prompt_generate_all_answer_options(
                        questions_json_chunk=json.dumps(all_select_questions, ensure_ascii=False, indent=2),
                        relevant_examples_str=relevant_context
                    )
                    
                    options_result = openai_service.generate_response(options_prompt, {"temperature": 0.3})
                    
                    if options_result['success']:
                        options_data = parse_llm_json_response(options_result)
                        options_object = options_data.get('options', {})
                        
                        # questions_list에 options 추가
                        for q in questions_list:
                            q_id = q.get('id')
                            if q_id and q_id in options_object:
                                options_data = options_object[q_id]
                                
                                # 새로운 형식: 이미 [{value, text}] 배열
                                if isinstance(options_data, list) and len(options_data) > 0 and isinstance(options_data[0], dict):
                                    q['options'] = options_data
                                # 기존 형식: 문자열 (줄바꿈으로 구분)
                                elif isinstance(options_data, str):
                                    q['options'] = [opt.strip().lstrip('-').strip() for opt in options_data.split('\n') if opt.strip()]
                                # 기존 형식: 배열 (문자열 배열)
                                elif isinstance(options_data, list):
                                    q['options'] = options_data
                        
                        print(f"[Survey Gen] Step 3 완료 - {len(all_select_questions)}개 문항의 선택지 생성됨")
                    else:
                        print(f"[Survey Gen] Step 3 경고 - 선택지 생성 실패, 선택지 없이 진행")
                
                # 최종 결과: JSON + Markdown 결합
                print(f"[Survey Gen] Step 4: 최종 변환")
                
                # JSON 데이터를 특수 마커로 감싸서 프론트엔드에서 파싱 가능하게
                # 새로운 형식(form_elements)인지 확인
                is_new_format = len(questions_list) > 0 and 'element' in questions_list[0]
                
                survey_data = {
                    'key_variables': key_variables,
                    'balance_variables': balance_variables,
                    'target_groups': target_groups,
                    'screening_criteria': screening_criteria,
                    'blocks': blocks,
                }
                
                # 새로운 형식이면 form_elements로, 기존 형식이면 questions로 저장
                if is_new_format:
                    survey_data['form_elements'] = questions_list
                    print(f"[Survey Gen] 새로운 형식(form_elements)으로 저장")
                else:
                    survey_data['questions'] = questions_list
                    print(f"[Survey Gen] 기존 형식(questions)으로 저장")
                
                content = f"<!-- SURVEY_DATA\n{json.dumps(survey_data, ensure_ascii=False, indent=2)}\n-->\n\n"
                content += "# 스크리너 설문\n\n"
                
                # 문항 목록 (섹션별로 그룹화하여 표시)
                content += "## 📝 설문 문항\n\n"

                # 블록별로 질문 그룹화 (block_id 우선, 없으면 section_id fallback)
                blocks_by_id = {b.get('id'): b for b in blocks if isinstance(b, dict) and b.get('id')}
                questions_by_block = {}
                for q in questions_list:
                    if not isinstance(q, dict):
                        continue
                    block_id = q.get('block_id') or q.get('section_id') or "D_ops"
                    if block_id not in questions_by_block:
                        questions_by_block[block_id] = []
                    questions_by_block[block_id].append(q)
                
                question_num = 1
                # blocks의 순서를 화면/문서 순서로 사용
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    block_id = b.get('id')
                    if not block_id or block_id not in questions_by_block:
                        continue
                    section_title = b.get('title') or block_id
                    ai_comment = (b.get('ai_comment') or '').strip()
                    content += f"### {section_title}\n\n"
                    if ai_comment:
                        content += f"> AI 코멘트: {ai_comment}\n\n"

                    for q in questions_by_block[block_id]:
                            content += f"#### {question_num}. {q.get('text')}\n"
                            # 기존 형식: type 필드, 새로운 형식: element 필드
                            question_type = q.get('type') or q.get('element', '')
                            content += f"**유형**: {question_type}\n"
                            # 선택지가 있으면 표시
                            if q.get('options') and len(q.get('options')) > 0:
                                content += "**선택지**:\n"
                                for opt in q.get('options'):
                                    # 새로운 형식: {value, text} 객체, 기존 형식: 문자열
                                    if isinstance(opt, dict):
                                        content += f"- {opt.get('text', opt.get('value', ''))}\n"
                                    else:
                                        content += f"- {opt}\n"
                            content += "\n"
                            question_num += 1
                
                # (Legacy) section_id 기반 추가 처리 블록 제거:
                # 현재는 blocks + block_id로만 그룹화하며, block_id 없는 문항은 위에서 기본 블록으로 배정함.
                
                # artifact 업데이트
                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if target:
                        target.content = content
                        target.status = 'completed'
                
                print(f"[Survey Gen] 완료!")
                
            except Exception as e:
                print(f"[ERROR] Survey 생성 실패: {e}")
                traceback.print_exc()
                
                # 오류 발생 시 pending artifact 삭제 (오류 로그와 함께)
                try:
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            bg_session.delete(target)
                    print(f"🗑️ 생성 실패로 인해 pending artifact 삭제: artifact_id={artifact_id}, 오류: {str(e)}")
                except Exception as delete_error:
                    print(f"[ERROR] 생성 실패 후 artifact 삭제 실패: {delete_error}")
                    # 삭제 실패 시 기존 방식으로 fallback (하위 호환성)
                    try:
                        with session_scope() as bg_session:
                            target = bg_session.execute(
                                select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                            ).scalar_one_or_none()
                            if target:
                                target.status = 'failed'
                                target.content = f'❌ 생성 실패: {str(e)}'
                    except:
                        pass
        
        import threading
        thread = threading.Thread(target=generate_in_background)
        thread.start()
        
        return jsonify({'success': True, 'artifact_id': artifact_id})
        
    except Exception as e:
        print(f"[ERROR] Survey artifact 생성 실패: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500



###가이드라인 생성기###
@app.route('/api/guideline/create-and-generate', methods=['POST'])
@tier_required(['free'])
def guideline_create_and_generate():
    """가이드라인 artifact 생성 + 백그라운드 생성"""
    try:
        if not SQLA_ENABLED or not session_scope:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        study_id = data.get('study_id')
        research_plan = data.get('research_plan', '')
        methodologies = data.get('methodologies', [])

        try:
            study_id_int = int(study_id)
        except Exception:
            return jsonify({'success': False, 'error': '유효하지 않은 study_id입니다.'}), 400

        # 1. pending artifact 먼저 생성
        with session_scope() as db_session:
            study_obj = db_session.execute(
                select(Study).where(Study.id == study_id_int).limit(1)
            ).scalar_one_or_none()
            if not study_obj:
                return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다'}), 404

            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == study_obj.project_id).limit(1)
            ).scalar_one_or_none()
            if owner_id is None:
                return jsonify({'success': False, 'error': '프로젝트 정보를 찾을 수 없습니다'}), 404

            artifact_obj = Artifact(
                study_id=study_id_int,
                artifact_type='guideline',
                content='',
                status='pending',
                owner_id=int(owner_id),
            )
            db_session.add(artifact_obj)
            db_session.flush()
            db_session.refresh(artifact_obj)
            artifact_id = artifact_obj.id
            project_id_for_keywords = study_obj.project_id

        project_keywords = fetch_project_keywords(project_id_for_keywords)
        
        # 2. 백그라운드에서 생성
        def generate_in_background():
            try:
                # 전역 변수들 체크 및 명시적 참조
                print(f"[Guideline Gen] 백그라운드 생성 시작: artifact_id={artifact_id}")
                
                # vector_service None 체크
                if vector_service is None:
                    raise Exception('Vector DB 서비스가 초기화되지 않았습니다.')
                
                # openai_service None 체크 추가
                if openai_service is None or openai_service.client is None:
                    raise Exception('OpenAI 서비스가 초기화되지 않았습니다.')
                
                print(f"[Guideline Gen] 서비스 체크 완료")
                
                # 가이드라인 생성 로직 (기존 로직 활용)
                options = {'methodology': ', '.join(methodologies)}
                options_json = json.dumps(options, ensure_ascii=False, indent=2)
                
                # RAG 검색
                methodology = ', '.join(methodologies)
                rag_query = f"""
                계획: {research_plan}
                방법론: {methodology}
                ---
                위 계획과 방법론에 적합한 가이드라인 예시 (웜업, 핵심 질문 등)
                """
                
                print(f"[Guideline Gen] 키워드 추출 시작")
                keywords = extract_contextual_keywords_from_input(research_plan)
                print(f"[Guideline Gen] 키워드 추출 완료: {keywords}")
                
                methodology_filter = "usability_test" if "UT" in methodology or "사용성" in methodology else "interview"
                
                print(f"[Guideline Gen] RAG 검색 시작")
                rag_results = vector_service.improved_service.hybrid_search(
                    query_text=rag_query,
                    principles_n=5,
                    examples_n=3,
                    topics=["가이드라인", methodology_filter],
                    domain_keywords=project_keywords
                )
                print(f"[Guideline Gen] RAG 검색 완료")
                
                rules_context_str = rag_results['principles']
                examples_context_str = rag_results['examples']
                
                # 프롬프트 생성 및 LLM 호출
                print(f"[Guideline Gen] 프롬프트 생성 시작")
                prompt = GuidelineGeneratorPrompts.prompt_generate_guideline(
                    research_plan, options_json, rules_context_str, examples_context_str
                )
                
                print(f"[Guideline Gen] LLM 호출 시작")
                result = openai_service.generate_response(prompt, {"max_output_tokens": 8192},
            model_name="gpt-5")
                
                if result['success']:
                    content = result['content']
                    print(f"[Guideline Gen] LLM 호출 완료, content 길이: {len(content)}")
                    
                    # artifact 업데이트
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            target.content = content
                            target.status = 'completed'
                    
                    print(f"[Guideline Gen] 완료: artifact_id={artifact_id}")
                else:
                    error_msg = result.get('error', '알 수 없는 오류')
                    print(f"[Guideline Gen] LLM 생성 실패: {error_msg}")
                    raise Exception(f'LLM 생성 실패: {error_msg}')
                
            except Exception as e:
                print(f"[ERROR] Guideline 생성 실패: {e}")
                traceback.print_exc()
                
                # 오류 발생 시 pending artifact 삭제 (오류 로그와 함께)
                try:
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            bg_session.delete(target)
                    print(f"🗑️ 생성 실패로 인해 pending artifact 삭제: artifact_id={artifact_id}, 오류: {str(e)}")
                except Exception as delete_error:
                    print(f"[ERROR] 생성 실패 후 artifact 삭제 실패: {delete_error}")
                    # 삭제 실패 시 기존 방식으로 fallback (하위 호환성)
                    try:
                        with session_scope() as bg_session:
                            target = bg_session.execute(
                                select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                            ).scalar_one_or_none()
                            if target:
                                target.status = 'failed'
                                target.content = f'❌ 생성 실패: {str(e)}'
                    except:
                        pass
        
        import threading
        thread = threading.Thread(target=generate_in_background)
        thread.start()
        
        return jsonify({'success': True, 'artifact_id': artifact_id})
        
    except Exception as e:
        print(f"[ERROR] Guideline artifact 생성 실패: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# =====================================================================
# == App 7: 워크스페이스 (Workspace) - 프로젝트/스터디 관리
# =====================================================================

@app.route('/api/workspace/projects', methods=['GET'])
@tier_required(['free'])
def workspace_get_projects():
    """
    [GET] 현재 사용자의 모든 프로젝트 조회
    - Supabase 'projects' 테이블에서 owner_id로 필터링
    - 최신순 정렬 (created_at DESC)
    """
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)
        with session_scope() as db_session:
            projects = WorkspaceRepository.get_projects_by_owner_ids(db_session, owner_ids)
        return jsonify({'success': True, 'projects': projects})
    except Exception as e:
        log_error(e, "프로젝트 목록 조회")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workspace/projects-with-studies', methods=['GET'])
@tier_required(['free'])
def workspace_get_projects_with_studies():
    """
    [GET] 현재 사용자의 모든 프로젝트와 각 프로젝트의 스터디를 한 번에 조회
    - 프로젝트와 스터디를 통합하여 반환 (N+1 쿼리 문제 해결)
    - 권한 체크를 한 번만 수행
    """
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)

        with session_scope() as db_session:
            projects = WorkspaceRepository.get_projects_by_owner_ids(db_session, owner_ids)
            project_ids = [p['id'] for p in projects]
            studies = WorkspaceRepository.get_studies_by_project_ids(db_session, project_ids)
            study_ids = [s['id'] for s in studies]
            artifacts = WorkspaceRepository.get_artifacts_by_study_ids(db_session, study_ids)

        studies_by_project = WorkspaceRepository.group_studies_by_project(studies)
        artifacts_by_study = WorkspaceRepository.group_artifacts_by_study(artifacts)

        projects_with_studies = []
        all_studies = []
        for project in projects:
            project_with_studies = project.copy()
            project_studies = studies_by_project.get(project['id'], [])
            for study in project_studies:
                study['artifacts'] = artifacts_by_study.get(study['id'], [])
                all_studies.append(study.copy())
            project_with_studies['studies'] = project_studies
            projects_with_studies.append(project_with_studies)

        all_artifacts = []
        for study in all_studies:
            for artifact in study.get('artifacts', []):
                artifact_with_study = artifact.copy()
                artifact_with_study['study_name'] = study.get('name', '')
                artifact_with_study['study_slug'] = study.get('slug', study.get('id'))
                all_artifacts.append(artifact_with_study)

        recent_artifacts = sorted(
            all_artifacts,
            key=lambda x: x.get('created_at', ''),
            reverse=True
        )[:3]

        return jsonify({
            'success': True,
            'projects': projects_with_studies,
            'all_studies': all_studies,
            'recent_artifacts': recent_artifacts
        })
    except Exception as e:
        log_error(e, "프로젝트+스터디 목록 조회")
        return jsonify({'success': False, 'error': str(e)}), 500


"""
NOTE: `/api/b2b/*` endpoints were moved to `routes/b2b.py` (blueprint).
"""
@app.route('/api/workspace/projects', methods=['POST'])
@tier_required(['free'])
def workspace_create_project():
    """
    [POST] 새 프로젝트 생성
    - Supabase 'projects' 테이블에 저장
    - 필수: name
    - 선택: product_url, keywords (배열)
    - description은 사용 안 함 (UI에서 제거됨)
    """
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        data = request.json
        name = data.get('name')
        tags = data.get('tags', [])
        product_url = data.get('productUrl', '')

        keywords_array = []
        try:
            if isinstance(tags, list) and len(tags) > 0:
                keywords_array = tags
            elif isinstance(tags, str):
                keywords_array = [tags]
        except Exception:
            keywords_array = []

        if not name:
            return jsonify({'success': False, 'error': '프로젝트 이름은 필수입니다.'}), 400

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        with session_scope() as db_session:
            created_project = WorkspaceRepository.create_project(
                db_session,
                owner_id=int(user_id_int),
                name=name,
                product_url=product_url,
                keywords=keywords_array,
            )
        return jsonify({'success': True, 'project': created_project})
    except Exception as e:
        log_error(e, "프로젝트 생성")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workspace/projects/<int:project_id>', methods=['DELETE'])
@tier_required(['free'])
def workspace_delete_project(project_id):
    """
    [DELETE] 프로젝트 삭제
    - Supabase에서 해당 프로젝트 삭제
    - 관련된 studies도 CASCADE로 자동 삭제 (DB 설정 필요)
    """
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        with session_scope() as db_session:
            WorkspaceRepository.delete_project_for_owner(db_session, project_id, int(user_id_int))
        return jsonify({'success': True, 'message': f'프로젝트 {project_id} 삭제 완료'})
    except Exception as e:
        log_error(e, f"프로젝트 {project_id} 삭제")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workspace/projects/<int:project_id>', methods=['PUT'])
@tier_required(['free'])
def workspace_update_project(project_id):
    """
    [PUT] 프로젝트 정보 수정
    - Supabase에서 프로젝트 정보 업데이트
    """
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결이 필요합니다.'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        data = request.json
        update_data = {}
        if 'name' in data:
            update_data['name'] = data['name']
        if 'productUrl' in data:
            update_data['product_url'] = data['productUrl']
        if 'tags' in data:
            tags = data['tags']
            if isinstance(tags, list):
                update_data['keywords'] = tags
            elif isinstance(tags, str):
                update_data['keywords'] = [tags]
            else:
                update_data['keywords'] = []

        if not update_data:
            return jsonify({'success': False, 'error': '업데이트할 데이터가 없습니다.'}), 400

        with session_scope() as db_session:
            updated_project = WorkspaceRepository.update_project_for_owner(
                db_session, project_id, int(user_id_int), update_data
            )
        if not updated_project:
            return jsonify({'success': False, 'error': '프로젝트를 찾을 수 없습니다.'}), 404
        return jsonify({'success': True, 'message': '프로젝트 정보가 업데이트되었습니다.', 'data': updated_project})
    except Exception as e:
        log_error(e, f"프로젝트 {project_id} 업데이트")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workspace/generate-project-name', methods=['POST'])
@tier_required(['free'])
def generate_project_name():
    """
    [POST] 프로젝트명 자동 생성
    - studyName과 problemDefinition을 기반으로 AI가 프로젝트명 생성
    - 프로젝트명과 관련 태그를 함께 생성하여 반환
    """
    try:
        data = request.json
        study_name = data.get('studyName', '')
        problem_definition = data.get('problemDefinition', '')
        
        if not study_name and not problem_definition:
            return jsonify({'success': False, 'error': '연구명 또는 문제 정의가 필요합니다.'}), 400
        
        # 프롬프트 생성
        prompt = f"""
다음 연구 제목과 문제 정의에서 핵심 키워드를 추출하여 프로젝트명과 관련 태그를 생성해주세요.

연구 제목: {study_name if study_name else '(없음)'}
문제 정의: {problem_definition if problem_definition else '(없음)'}

응답 형식 (JSON만):
{{
  "projectName": "서비스명 또는 브랜드명",
  "tags": ["태그1", "태그2", "태그3"]
}}

규칙:
- 프로젝트명은 최대한 브랜드/서비스명을 포함하되, 유추할 수 없다면 도메인/비즈니스 수준의 단어로 선택
- 태그는 3-5개 생성 (도메인, 산업, 비즈니스 유형 등)
- 태그는 간결하고 명확하게
- 각 결과물 앞에 띄어쓰기나, - 와 같은 불필요한 부분이 포함되지않도록 처리해주세요.

예시:
{{"projectName": "KB증권 MTS M-able", "tags": ["금융", "증권", "모바일앱", "MTS"]}}

답변:"""

        result = openai_service.generate_response(
            prompt,
            generation_config={'temperature': 0.3, 'max_output_tokens': 200}
        )
        
        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', '프로젝트명 생성 실패')}), 500
        
        # JSON 파싱
        content = result.get('content', '').strip()
        import re
        import json as json_lib
        
        try:
            # JSON 부분만 추출
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json_lib.loads(json_match.group())
                project_name = data.get('projectName', '').strip()
                tags = data.get('tags', [])
                
                # 유효성 검증
                if not project_name:
                    project_name = content.split('\n')[0].strip().replace('"', '').strip()
                    if ':' in project_name:
                        project_name = project_name.split(':')[-1].strip()
                    tags = []
            else:
                # JSON 파싱 실패 시 기본 처리
                project_name = content.strip().replace('"', '').strip()
                if ':' in project_name:
                    project_name = project_name.split(':')[-1].strip()
                tags = []
        except Exception as e:
            print(f"[ERROR] JSON 파싱 실패: {e}, content: {content}")
            # 기본 처리
            project_name = content.strip().replace('"', '').strip()
            if ':' in project_name:
                project_name = project_name.split(':')[-1].strip()
            tags = []
        
        # 숫자나 불필요한 문자 제거
        project_name = re.sub(r'^\d+\.\s*', '', project_name)
        
        return jsonify({'success': True, 'projectName': project_name, 'tags': tags})
        
    except Exception as e:
        log_error(e, "프로젝트명 생성")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workspace/generate-study-name', methods=['POST'])
@tier_required(['free'])
def generate_study_name():
    """
    [POST] 연구명 자동 생성
    - problemDefinition을 기반으로 AI가 연구명 생성
    """
    try:
        data = request.json
        problem_definition = data.get('problemDefinition', '')
        
        if not problem_definition or len(problem_definition.strip()) < 10:
            return jsonify({'success': False, 'error': '문제 정의가 필요합니다.'}), 400
        
        # 프롬프트 생성
        prompt = f"""
다음 문제 정의를 바탕으로 적절한 연구명을 하나만 생성해주세요.

문제 정의:
{problem_definition}

연구명 규칙:
- 명확하고 구체적인 연구명 하나만 작성
- 문제를 잘 나타내야 함
- "연구", "조사", "분석" 같은 단어는 제외
- 10-20자 이내
- 설명 없이 연구명만 출력

답변 형식: 연구명만 출력 (추가 설명, 예시, 목록 없이)"""

        result = openai_service.generate_response(
            prompt,
            generation_config={'temperature': 0.4, 'max_output_tokens': 100}
        )
        
        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', '연구명 생성 실패')}), 500
        
        content = result.get('content', '').strip()
        
        # 여러 줄이 있을 경우 첫 번째 줄만 추출
        lines = content.split('\n')
        study_name = lines[0].strip()
        
        # 불필요한 문자 제거
        study_name = study_name.replace('"', '').replace('*', '').replace('-', '').strip()
        
        # ": " 같은 구분자 제거
        if ':' in study_name:
            study_name = study_name.split(':')[-1].strip()
        
        # 숫자나 불필요한 문자 제거 (예: "1. ", "2. ")
        study_name = re.sub(r'^\d+\.\s*', '', study_name)
        
        return jsonify({'success': True, 'studyName': study_name})
        
    except Exception as e:
        log_error(e, "연구명 생성")
        return jsonify({'success': False, 'error': str(e)}), 500

# --- [태그 생성] LLM으로 프로젝트 제목 기반 태그 자동 생성 ---

def _build_url_analysis_context(product_url: str) -> Optional[str]:
    if not product_url:
        return None
    normalized_url = product_url.strip()
    if not normalized_url:
        return None

    if not normalized_url.startswith(("http://", "https://")):
        normalized_url = f"https://{normalized_url}"

    try:
        response = requests.get(
            normalized_url,
            timeout=6,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; SmartResearchManager/1.0; "
                    "+https://smart-research-manager.local)"
                )
            },
        )
    except Exception as exc:
        print(f"[URL 분석] 요청 실패 ({normalized_url}): {exc}")
        return None

    if response.status_code >= 400 or not response.text:
        print(f"[URL 분석] 응답 코드 {response.status_code} ({normalized_url})")
        return None

    final_url = response.url or normalized_url
    soup = BeautifulSoup(response.text, "html.parser")
    parsed = urlparse(final_url)
    domain = parsed.netloc

    def get_meta_by(attr_name: str, attr_value: str) -> Optional[str]:
        tag = soup.find('meta', attrs={attr_name: attr_value})
        if tag:
            return _clean_metadata_text(tag.get('content'))
        return None

    title_candidates: List[str] = []
    if soup.title and soup.title.string:
        title_text = _clean_metadata_text(soup.title.string)
        if title_text:
            title_candidates.append(title_text)

    og_title = get_meta_by('property', 'og:title')
    if og_title and og_title not in title_candidates:
        title_candidates.append(og_title)

    site_name = get_meta_by('property', 'og:site_name')

    description_candidates: List[str] = []
    meta_description = get_meta_by('name', 'description')
    if meta_description:
        description_candidates.append(meta_description)

    og_description = get_meta_by('property', 'og:description')
    if og_description and og_description not in description_candidates:
        description_candidates.append(og_description)

    keywords: List[str] = []
    keywords_lower: Set[str] = set()
    keywords_tag = soup.find('meta', attrs={'name': 'keywords'}) or soup.find('meta', attrs={'property': 'keywords'})
    if keywords_tag and keywords_tag.get('content'):
        for keyword in keywords_tag.get('content').split(','):
            cleaned = _clean_metadata_text(keyword, max_len=60)
            if cleaned:
                lowered = cleaned.lower()
                if lowered not in keywords_lower:
                    keywords.append(cleaned)
                    keywords_lower.add(lowered)

    heading_text: Optional[str] = None
    for heading_tag in ('h1', 'h2'):
        heading = soup.find(heading_tag)
        if heading and heading.get_text():
            cleaned_heading = _clean_metadata_text(heading.get_text())
            if cleaned_heading:
                heading_text = cleaned_heading
                break

    info_lines: List[str] = []
    if domain:
        info_lines.append(f"도메인: {domain}")
    info_lines.append(f"최종 URL: {final_url}")
    if site_name:
        info_lines.append(f"서비스 명: {site_name}")
    if title_candidates:
        info_lines.append(f"페이지 타이틀: {title_candidates[0]}")
    if heading_text:
        info_lines.append(f"주요 헤더: {heading_text}")
    if description_candidates:
        info_lines.append(f"설명: {description_candidates[0]}")
    if keywords:
        info_lines.append("태그 후보 키워드: " + ", ".join(keywords[:8]))

    context_block = "\n".join(line for line in info_lines if line)
    return context_block or None


@app.route('/api/workspace/generate-tags', methods=['POST'])
@tier_required(['free'])
def workspace_generate_tags():
    """
    [POST] 프로젝트 제목 기반 관련 태그 자동 생성
    - Gemini LLM 스트리밍 모드로 태그 실시간 생성
    - 프론트엔드에서 Server-Sent Events (SSE)로 수신
    - 쉼표 단위로 태그가 하나씩 추가되는 효과
    """
    try:
        data = request.json or {}
        project_title = (data.get('project_title') or '').strip()
        product_url = (data.get('product_url') or '').strip()
        
        if len(project_title) < 2 and not product_url:
            return jsonify({'success': False, 'error': '프로젝트 제목 또는 URL이 필요합니다.'}), 400
        
        url_context = _build_url_analysis_context(product_url) if product_url else None
        
        context_sections: List[str] = []
        if url_context:
            context_sections.append(f"서비스 URL 분석 결과:\n{url_context}")
        elif product_url:
            context_sections.append(f"서비스 URL/도메인: {product_url}")
        
        if project_title:
            context_sections.append(f"프로젝트 이름: {project_title}")
        
        context = "\n\n".join(section for section in context_sections if section).strip()
        
        prompt = f"""
{context}

지침:
- URL의 메타데이터가 제공되면 해당 정보를 우선으로 반영하세요.
- 프로젝트 이름만 제공되더라도 기업/브랜드, 산업군, 서비스 유형, 주요 기능, 타깃 사용자 등을 적극적으로 유추하여 7개 내외의 태그를 작성하세요.
- 태그는 쉼표로 구분하고, 각 태그는 2~4 단어 이내로 간결하게 작성합니다.
- 회사명 또는 브랜드명은 최대 1개만 포함하고, 나머지는 산업/서비스/기능/고객 관점의 태그로 구성하세요.
- 숫자, 기호, 불필요한 접미사는 제거하고, 한글 또는 널리 쓰이는 영문 약어를 사용합니다.
- 중복되거나 지나치게 일반적인 단어(예: 서비스, 플랫폼)는 피하세요.
- 가능한 경우 {project_title}의 서비스 유형을 추론하여 구체적인 산업/사용 시나리오 태그를 추가하세요.
"""
        
        # 스트리밍 응답 생성
        def generate_stream():
            # gemini_service를 사용해 한 번 생성 후, 쉼표 기준으로 태그 파싱해 스트리밍
            result = openai_service.generate_response(
                prompt,
                {"temperature": 0.3}
            )
            
            if not result.get('success'):
                error_data = {'error': result.get('error', '생성 실패'), 'done': True}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
                return
            
            accumulated_text = result.get('content') or ''
            tags = [tag.strip() for tag in accumulated_text.split(',') if tag.strip()]
            
            # 스트리밍 효과를 위해 태그를 하나씩 보내기
            current_tags = []
            for tag in tags[:8]:
                current_tags.append(tag)
                yield f"data: {json.dumps({'tags': current_tags[:8]}, ensure_ascii=False)}\n\n"
            
            # 최종 완료 신호
            final_tags = tags[:8]
            yield f"data: {json.dumps({'tags': final_tags, 'done': True}, ensure_ascii=False)}\n\n"
        
        from flask import Response
        return Response(generate_stream(), mimetype='text/event-stream')
            
    except Exception as e:
        log_error(e, "태그 생성 API 오류")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/studies/<int:study_id>', methods=['GET'])
@tier_required(['free'])
def get_study(study_id):
    """개별 연구 조회"""
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)
        allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}
        with session_scope() as db_session:
            study_row = WorkspaceRepository.get_study_by_id_with_owner(db_session, study_id)
        if not study_row:
            return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
        study, owner_id = study_row
        if owner_id is not None and str(owner_id) not in allowed_owner_ids:
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify(study)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/projects/<int:project_id>', methods=['GET'])
@tier_required(['free'])
def get_project(project_id):
    """개별 프로젝트 조회"""
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)
        allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}
        with session_scope() as db_session:
            project = WorkspaceRepository.get_project_by_id(db_session, project_id)
        if not project:
            return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
        owner_id = project.get('owner_id')
        if owner_id is not None and str(owner_id) not in allowed_owner_ids:
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
        return jsonify(project)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/projects/<int:project_id>/studies', methods=['GET'])
@tier_required(['free'])
def get_project_studies(project_id):
    """프로젝트의 연구 목록 조회"""
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)
        allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}

        with session_scope() as db_session:
            project_owner_id = WorkspaceRepository.get_project_owner_id(db_session, project_id)
            if project_owner_id is None:
                return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
            if str(project_owner_id) not in allowed_owner_ids:
                return jsonify({'error': '접근 권한이 없습니다.'}), 403
            studies = WorkspaceRepository.get_studies_by_project_id(db_session, project_id)
        return jsonify({'success': True, 'studies': studies})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/studies/<int:study_id>/schedule', methods=['GET'])
@tier_required(['free'])
def get_study_schedule(study_id):
    """연구의 일정 데이터 조회"""
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)
        allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}
        with session_scope() as db_session:
            study_row = WorkspaceRepository.get_study_by_id_with_owner(db_session, study_id)
            if not study_row:
                return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
            _study, owner_id = study_row
            if owner_id is not None and str(owner_id) not in allowed_owner_ids:
                return jsonify({'error': '접근 권한이 없습니다.'}), 403
            schedule = WorkspaceRepository.get_latest_schedule_by_study_id(db_session, study_id)
        if schedule:
            return jsonify({'success': True, 'schedule': schedule})
        return jsonify({'success': False, 'schedule': None})
    except Exception as e:
        print(f"[ERROR] get_study_schedule 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/artifacts/<int:artifact_id>', methods=['PUT'])
@tier_required(['free'])
def update_artifact(artifact_id):
    """아티팩트 내용 업데이트"""
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json
        content = data.get('content', '')
        if not content.strip():
            return jsonify({'success': False, 'error': '내용이 필요합니다.'}), 400

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        with session_scope() as db_session:
            updated = WorkspaceRepository.update_artifact_content_for_owner(
                db_session, artifact_id, int(user_id_int), content
            )
        if updated:
            return jsonify({'success': True, 'message': '아티팩트가 업데이트되었습니다.'})
        return jsonify({'success': False, 'error': '아티팩트를 찾을 수 없거나 접근 권한이 없습니다.'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/studies/<int:study_id>/artifacts', methods=['GET'])
@tier_required(['free'])
def get_study_artifacts(study_id):
    """연구의 아티팩트 목록 조회"""
    try:
        if not (SQLA_ENABLED and session_scope and WorkspaceRepository):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_int, err_body, err_status = _extract_request_user_id()
        if err_body:
            return err_body, err_status

        owner_ids = _resolve_owner_ids_sqlalchemy(user_id_int)
        allowed_owner_ids = {str(oid) for oid in owner_ids if oid is not None}

        with session_scope() as db_session:
            study_row = WorkspaceRepository.get_study_by_id_with_owner(db_session, study_id)
            if not study_row:
                return jsonify({'error': '연구를 찾을 수 없습니다.'}), 404
            _study, owner_id = study_row
            if owner_id is not None and str(owner_id) not in allowed_owner_ids:
                return jsonify({'error': '접근 권한이 없습니다.'}), 403
            artifacts = WorkspaceRepository.get_artifacts_by_study_id(db_session, study_id)
        return jsonify({'success': True, 'artifacts': artifacts})
    except Exception as e:
        print(f"[ERROR] get_study_artifacts 예외 발생: study_id={study_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/artifacts/<int:artifact_id>/stream', methods=['GET'])
@tier_required(['free'])
def stream_artifact_generation(artifact_id):
    """Artifact 생성 상태 실시간 스트리밍"""
    if not (SQLA_ENABLED and session_scope):
        return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

    # 사용자 ID 확인 (스트리밍 시작 전에)
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'error': '사용자 인증이 필요합니다.'}), 401
    try:
        user_id_int = int(user_id)
    except Exception:
        return jsonify({'error': '유효하지 않은 사용자 ID입니다.'}), 400
    
    def generate():
        with session_scope() as db_session:
            artifact = db_session.execute(
                select(Artifact).where(Artifact.id == artifact_id, Artifact.owner_id == user_id_int).limit(1)
            ).scalar_one_or_none()
        if not artifact:
            yield f"data: {json.dumps({'error': 'Artifact not found or access denied'})}\n\n"
            return

        # 이미 완료된 경우
        if artifact.status == 'completed':
            yield f"data: {json.dumps({'content': artifact.content, 'done': True})}\n\n"
            return
        
        # pending 상태면 폴링하면서 content 스트리밍
        import time
        last_content = ''
        
        for i in range(180):  # 최대 3분
            time.sleep(1)
            
            try:
                # artifact 다시 조회
                with session_scope() as db_session:
                    artifact = db_session.execute(
                        select(Artifact).where(Artifact.id == artifact_id).limit(1)
                    ).scalar_one_or_none()
                if artifact:
                    if artifact.content and artifact.content != last_content:
                        last_content = artifact.content
                        yield f"data: {json.dumps({'content': artifact.content}, ensure_ascii=False)}\n\n"

                    if artifact.status == 'completed':
                        yield f"data: {json.dumps({'done': True})}\n\n"
                        return

                    if artifact.status == 'failed':
                        yield f"data: {json.dumps({'error': '생성 실패', 'done': True})}\n\n"
                        return
            except Exception as e:
                # 일시적인 리소스 오류는 무시 (EAGAIN)
                if 'temporarily unavailable' not in str(e):
                    print(f"[ERROR] 스트리밍 폴링 오류: {e}")
                continue
        
        # 타임아웃
        yield f"data: {json.dumps({'error': '시간 초과', 'done': True})}\n\n"
    
    return app.response_class(generate(), mimetype='text/event-stream')

@app.route('/api/studies/<int:study_id>', methods=['DELETE'])
@tier_required(['free'])
def delete_study(study_id):
    """연구 삭제"""
    try:
        if not (SQLA_ENABLED and session_scope):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id = request.headers.get('X-User-ID')
        if not user_id:
            return jsonify({'error': '사용자 인증이 필요합니다.'}), 401
        try:
            user_id_int = int(user_id)
        except Exception:
            return jsonify({'error': '유효하지 않은 사용자 ID입니다.'}), 400

        with session_scope() as db_session:
            study_obj = db_session.execute(
                select(Study).where(Study.id == study_id).limit(1)
            ).scalar_one_or_none()
            if not study_obj:
                return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404

            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == study_obj.project_id).limit(1)
            ).scalar_one_or_none()
            if owner_id is None or int(owner_id) != user_id_int:
                return jsonify({'error': '접근 권한이 없습니다.'}), 403

            db_session.delete(study_obj)

        return jsonify({'success': True, 'message': '연구가 삭제되었습니다.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/artifacts/<int:artifact_id>', methods=['DELETE'])
@tier_required(['free'])
def delete_artifact(artifact_id):
    """아티팩트 삭제"""
    try:
        if not (SQLA_ENABLED and session_scope):
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id = request.headers.get('X-User-ID')
        if not user_id:
            return jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401
        try:
            user_id_int = int(user_id)
        except Exception:
            return jsonify({'success': False, 'error': '유효하지 않은 사용자 ID입니다.'}), 400

        with session_scope() as db_session:
            artifact = db_session.execute(
                select(Artifact).where(Artifact.id == artifact_id, Artifact.owner_id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not artifact:
                return jsonify({'success': False, 'error': '아티팩트를 찾을 수 없거나 삭제 권한이 없습니다.'}), 404
            db_session.delete(artifact)

        return jsonify({'success': True, 'message': '아티팩트가 삭제되었습니다.'})
    except Exception as e:
        log_error(e, f"아티팩트 {artifact_id} 삭제")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/studies/<int:study_id>/regenerate-plan', methods=['POST'])
@tier_required(['free'])
def regenerate_study_plan(study_id):
    """기존 연구의 계획서 재생성 - 비동기 처리"""
    try:
        if not (SQLA_ENABLED and session_scope):
            return jsonify({'success': False, 'error': 'DB 연결 실패'}), 500

        data = request.json or {}
        form_data = data.get('formData', {})
        user_id = request.headers.get('X-User-ID')
        if not user_id:
            return jsonify({'error': '사용자 인증이 필요합니다.'}), 401
        try:
            user_id_int = int(user_id)
        except Exception:
            return jsonify({'error': '유효하지 않은 사용자 ID입니다.'}), 400

        with session_scope() as db_session:
            study_obj = db_session.execute(
                select(Study).where(Study.id == study_id).limit(1)
            ).scalar_one_or_none()
            if not study_obj:
                return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404

            owner_id = db_session.execute(
                select(Project.owner_id).where(Project.id == study_obj.project_id).limit(1)
            ).scalar_one_or_none()
            if owner_id is None or int(owner_id) != user_id_int:
                return jsonify({'error': '접근 권한이 없습니다.'}), 403

            existing_plans = db_session.execute(
                select(Artifact).where(Artifact.study_id == study_id, Artifact.artifact_type == 'plan')
            ).scalars().all()
            for existing in existing_plans:
                db_session.delete(existing)

            pending = Artifact(
                study_id=study_id,
                artifact_type='plan',
                content='',
                owner_id=int(owner_id),
                status='pending',
            )
            db_session.add(pending)
            db_session.flush()
            db_session.refresh(pending)
            artifact_id = pending.id
            study_slug = study_obj.slug or str(study_id)
            project_id = study_obj.project_id

        project_keywords = fetch_project_keywords(project_id)

        def generate_plan_background():
            try:
                log_expert_analysis("백그라운드 계획서 재생성", f"시작: artifact_id={artifact_id}, study_id={study_id}")
                response = handle_oneshot_parallel_experts(form_data, project_keywords)
                with session_scope() as bg_session:
                    target = bg_session.execute(
                        select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                    ).scalar_one_or_none()
                    if not target:
                        return
                    if response.get('success'):
                        target.content = response.get('final_plan', '')
                        target.status = 'completed'
                        log_analysis_complete()
                        log_data_processing(
                            "계획서 재생성 완료",
                            {"artifact_id": artifact_id, "study_id": study_id},
                            "백그라운드 계획서 재생성 성공",
                        )
                    else:
                        bg_session.delete(target)
            except Exception as e:
                log_error(e, f"백그라운드 계획서 재생성 오류: artifact_id={artifact_id}, study_id={study_id}")
                try:
                    with session_scope() as bg_session:
                        target = bg_session.execute(
                            select(Artifact).where(Artifact.id == int(artifact_id)).limit(1)
                        ).scalar_one_or_none()
                        if target:
                            bg_session.delete(target)
                except Exception as delete_error:
                    log_error(delete_error, f"재생성 오류 후 artifact 삭제 실패: artifact_id={artifact_id}")

        thread = threading.Thread(target=generate_plan_background, daemon=True)
        thread.start()

        return jsonify({
            'success': True,
            'study_id': study_id,
            'study_slug': study_slug,
            'artifact_id': artifact_id,
            'message': '계획서를 생성하고 있습니다...'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/studies/<int:study_id>', methods=['PUT'])
@tier_required(['free'])
def update_study(study_id):
    """연구 정보 업데이트"""
    try:
        if SQLA_ENABLED and session_scope and WorkspaceRepository:
            data = request.json
            user_id_int, err_body, err_status = _extract_request_user_id()
            if err_body:
                return err_body, err_status

            with session_scope() as db_session:
                study_row = WorkspaceRepository.get_study_by_id_with_owner(db_session, study_id)
                if not study_row:
                    return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404
                _study, owner_id = study_row
                if owner_id is not None and int(owner_id) != int(user_id_int):
                    return jsonify({'error': '접근 권한이 없습니다.'}), 403

                update_data = {}
                for key, value in data.items():
                    if key == 'initial_input':
                        update_data['initial_input'] = value
                    elif key == 'name':
                        update_data['name'] = value
                    elif key == 'methodologies':
                        update_data['methodologies'] = value
                    elif key == 'target_audience':
                        update_data['target_audience'] = value
                    elif key == 'participant_count':
                        update_data['participant_count'] = value
                    elif key == 'start_date':
                        update_data['start_date'] = value
                    elif key == 'end_date':
                        update_data['end_date'] = value
                    elif key == 'timeline':
                        update_data['timeline'] = value
                    elif key == 'budget':
                        update_data['budget'] = value
                    elif key == 'additional_requirements':
                        update_data['additional_requirements'] = value

                if not update_data:
                    return jsonify({'success': False, 'error': '업데이트할 데이터가 없습니다.'}), 400

                updated = WorkspaceRepository.update_study_for_owner(
                    db_session, study_id, int(user_id_int), update_data
                )
                if not updated:
                    return jsonify({'success': False, 'error': '연구를 찾을 수 없습니다.'}), 404
            return jsonify({
                'success': True,
                'message': '연구 정보가 업데이트되었습니다.',
                'data': updated
            })

        return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500
    except Exception as e:
        print(f"[ERROR] 업데이트 오류: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
  
        
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
