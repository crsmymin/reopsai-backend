#!/usr/bin/env python3
"""
API 호출 과정 로깅 시스템
API 요청, RAG 검색, 데이터 처리 과정을 상세히 로깅합니다.
"""

import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
import traceback
from collections import deque

from pii_utils import sanitize_for_log, sanitize_prompt_for_llm

class APILogger:
    """API 호출 과정 로깅 클래스"""
    
    def __init__(self, max_logs: int = 1000):
        # 최근 1000개 로그만 메모리에 유지 (메모리 누수 방지)
        self.logs = deque(maxlen=max_logs)
    
    def log_request(self, endpoint: str, method: str, data: Dict[str, Any]):
        """API 요청 로깅"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        safe_data = sanitize_for_log(data or {})
        log_entry = {
            "timestamp": timestamp,
            "type": "REQUEST",
            "endpoint": endpoint,
            "method": method,
            "data": safe_data
        }
        self.logs.append(log_entry)
        print(f"\n🔵 [{timestamp}] API 요청: {method} {endpoint}")
        print(f"   📥 요청 데이터: {json.dumps(safe_data, ensure_ascii=False, indent=2)}")
    
    def log_rag_search(self, query: str, search_type: str, results: Dict[str, Any]):
        """RAG 검색 로깅 (간단한 성능 모니터링용)"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = {
            "timestamp": timestamp,
            "type": "RAG_SEARCH",
            "query": query,
            "search_type": search_type,
            "results": results
        }
        self.logs.append(log_entry)
        
        # 자세한 검색 정보 출력
        print(f"\n🔍 [{timestamp}] RAG 검색: {search_type}")
        print(f"   🔎 검색어: {query[:150]}{'...' if len(query) > 150 else ''}")
        
        if isinstance(results, dict):
            for key, value in results.items():
                if isinstance(value, str):
                    print(f"   📊 {key}: {len(value)}자")
                    # 청크별로 상세 정보 표시
                    if len(value) > 0:
                        chunks = value.split('\n\n')
                        print(f"      📋 {len(chunks)}개 청크 검색됨:")
                        for i, chunk in enumerate(chunks[:2]):  # 최대 2개 청크 미리보기
                            if chunk.strip():
                                chunk_preview = chunk[:80].replace('\n', ' ').strip()
                                print(f"         {i+1}. {chunk_preview}{'...' if len(chunk) > 80 else ''}")
                        if len(chunks) > 2:
                            print(f"         ... 외 {len(chunks)-2}개 청크 더")
                    else:
                        print(f"      ⚠️ 빈 결과!")
                elif isinstance(value, list):
                    print(f"   📊 {key}: {len(value)}개 항목")
                else:
                    print(f"   📊 {key}: {value}")
        else:
            print(f"   📊 결과: {len(str(results))}자")
    
    def log_data_processing(self, step: str, data: Any, details: Optional[str] = None):
        """데이터 처리 과정 로깅"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = {
            "timestamp": timestamp,
            "type": "DATA_PROCESSING",
            "step": step,
            "data": data,
            "details": details
        }
        self.logs.append(log_entry)
        
        print(f"\n⚙️ [{timestamp}] 데이터 처리: {step}")
        if details:
            print(f"   📝 상세: {details}")
        
        if isinstance(data, str):
            print(f"   📄 데이터 길이: {len(data)}자")
        elif isinstance(data, dict):
            print(f"   📄 데이터 키: {list(data.keys())}")
        elif isinstance(data, list):
            print(f"   📄 데이터 개수: {len(data)}개")
    
    def log_llm_call(self, prompt: str, response: Any, model: str = "gemini"):
        """LLM 호출 로깅"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        safe_prompt, _, _ = sanitize_prompt_for_llm(prompt or "")
        safe_response = sanitize_for_log(response)
        log_entry = {
            "timestamp": timestamp,
            "type": "LLM_CALL",
            "prompt": safe_prompt,
            "response": safe_response,
            "model": model
        }
        self.logs.append(log_entry)
        
        print(f"\n🤖 [{timestamp}] LLM 호출: {model}")
        print(f"   📝 프롬프트 길이: {len(safe_prompt)}자")
        
        if isinstance(safe_response, dict) and 'content' in safe_response:
            print(f"   📤 응답 길이: {len(str(safe_response['content']))}자")
        elif isinstance(safe_response, str):
            print(f"   📤 응답 길이: {len(safe_response)}자")
        else:
            print(f"   📤 응답 타입: {type(safe_response)}")
    
    def log_error(self, error: Exception, context: str = "", user_info: Dict[str, Any] = None):
        """오류 로깅 (사용자 추적 강화)"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        safe_user_info = sanitize_for_log(user_info or {})
        safe_error_message = sanitize_for_log(str(error))
        log_entry = {
            "timestamp": timestamp,
            "type": "ERROR",
            "error_type": type(error).__name__,
            "error_message": safe_error_message,
            "context": context,
            "user_info": safe_user_info,
            "traceback": traceback.format_exc()
        }
        self.logs.append(log_entry)
        
        print(f"\n❌ [{timestamp}] 오류 발생: {context}")
        print(f"   🚨 오류 타입: {type(error).__name__}")
        print(f"   💥 오류 메시지: {safe_error_message}")
        if user_info:
            print(f"   👤 사용자 정보: {json.dumps(safe_user_info, ensure_ascii=False)}")
        print(f"   📍 상세 정보: {traceback.format_exc()}")
    
    
    def log_performance(self, operation: str, duration: float, details: Optional[str] = None):
        """성능 로깅"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = {
            "timestamp": timestamp,
            "type": "PERFORMANCE",
            "operation": operation,
            "duration": duration,
            "details": details
        }
        self.logs.append(log_entry)
        
        print(f"\n⏱️ [{timestamp}] 성능 측정: {operation}")
        print(f"   ⏰ 소요 시간: {duration:.3f}초")
        if details:
            print(f"   📊 상세: {details}")
    
    def get_logs(self) -> List[Dict[str, Any]]:
        """로그 목록 반환"""
        return self.logs
    
    def clear_logs(self):
        """로그 초기화"""
        self.logs.clear()
        print("\n🧹 로그가 초기화되었습니다.")

# 전역 로거 인스턴스
api_logger = APILogger()

def log_api_call(endpoint: str, method: str, data: Optional[Dict[str, Any]] = None):
    """API 호출 로깅 헬퍼 함수"""
    api_logger.log_request(endpoint, method, data or {})

def log_rag_search(query: str, search_type: str, results: Dict[str, Any]):
    """RAG 검색 로깅 헬퍼 함수"""
    api_logger.log_rag_search(query, search_type, results)

def log_data_processing(step: str, data: Any, details: Optional[str] = None):
    """데이터 처리 로깅 헬퍼 함수"""
    api_logger.log_data_processing(step, data, details)

def log_llm_call(prompt: str, response: Any, model: str = "gemini"):
    """LLM 호출 로깅 헬퍼 함수"""
    api_logger.log_llm_call(prompt, response, model)

def log_error(error: Exception, context: str = "", user_info: Dict[str, Any] = None):
    """오류 로깅 헬퍼 함수 (사용자 추적 강화)"""
    api_logger.log_error(error, context, user_info)

def log_response(endpoint: str, response_data: Any, status_code: int = 200):
    """API 응답 로깅 헬퍼 함수"""
    api_logger.log_response(endpoint, response_data, status_code)

def log_performance(operation: str, duration: float, details: Optional[str] = None):
    """성능 로깅 헬퍼 함수"""
    api_logger.log_performance(operation, duration, details)

def log_rag_performance(query: str, search_type: str, results: Dict[str, Any], duration: float = 0.0):
    """RAG 성능 모니터링 전용 로그 함수"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    # 검색어에서 핵심 키워드 추출
    query_words = [word for word in query.split() if len(word) > 2][:5]
    query_keywords = ', '.join(query_words)
    
    # 결과 요약
    result_summary = {}
    if isinstance(results, dict):
        for key, value in results.items():
            if isinstance(value, str):
                result_summary[key] = f"{len(value)}자"
            elif isinstance(value, list):
                result_summary[key] = f"{len(value)}개"
            else:
                result_summary[key] = str(value)
    
    print(f"\n🚀 [{timestamp}] RAG 성능: {search_type}")
    print(f"   🔎 검색어: {query_keywords}")
    print(f"   ⏱️ 소요시간: {duration:.3f}초")
    print(f"   📊 결과: {result_summary}")
    
    # 성능 평가
    if duration > 2.0:
        print(f"   ⚠️ 느린 검색 (>{duration:.1f}초)")
    elif duration < 0.5:
        print(f"   ✅ 빠른 검색 (<{duration:.1f}초)")
    
    # 결과 품질 평가
    if isinstance(results, dict):
        total_content = sum(len(str(v)) for v in results.values() if isinstance(v, str))
        if total_content < 100:
            print(f"   ⚠️ 결과 부족 ({total_content}자)")
        elif total_content > 5000:
            print(f"   ✅ 풍부한 결과 ({total_content}자)")
        else:
            print(f"   📊 적절한 결과 ({total_content}자)")

def log_rag_search_simple(query: str, search_type: str, results: Dict[str, Any]):
    """RAG 검색 간단 로그 (성능 향상용)"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    # 검색어 요약 (핵심 키워드만)
    query_summary = query[:80].replace('\n', ' ').strip()
    if len(query) > 80:
        query_summary += "..."
    
    # 결과 요약
    if isinstance(results, dict):
        result_info = []
        for key, value in results.items():
            if isinstance(value, str):
                result_info.append(f"{key}:{len(value)}자")
            elif isinstance(value, list):
                result_info.append(f"{key}:{len(value)}개")
        result_summary = " | ".join(result_info)
    else:
        result_summary = f"결과:{len(str(results))}자"
    
    print(f"🔍 [{timestamp}] {search_type} | {query_summary} | {result_summary}")

def log_step_search(step_name: str, query: str, results: Dict[str, Any], context: str = ""):
    """단계별 검색 모니터링 로그"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    # 검색어에서 핵심 키워드 추출 (개선)
    # "조사 목적 설정, 연구 목표 정의, 가설 설정, 핵심 질문: 키워드1, 키워드2, ..." 형태에서 실제 키워드만 추출
    if ": " in query:
        # 콜론 뒤의 실제 키워드 부분만 추출
        actual_keywords = query.split(": ", 1)[1]
        # 콤마로 구분된 키워드들을 정리
        keyword_list = [kw.strip() for kw in actual_keywords.split(",") if kw.strip()]
        query_keywords = ', '.join(keyword_list[:5])  # 최대 5개만 표시
    else:
        # 콜론이 없으면 기존 방식 사용
        query_words = [word for word in query.split() if len(word) > 2][:3]
        query_keywords = ', '.join(query_words)
    
    # 유사도 스코어 추출 (결과 텍스트에서 파싱)
    max_score = "N/A"
    min_score = "N/A"
    if isinstance(results, dict):
        principles_text = results.get('principles', '')
        examples_text = results.get('examples', '')
        
        # 결과 텍스트에서 "관련도: 0.XX" 패턴 찾기
        import re
        all_scores = []
        for text in [principles_text, examples_text]:
            scores = re.findall(r'관련도: (0\.\d+)', text)
            all_scores.extend([float(s) for s in scores])
        
        if all_scores:
            max_score = f"{max(all_scores):.2f}"
            min_score = f"{min(all_scores):.2f}"
    
    # 쿼리 길이 체크
    query_length = len(query)
    if query_length > 200:
        print(f"\n⚠️ [{timestamp}] {step_name} 단계 검색 - 쿼리 과다")
        print(f"   🔎 검색어: {query_keywords}")
        print(f"   📏 쿼리 길이: {query_length}자 (권장: <200자)")
    else:
        print(f"\n✅ [{timestamp}] {step_name} 단계 검색 - 쿼리 적절")
        print(f"   🔎 검색어: {query_keywords}")
        print(f"   📏 쿼리 길이: {query_length}자")
    
    # 결과 분석
    if isinstance(results, dict):
        principles_len = len(results.get('principles', ''))
        examples_len = len(results.get('examples', ''))
        
        # 검색 품질 평가
        quality_score = 0
        if principles_len > 500:
            quality_score += 1
        if examples_len > 200:
            quality_score += 1
        if principles_len > 0 and examples_len > 0:
            quality_score += 1
            
        quality_indicator = "🟢" if quality_score >= 2 else "🟡" if quality_score >= 1 else "🔴"
        
        print(f"   📊 원칙: {principles_len}자 | 예시: {examples_len}자")
        print(f"   🎯 품질: {quality_score}/3 ({'우수' if quality_score >= 2 else '보통' if quality_score >= 1 else '부족'})")
        print(f"   📈 유사도: Max={max_score}, Min={min_score}")
        
        if context:
            print(f"   📝 맥락: {context}")
            
        # 검색 결과 미리보기 (더 자세한 청크 정보)
        if principles_len > 0:
            principles_text = results['principles']
            # 청크별로 분할하여 각 청크의 정보 표시
            chunks = principles_text.split('\n\n')
            print(f"   📋 검색된 청크들:")
            for i, chunk in enumerate(chunks[:3]):  # 최대 3개 청크만 표시
                if chunk.strip():
                    chunk_preview = chunk[:100].replace('\n', ' ').strip()
                    print(f"      {i+1}. {chunk_preview}{'...' if len(chunk) > 100 else ''}")
            
            if len(chunks) > 3:
                print(f"      ... 외 {len(chunks)-3}개 청크 더")
        
        if examples_len > 0:
            examples_text = results['examples']
            chunks = examples_text.split('\n\n')
            print(f"   📋 예시 청크들:")
            for i, chunk in enumerate(chunks[:2]):  # 최대 2개 청크만 표시
                if chunk.strip():
                    chunk_preview = chunk[:100].replace('\n', ' ').strip()
                    print(f"      {i+1}. {chunk_preview}{'...' if len(chunk) > 100 else ''}")
            
            if len(chunks) > 2:
                print(f"      ... 외 {len(chunks)-2}개 청크 더")
    else:
        print(f"   📊 결과: {len(str(results))}자")

def log_rag_quality_check(step: str, query: str, results: Dict[str, Any]):
    """RAG 검색 품질 체크 로그"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    if isinstance(results, dict):
        principles = results.get('principles', '')
        examples = results.get('examples', '')
        
        # 품질 체크
        checks = []
        
        # 원칙 검색 체크
        if len(principles) > 500:
            checks.append("✅ 원칙 풍부")
        elif len(principles) > 100:
            checks.append("🟡 원칙 보통")
        else:
            checks.append("❌ 원칙 부족")
            
        # 예시 검색 체크
        if len(examples) > 200:
            checks.append("✅ 예시 풍부")
        elif len(examples) > 50:
            checks.append("🟡 예시 보통")
        else:
            checks.append("❌ 예시 부족")
            
        # 관련성 체크 (간단한 키워드 매칭)
        query_lower = query.lower()
        principles_lower = principles.lower()
        examples_lower = examples.lower()
        
        relevance_score = 0
        if any(word in principles_lower for word in query_lower.split() if len(word) > 2):
            relevance_score += 1
        if any(word in examples_lower for word in query_lower.split() if len(word) > 2):
            relevance_score += 1
            
        if relevance_score >= 1:
            checks.append("✅ 관련성 양호")
        else:
            checks.append("❌ 관련성 부족")
        
        print(f"\n📋 [{timestamp}] {step} 품질 체크")
        print(f"   🔎 검색어: {query[:60]}{'...' if len(query) > 60 else ''}")
        print(f"   📊 체크 결과: {' | '.join(checks)}")
        print(f"   📈 원칙: {len(principles)}자 | 예시: {len(examples)}자")

# =====================================================================
# 새로운 깔끔한 로깅 시스템
# =====================================================================

def log_user_request(feature_name: str, user_input: str):
    """사용자 요청 로그 - 깔끔한 포맷"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # 입력 텍스트를 1-2문장으로 요약
    input_summary = user_input[:100] + "..." if len(user_input) > 100 else user_input
    
    print(f"\n{'='*60}")
    print(f"유저 요청 : [{feature_name}]기능")
    print(f"인풋 : [{input_summary}]")
    print(f"{'='*60}")

def log_keyword_extraction(keywords: list):
    """키워드 추출 로그"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    print(f"\n분석 중 : 유저 입력 키워드 내 주요 키워드 추출")
    print(f"추출된 키워드 : {', '.join(keywords[:8])}")  # 최대 8개만 표시
    print(f"{'-'*40}")

def log_rag_search_clean(query_keywords: str, principles_count: int, examples_count: int, max_score: float = 0.0, min_score: float = 0.0, top_result: str = ""):
    """RAG 검색 결과 로그 - 깔끔한 포맷"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    print(f"\nRAG 검색 키워드 : {query_keywords}")
    print(f"원칙 : {principles_count}자  예시 : {examples_count}자")
    print(f"{'-'*40}")
    
    if max_score > 0:
        print(f"유사도 검색")
        print(f"MAX : {max_score:.2f}  MIN : {min_score:.2f}")
        
        if top_result:
            # 실제 텍스트 내용 표시 (메타데이터 제거)
            clean_result = top_result
            # [[참고 자료 1 (출처: ...)]] 형태의 메타데이터 제거
            import re
            clean_result = re.sub(r'\[\[.*?\]\]', '', clean_result)
            clean_result = re.sub(r'\(출처:.*?\)', '', clean_result)
            clean_result = re.sub(r'\(타입:.*?\)', '', clean_result)
            clean_result = re.sub(r'\(관련도:.*?\)', '', clean_result)
            clean_result = clean_result.strip()
            
            # 첫 200자만 표시하고 줄바꿈 제거
            preview = clean_result[:200].replace('\n', ' ').strip()
            if len(clean_result) > 200:
                preview += "..."
            
            print(f"유사도 상위1 내용 : {preview}")
        else:
            print(f"유사도 상위1 내용 : 내용 없음")
        print(f"{'-'*40}")

def log_expert_analysis(expert_name: str, status: str = "분석중"):
    """전문가 분석 상태 로그"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    print(f"전문가 LLM {expert_name} {status}")
    print(f"{'-'*40}")

def log_analysis_complete():
    """분석 완료 로그"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    print(f"\n분석 결과 도출")
    print(f"{'='*60}")

def log_step_search_clean(step_name: str, query: str, results: Dict[str, Any], context: str = ""):
    """단계별 검색 모니터링 로그 - 새로운 깔끔한 포맷"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # 검색어에서 핵심 키워드 추출
    if ": " in query:
        actual_keywords = query.split(": ", 1)[1]
        keyword_list = [kw.strip() for kw in actual_keywords.split(",") if kw.strip()]
        query_keywords = ', '.join(keyword_list[:5])
    else:
        query_words = [word for word in query.split() if len(word) > 2][:3]
        query_keywords = ', '.join(query_words)
    
    # 유사도 스코어 추출
    max_score = 0.0
    min_score = 0.0
    top_result = ""
    
    if isinstance(results, dict):
        principles_text = results.get('principles', '')
        examples_text = results.get('examples', '')
        
        import re
        all_scores = []
        for text in [principles_text, examples_text]:
            scores = re.findall(r'관련도: (0\.\d+)', text)
            all_scores.extend([float(s) for s in scores])
        
        if all_scores:
            max_score = max(all_scores)
            min_score = min(all_scores)
            
            # 상위 결과 추출 (실제 텍스트 내용 - 더 긴 청크)
            if principles_text:
                # 메타데이터가 아닌 실제 텍스트 내용 찾기
                lines = principles_text.split('\n')
                content_lines = []
                
                for line in lines:
                    # 메타데이터 패턴이 아닌 실제 내용 찾기
                    if not line.strip().startswith('[') and not line.strip().startswith('(') and len(line.strip()) > 5:
                        content_lines.append(line.strip())
                
                if content_lines:
                    # 여러 줄을 합쳐서 더 긴 청크로 만들기 (최대 3줄)
                    top_result = ' '.join(content_lines[:3])
                else:
                    # 실제 내용을 찾지 못한 경우 첫 번째 줄 사용
                    top_result = lines[0] if lines else ""
    
    # 새로운 깔끔한 로그 포맷 사용
    log_rag_search_clean(
        query_keywords, 
        len(results.get('principles', '')) if isinstance(results, dict) else 0,
        len(results.get('examples', '')) if isinstance(results, dict) else 0,
        max_score, min_score, top_result
    )
