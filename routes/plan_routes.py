"""
계획서 생성 및 대화형 리서치 Blueprint.

app.py에서 분리됨. URL prefix: /api
"""
import concurrent.futures
import json
import re
import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import List, Optional, Set

from flask import Blueprint, Response, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity
from utils.request_utils import _extract_request_user_id
from sqlalchemy import func, select

from api_logger import (
    log_analysis_complete, log_api_call, log_data_processing, log_error,
    log_expert_analysis, log_keyword_extraction, log_performance,
    log_step_search_clean,
)
from db.engine import session_scope
from db.models.core import Artifact, Project, Study, Team, TeamMember, User
from db.repositories.workspace_repository import WorkspaceRepository
from debug_utils import analyze_error_patterns, get_stats, request_tracker
from prompts.analysis_prompts import (
    GenerationPrompts, KeywordExtractionPrompts, PlanGeneratorPrompts,
)
from routes.auth import tier_required
from services.gemini_service import gemini_service
from services.openai_service import openai_service
from services.vector_service import vector_service
from utils.idempotency import (
    _complete_idempotency_entry, _fail_idempotency_entry,
    _reserve_idempotency_entry, _respond_from_entry,
)
from utils.keyword_utils import (
    _refine_extracted_keywords, extract_contextual_keywords_from_input,
    fetch_project_keywords,
)
from utils.llm_utils import _safe_parse_json_object, parse_llm_json_response
from utils.usage_metering import classify_feature_key, get_llm_usage_context, set_llm_usage_context

plan_bp = Blueprint('plan', __name__, url_prefix='/api')


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_llm_usage_context(user_id, request_id):
    try:
        claims = get_jwt() or {}
    except Exception:
        claims = {}

    company_id = claims.get("company_id")
    try:
        company_id = int(company_id) if company_id is not None else None
    except Exception:
        company_id = None

    if company_id is None and session_scope and user_id is not None:
        try:
            with session_scope() as db_session:
                company_id = db_session.execute(
                    select(User.company_id)
                    .where(User.id == int(user_id))
                    .limit(1)
                ).scalar_one_or_none()
        except Exception:
            company_id = None
    team_id = None
    if session_scope and user_id is not None:
        try:
            with session_scope() as db_session:
                team_id = db_session.execute(
                    select(Team.id)
                    .where(Team.owner_id == int(user_id), Team.status != "deleted")
                    .order_by(Team.created_at.asc())
                    .limit(1)
                ).scalar_one_or_none()
                if team_id is None:
                    team_id = db_session.execute(
                        select(TeamMember.team_id)
                        .join(Team, Team.id == TeamMember.team_id)
                        .where(TeamMember.user_id == int(user_id), Team.status != "deleted")
                        .order_by(TeamMember.joined_at.asc())
                        .limit(1)
                    ).scalar_one_or_none()
                team_id = int(team_id) if team_id is not None else None
        except Exception:
            team_id = None

    return {
        "company_id": company_id,
        "team_id": team_id,
        "user_id": int(user_id) if user_id is not None else None,
        "account_type": claims.get("account_type"),
        "endpoint": request.path or "",
        "feature_key": classify_feature_key(request.path or "") or "plan_generation",
        "request_id": request_id,
    }


def _run_with_llm_usage_context(context, func, *args, **kwargs):
    set_llm_usage_context(context)
    return func(*args, **kwargs)

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
            analysis["selected_methodologies"].append({"title": title, "content": content})
        elif "goal" in card_type or "hypothesis" in card_type or "question" in card_type:
            analysis["selected_goals"].append({"title": title, "content": content})
        elif "audience" in card_type or "quota" in card_type or "screener" in card_type:
            analysis["selected_audiences"].append({"title": title, "content": content})
        elif "context" in card_type or "project_context" in card_type:
            analysis["selected_context"].append({"title": title, "content": content})

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

    seen: Set[str] = set()
    out: List[str] = []
    for m in methods:
        if m.lower() in seen:
            continue
        seen.add(m.lower())
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# handle_oneshot_parallel_experts (workspace.py에서도 사용)
# ---------------------------------------------------------------------------

def handle_oneshot_parallel_experts(form_data, project_keywords: Optional[List[str]] = None):
    """원샷 방식: 폼 데이터 기반으로 8개 전문가 병렬 호출 → Pro 모델로 최종 취합"""
    try:
        project_keywords = [
            kw for kw in (project_keywords or []) if isinstance(kw, str) and kw.strip()
        ]

        problem_definition = form_data.get('problemDefinition', '')
        study_name = form_data.get('studyName', '')
        methodologies = form_data.get('methodologies', [])
        target_audience = form_data.get('targetAudience', '')
        participant_count = form_data.get('participantCount', '')
        start_date = form_data.get('startDate', '')
        timeline = form_data.get('timeline', '')
        budget = form_data.get('budget', '')
        additional_requirements = form_data.get('additionalRequirements', '')

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

        methodology_result_content = methodology_result['content']

        expert_configs = [
            ("연구 목표", GenerationPrompts.prompt_generate_research_goal),
            ("핵심 질문", GenerationPrompts.prompt_generate_core_questions),
            ("조사 대상", GenerationPrompts.prompt_generate_target_audience),
            ("참여자 기준", GenerationPrompts.prompt_generate_participant_criteria),
            ("분석 방법", GenerationPrompts.prompt_generate_analysis_method),
            ("일정 및 타임라인", GenerationPrompts.prompt_generate_timeline),
            ("액션 플랜", GenerationPrompts.prompt_generate_action_plan)
        ]

        def call_expert(expert_name, prompt_func):
            try:
                combined_input_with_methodology = f"""{combined_input}

**[방법론 전문가 결과]**
{methodology_result_content}
"""
                if expert_name == "분석 방법":
                    prompt = prompt_func(combined_input, methodology_result_content, principles_context, examples_context)
                else:
                    prompt = prompt_func(combined_input_with_methodology, principles_context, examples_context)

                result = openai_service.generate_response(prompt, {"temperature": 0.3})
                if result['success']:
                    return {'expert': expert_name, 'content': result['content'], 'success': True}
                return {'expert': expert_name, 'error': result.get('error'), 'success': False}
            except Exception as e:
                return {'expert': expert_name, 'error': str(e), 'success': False}

        log_expert_analysis("7개 전문가", "병렬 호출 시작 (방법론 결과 포함 + 일정 전문가)")

        expert_results = [methodology_expert_result]
        executor_usage_context = get_llm_usage_context()
        with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
            futures = [
                executor.submit(_run_with_llm_usage_context, executor_usage_context, call_expert, name, func)
                for name, func in expert_configs
            ]
            for future in concurrent.futures.as_completed(futures):
                expert_results.append(future.result())

        successful_experts = [r for r in expert_results if r['success']]

        if len(successful_experts) < 7:
            raise Exception(f"전문가 호출 실패: {len(successful_experts)}/8 성공")

        expert_outputs = "\n\n".join([
            f"### {r['expert']} 분석:\n{r['content']}"
            for r in successful_experts
        ])

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


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@plan_bp.route('/study-helper/chat', methods=['POST'])
@tier_required(['free'])
def study_helper_chat():
    """연구 생성 폼의 챗봇 도우미 (스트리밍 응답)"""
    try:
        data = request.json
        user_message = data.get('message', '')
        context = data.get('context', {})
        mode = data.get('mode', 'general')  # 'general' | 'help'
        task = data.get('task')

        current_form = context.get('currentForm', {})
        project_name = context.get('projectName', '프로젝트')

        context_info = f"""
현재 작성 중인 연구:
- 프로젝트: {project_name}
- 연구명: {current_form.get('studyName', '(미입력)')}
- 문제정의: {current_form.get('problemDefinition', '(미입력)')}
- 선택된 방법론: {', '.join(current_form.get('methodologies', [])) or '(미선택)'}
- 조사대상: {current_form.get('targetAudience', '(미입력)')}
- 희망일정: {current_form.get('timeline', '(미입력)')}
"""

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
            problem_def = current_form.get('problemDefinition', '').strip()

            CONCISE_POLICY = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 전달.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""

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

        if not user_message:
            legacy_form = data.get('formData') or {}
            if legacy_form:
                user_message = "현재 폼 기반으로 간결 조언을 제공해 주세요."
                context_form = {
                    'currentForm': legacy_form,
                    'projectName': context.get('projectName', '프로젝트')
                }
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

        generation_config = {"temperature": 0.2, "max_output_tokens": 1000, "top_p": 0.9}
        if mode == 'help':
            generation_config = {"temperature": 0.1, "max_output_tokens": 1000, "top_p": 0.8}

        def generate_streaming_response():
            try:
                result = openai_service.generate_response(helper_prompt, generation_config)
                if result['success']:
                    content = result['content']
                    words = content.split(' ')
                    for i, word in enumerate(words):
                        chunk_data = {
                            'content': word + (' ' if i < len(words) - 1 else ''),
                            'done': i == len(words) - 1
                        }
                        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                        time.sleep(0.02)
                else:
                    error_data = {'error': '응답 생성에 실패했습니다.', 'done': True}
                    yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_data = {'error': str(e), 'done': True}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        return current_app.response_class(
            generate_streaming_response(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/generator/create-plan-oneshot', methods=['POST'])
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
        if not session_scope:
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

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

        idempotency_key = f"{user_id_int}:{project_id_int}:{request_id}"
        idempotency_entry, is_new_request = _reserve_idempotency_entry(idempotency_key)
        if not is_new_request:
            return _respond_from_entry(idempotency_entry)

        try:
            claims = get_jwt() or {}
        except Exception:
            claims = {}
        tier = claims.get('tier') or 'free'
        llm_usage_context = _build_llm_usage_context(user_id_int, request_id)

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
            set_llm_usage_context(llm_usage_context)
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


@plan_bp.route('/conversation/message', methods=['POST'])
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

        conversation_text = "\n".join(
            [
                f"{msg.get('type', 'user')}: {msg.get('content', '')}"
                for msg in conversation
                if isinstance(msg, dict)
            ]
        )

        recent_user_messages = [
            msg.get('content', '')
            for msg in conversation[-6:]
            if isinstance(msg, dict) and msg.get('type') == 'user'
        ][-3:]
        recent_user_input = "\n".join(recent_user_messages) if recent_user_messages else ""

        combined_input = f"""[LEDGER]
{ledger_text}

[CONVERSATION]
{conversation_text}
""".strip()

        project_keywords: List[str] = []
        try:
            if project_id is not None:
                project_keywords = fetch_project_keywords(int(project_id))
        except Exception:
            project_keywords = []

        concise_source = combined_input[:5000]
        keywords = extract_contextual_keywords_from_input(concise_source)
        if project_keywords:
            keywords = _refine_extracted_keywords(keywords, project_keywords)
        log_keyword_extraction(keywords)

        RAG_TOPICS_BY_STEP = {
            0: ["조사목적", "연구목표", "리서치질문", "계획서"],
            1: ["가설", "리서치질문", "연구질문"],
            2: ["방법론", "방법", "방법 설계"],
            3: ["대상자", "참가자모집", "스크리너"],
            4: None,
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

        previous_analysis = _analyze_previous_step_selections(ledger_cards, step_int)

        step_goal_map = {
            0: "[상황값 명확화] 이 단계에서는 리서치를 시작하게 된 배경과 상황을 명확히 하는 것이 목표입니다. 핵심 맥락(리스크/사용 맥락/검증할 화면·기능)을 먼저 파악하여 컨텍스트를 고해상도로 만들기. 사용자가 이 단계에서 '어떤 상황에서 어떤 문제를 해결하려는지'를 구체적으로 생각할 수 있도록 도와주세요.",
            1: "[목적값 명확화] 이 단계에서는 리서치의 목적, 연구 질문, 가설을 명확히 하는 것이 목표입니다. 목표/연구질문/가설 후보 카드를 많이 생성하여 사용자가 '이번 조사로 무엇을 결정하고 싶은지'를 구체적으로 생각할 수 있도록 도와주세요.",
            2: "[방법론값 명확화] 이 단계에서는 리서치 방법론과 세션 설계를 명확히 하는 것이 목표입니다. 이전 단계에서 선택한 목적/가설을 바탕으로 방법론/세션 설계 후보 카드를 생성하여 사용자가 '어떤 방법으로 조사할지'를 구체적으로 생각할 수 있도록 도와주세요.",
            3: "[대상값 명확화] 이 단계에서는 조사 대상과 스크리너 기준을 명확히 하는 것이 목표입니다. 대상/쿼터/스크리너(필수/제외) 후보 카드를 생성하여 사용자가 '누구를 대상으로 조사할지'를 구체적으로 생각할 수 있도록 도와주세요.",
            4: "[추가 요구사항 명확화] 이 단계에서는 지금까지 수집한 정보를 종합 분석하여, 리서치 설계를 더욱 구체화하기 위해 필요한 추가 요구사항을 판단하고 제안합니다. 예: UT/IDI의 경우 task/시나리오, 특정 기능/화면 집중 관찰 포인트, 편향 제거 고려사항, 추가 제약사항 등. 사용자가 '추가로 무엇을 고려해야 하는지'를 구체적으로 생각할 수 있도록 도와주세요.",
        }
        step_goal = step_goal_map.get(step_int, step_goal_map[0])

        context_summary = ""
        if step_int == 2:
            if previous_analysis["selected_goals"]:
                goals_text = ", ".join([g["title"] for g in previous_analysis["selected_goals"][:3]])
                context_summary += f"이미 설정된 목적: {goals_text}\n"
            if previous_analysis["selected_methodologies"]:
                methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
                context_summary += f"⚠️ 이미 선택된 방법론이 있습니다: {methods_text}\n"
                context_summary += "이 경우, 선택된 방법론의 세부 설계나 추가 방법론 제안에 집중하세요.\n"
        elif step_int == 3:
            if previous_analysis["selected_methodologies"]:
                methods_text = ", ".join([m["title"] for m in previous_analysis["selected_methodologies"][:3]])
                context_summary += f"선택된 방법론: {methods_text}\n"
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
        elif step_int == 4:
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

        # Step별 interrogation_rules는 길이 때문에 별도 변수로 관리
        interrogation_rules = _get_interrogation_rules(step_int)

        previous_summary = ""
        if context_summary:
            previous_summary = f"\n[이전 단계 결과 분석]\n{context_summary}\n"

        has_previous_selections = len(ledger_cards) > 0
        is_step_transition = not conversation_text.strip()
        transition_hint = ""
        if is_step_transition:
            if has_previous_selections:
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
- message 필드를 생성하지 않아도 됩니다 (기본 프롬프트가 이미 표시되므로).
"""
            else:
                transition_hint = """
[단계 전환 모드 - 새 단계 시작]
- 사용자가 이전 단계에서 아직 카드를 선택하지 않았습니다.
- 이번 단계의 기본 프롬프트를 따르고, 이전 단계 선택을 언급하지 마세요.
- message 필드에서 이번 단계의 목적과 필요성을 자연스럽게 안내하세요.
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
  * CONVERSATION의 **가장 최근 사용자 입력**이 가장 중요합니다.
  * **추론 확장이 핵심**: 사용자가 명시적으로 말하지 않은 부분도 적극적으로 추론하여 카드로 생성하세요.
- **컨텍스트 종합 분석**: CONVERSATION 전체를 종합해서 핵심 키워드와 맥락을 파악하세요.
- **중복 방지 (완화된 기준)**: 완전히 동일한 내용의 카드는 생성하지 마세요. 하지만 새로운 관점이 있으면 새로운 카드로 생성하세요.
- 후보는 "과감하게" 구체적으로 제시하세요.
- 교과서 설명 금지. 일반론 금지. 추상적 표현 금지.
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
                return ["task", "analysis_plan", "scope_item", "note"]
            return []

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

                if step_int < 4:
                    type_matches = any(allowed_type in c_type for allowed_type in allowed_types)
                    if not type_matches:
                        continue

                filtered_cards.append(c)
            draft_cards = filtered_cards

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


def _get_interrogation_rules(step_int: int) -> str:
    """단계별 강제 규칙 반환 (프롬프트 가독성을 위해 분리)"""
    if step_int == 0:
        return """
[Step0 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 5개, 최대 10개를 반드시 생성하세요.
- **절대 규칙 - 카드 타입 제한**: Step 0에서는 **오직 project_context와 scope_item 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    elif step_int == 1:
        return """
[Step1 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 7개, 최대 10개 생성하세요. (0개는 금지)
- **카드 구성 강제**: research_goal 타입 최소 3개, hypothesis 타입 최소 4개
- **절대 규칙 - 카드 타입 제한**: Step 1에서는 **오직 research_goal과 hypothesis 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    elif step_int == 2:
        return """
[Step2 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 2개, 최대 5개 생성하세요. (0개는 금지)
- **절대 규칙 - 카드 타입 제한**: Step 2에서는 **오직 methodology_set 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    elif step_int == 3:
        return """
[Step3 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 최소 6개, 최대 10개 생성하세요. (0개는 금지)
- **카드 구성 강제**: audience_segment 최소 2개, quota_plan 최소 2개, screener_rule 최소 2개
- **절대 규칙 - 카드 타입 제한**: Step 3에서는 **오직 audience_segment, quota_plan, screener_rule 타입만** 생성하세요.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    elif step_int == 4:
        return """
[Step4 강제 규칙 - 반드시 준수]
- next_question을 1개 생성하세요.
- draft_cards는 **최소 3개, 최대 8개** 생성하세요. (0개 금지)
- **카드 타입**: task, analysis_plan, scope_item, note 타입을 사용 가능합니다.
- title/content는 **절대 '?'로 끝내지 마세요.**
"""
    return ""


@plan_bp.route('/generator/conversation-maker/finalize-oneshot', methods=['POST'])
@tier_required(['free'])
def conversation_maker_finalize_oneshot():
    """카드 누적형 ConversationStudyMaker - Study+pending plan artifact 생성 후 백그라운드 계획서 생성."""
    start_time = time.time()
    idempotency_key = None
    idempotency_completed = False
    created_study_id = None
    created_artifact_id = None

    try:
        if not session_scope:
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

        user_id_int, err_resp, err_code = _extract_request_user_id()
        if err_resp:
            return err_resp, err_code

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
        llm_usage_context = _build_llm_usage_context(user_id_int, request_id)
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
            set_llm_usage_context(llm_usage_context)
            try:
                log_expert_analysis("ConversationStudyMaker 최종계획서", f"시작: artifact_id={artifact_id}")

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
- 교과서형 일반론 금지. 추상적 문장 금지. 이 프로젝트 맥락에 맞춰 구체화하세요.
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


# ---------------------------------------------------------------------------
# 디버그 엔드포인트
# ---------------------------------------------------------------------------

@plan_bp.route('/debug/stats', methods=['GET'])
@tier_required(['free'])
def debug_get_stats():
    """요청 통계 및 에러 분석"""
    try:
        stats = get_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/debug/analyze-errors', methods=['GET'])
@tier_required(['free'])
def debug_analyze_errors():
    """에러 패턴 분석"""
    try:
        analyze_error_patterns()
        return jsonify({'success': True, 'message': '에러 분석 완료 (콘솔 확인)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@plan_bp.route('/debug/health', methods=['GET'])
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
