"""RAG context preparation helpers for plan generation."""

from __future__ import annotations

from api_logger import log_step_search_clean


def _optimized_contexts(vector_adapter, rag_results, *, principles_max_length, examples_max_length):
    return (
        vector_adapter.improved_service.context_optimization(
            rag_results["principles"], max_length=principles_max_length
        ),
        vector_adapter.improved_service.context_optimization(
            rag_results["examples"], max_length=examples_max_length
        ),
    )


def prepare_oneshot_expert_rag_context(vector_adapter, *, keywords, project_keywords):
    rag_query = f"조사 계획서, 연구 설계: {', '.join(keywords)}"
    rag_results = vector_adapter.improved_service.hybrid_search(
        query_text=rag_query,
        principles_n=5,
        examples_n=4,
        topics=["조사목적", "연구목표", "방법론", "대상자", "일정", "예산", "계획서"],
        domain_keywords=project_keywords,
    )
    log_step_search_clean("원샷-RAG검색", rag_query, rag_results, "전문가 호출용 컨텍스트")
    return _optimized_contexts(
        vector_adapter,
        rag_results,
        principles_max_length=2500,
        examples_max_length=2000,
    )


def prepare_conversation_recommendation_rag_context(vector_adapter, *, step_int, keywords, project_keywords):
    rag_topics_by_step = {
        0: ["조사목적", "연구목표", "리서치질문", "계획서"],
        1: ["가설", "리서치질문", "연구질문"],
        2: ["방법론", "방법", "방법 설계"],
        3: ["대상자", "참가자모집", "스크리너"],
        4: None,
    }
    rag_query_prefix_by_step = {
        0: "리서치 배경, 상황, 조사 목적, 계획서(배경/상황)",
        1: "목적, 연구 목표, 리서치 질문, 연구질문, 가설",
        2: "방법론, 방법, 방법 설계, 세션 설계",
        3: "대상자, 참가자모집, 스크리너",
        4: "추가 요구사항, 제약조건, task 설계, 시나리오, 관찰 포인트, 편향 제거, 리스크",
    }

    step_topics = rag_topics_by_step.get(step_int, rag_topics_by_step[0])
    rag_prefix = rag_query_prefix_by_step.get(step_int, rag_query_prefix_by_step[0])
    rag_query = f"UX 리서치 계획서 설계 ({rag_prefix}): {', '.join(keywords)}"
    rag_results = vector_adapter.improved_service.hybrid_search(
        query_text=rag_query,
        principles_n=2,
        examples_n=2,
        topics=step_topics,
        domain_keywords=project_keywords,
    )
    log_step_search_clean("conversation-maker-recommend", rag_query, rag_results, "카드 후보 생성용 컨텍스트")
    return _optimized_contexts(
        vector_adapter,
        rag_results,
        principles_max_length=1200,
        examples_max_length=1000,
    )


def prepare_conversation_final_rag_context(vector_adapter, *, keywords, project_keywords):
    rag_query = f"조사 계획서, 연구 설계, 사용성 테스트, 인터뷰, 세션 설계, 분석 계획: {', '.join(keywords)}"
    rag_results = vector_adapter.improved_service.hybrid_search(
        query_text=rag_query,
        principles_n=8,
        examples_n=4,
        topics=["조사목적", "연구목표", "방법론", "대상자", "계획서", "사용성테스트", "인터뷰", "조사 설계", "리서치질문"],
        domain_keywords=project_keywords,
    )
    log_step_search_clean("conversation-maker-finalize", rag_query, rag_results, "최종 계획서 생성 컨텍스트")
    return _optimized_contexts(
        vector_adapter,
        rag_results,
        principles_max_length=1800,
        examples_max_length=1300,
    )
