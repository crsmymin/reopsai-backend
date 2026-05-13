"""
키워드 처리 유틸리티.

프로젝트 키워드 조회, 정제, 추출 기능을 제공합니다.
"""
from importlib import import_module
import re
from typing import Iterable, List, Optional, Set

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


def fetch_project_keywords(project_id) -> List[str]:
    """
    프로젝트 키워드(도메인 태그)를 조회합니다.
    """
    keywords: List[str] = []
    if not project_id:
        return keywords

    try:
        session_scope = import_module("db.engine").session_scope
        WorkspaceRepository = import_module("db.repositories.workspace_repository").WorkspaceRepository
    except Exception:
        return keywords

    if session_scope and WorkspaceRepository:
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

    return keywords


def extract_contextual_keywords_from_input(text) -> List[str]:
    """사용자 입력에서 맥락적으로 중요한 키워드들을 모두 추출"""
    try:
        get_openai_service = import_module("reopsai.infrastructure.llm").get_openai_service
        openai_service = get_openai_service()
        KeywordExtractionPrompts = import_module("prompts.analysis_prompts").KeywordExtractionPrompts

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
            fallback = [word for word in text.split() if len(word) > 2][:10]
            return _refine_extracted_keywords(fallback)

    except Exception as e:
        print(f"[DEBUG] 키워드 추출 오류: {e}")
        fallback = [word for word in text.split() if len(word) > 2][:10]
        return _refine_extracted_keywords(fallback)


def create_concise_summary_for_rag(conversation_or_text, previous_summaries=None, step_name="") -> str:
    """RAG 검색용 간결한 요약 생성 - LLM 기반 키워드 추출 사용"""
    if previous_summaries:
        previous_texts = []
        for step, summary in previous_summaries.items():
            previous_texts.append(f"{step}: {summary}")

        previous_text = " ".join(previous_texts)
        previous_keywords = extract_contextual_keywords_from_input(previous_text)
        previous_context = f"이전 단계: {', '.join(previous_keywords)}"
    else:
        previous_context = ""

    if isinstance(conversation_or_text, str):
        current_text = conversation_or_text
    else:
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

    if previous_context and current_context:
        concise_summary = f"{previous_context} | {current_context}"
    elif current_context:
        concise_summary = current_context
    else:
        concise_summary = step_name

    return concise_summary


__all__ = [
    "KEYWORD_STOPWORDS",
    "KEYWORD_STOPWORDS_LOWER",
    "_clean_metadata_text",
    "_refine_extracted_keywords",
    "create_concise_summary_for_rag",
    "extract_contextual_keywords_from_input",
    "fetch_project_keywords",
]
