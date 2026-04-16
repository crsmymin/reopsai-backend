# 개선된 RAG 기반 API 사용 예시

# 1. 개선된 VectorDBService 사용
from improved_vector_db_service import VectorDBServiceWrapper

# 기존 코드와 호환되는 방식으로 초기화
vector_service = VectorDBServiceWrapper(
    db_path="./chroma_db", 
    collection_name="ux_rag"
)

# 2. 개선된 검색 사용 예시
def improved_plan_generation_with_rag(conversation_summary):
    """
    개선된 RAG를 활용한 계획서 생성
    """
    
    # 하이브리드 검색 - 원칙과 예시를 분리하여 검색
    rag_results = vector_service.improved_service.hybrid_search(
        query_text=conversation_summary,
        principles_n=8,  # 원칙은 더 많이
        examples_n=3,     # 예시는 적당히
        topics=["계획서", "리서치"],  # 관련 주제 필터링
        methodology="usability_test"  # 방법론 필터링
    )
    
    # 컨텍스트 최적화
    principles_context = vector_service.improved_service.context_optimization(
        rag_results["principles"], 
        max_length=1500
    )
    
    examples_context = vector_service.improved_service.context_optimization(
        rag_results["examples"], 
        max_length=1000
    )
    
    # 프롬프트 구성
    prompt = f"""
    다음 원칙과 예시를 참고하여 조사 계획서를 작성해주세요.
    
    [원칙]
    {principles_context}
    
    [예시]
    {examples_context}
    
    [사용자 요구사항]
    {conversation_summary}
    
    위 원칙을 따라 예시를 참고하여 전문적인 조사 계획서를 작성해주세요.
    """
    
    return prompt

def improved_survey_generation_with_rag(research_plan_content, key_variables):
    """
    개선된 RAG를 활용한 설문 생성
    """
    
    # 쿼리 확장으로 검색 정확도 향상
    expanded_query = vector_service.improved_service.query_expansion(
        f"설문조사 설계 {research_plan_content}"
    )
    
    # 도메인별 필터링
    domain = "fintech" if "금융" in research_plan_content or "핀테크" in research_plan_content else "general"
    
    rag_results = vector_service.improved_service.hybrid_search(
        query_text=expanded_query,
        principles_n=6,
        examples_n=4,
        topics=["설문", "스크리너"],
        methodology="screener_survey"
    )
    
    # 품질 점수 기반 필터링된 컨텍스트
    principles_context = rag_results["principles"]
    examples_context = rag_results["examples"]
    
    prompt = f"""
    다음 설문 설계 원칙과 고품질 예시를 참고하여 설문을 생성해주세요.
    
    [설문 설계 원칙]
    {principles_context}
    
    [고품질 설문 예시]
    {examples_context}
    
    [조사 계획서]
    {research_plan_content}
    
    [핵심 변수]
    {key_variables}
    
    위 원칙을 따라 예시를 참고하여 전문적인 설문을 생성해주세요.
    """
    
    return prompt

# 3. 성능 모니터링을 위한 검색 품질 평가
def evaluate_search_quality(query_text, retrieved_context):
    """
    검색 품질 평가 함수
    """
    # 간단한 품질 지표들
    quality_metrics = {
        "context_length": len(retrieved_context),
        "relevance_score": 0.0,  # 실제로는 더 정교한 방법 사용
        "diversity_score": 0.0,   # 다양한 소스에서 온 정보인지
        "freshness_score": 0.0    # 최신 정보인지
    }
    
    # 컨텍스트 길이가 적절한지 확인
    if 500 <= quality_metrics["context_length"] <= 2000:
        quality_metrics["relevance_score"] += 0.3
    
    # 다양한 소스 확인
    sources = set()
    for line in retrieved_context.split('\n'):
        if '[참고 자료' in line and '출처:' in line:
            source = line.split('출처: ')[1].split(',')[0]
            sources.add(source)
    
    quality_metrics["diversity_score"] = min(len(sources) / 3, 1.0)
    
    return quality_metrics

# 4. A/B 테스트를 위한 기존 방식과 개선된 방식 비교
def compare_rag_approaches(query_text):
    """
    기존 RAG와 개선된 RAG 성능 비교
    """
    
    # 기존 방식 (단순 검색)
    old_context = vector_service.search(
        query_text=query_text,
        n_results=5,
        filter_metadata={"data_type": "원칙"}
    )
    
    # 개선된 방식 (하이브리드 + 필터링)
    new_results = vector_service.improved_service.hybrid_search(
        query_text=query_text,
        principles_n=5,
        examples_n=3,
        min_score=0.7
    )
    new_context = new_results["principles"] + "\n" + new_results["examples"]
    
    # 품질 비교
    old_quality = evaluate_search_quality(query_text, old_context)
    new_quality = evaluate_search_quality(query_text, new_context)
    
    return {
        "old_approach": {
            "context": old_context,
            "quality": old_quality
        },
        "new_approach": {
            "context": new_context,
            "quality": new_quality
        },
        "improvement": {
            "relevance": new_quality["relevance_score"] - old_quality["relevance_score"],
            "diversity": new_quality["diversity_score"] - old_quality["diversity_score"]
        }
    }
