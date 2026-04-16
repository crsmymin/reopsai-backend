#!/usr/bin/env python3
"""
디버깅 및 모니터링 유틸리티
사용자 요청 추적, 성능 모니터링, 에러 분석을 위한 도구들
"""

import json
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
import traceback
from collections import deque

class RequestTracker:
    """사용자 요청 추적 클래스"""
    
    def __init__(self, max_completed_requests: int = 500, max_performance_logs: int = 200):
        self.active_requests = {}
        # 최근 500개 완료된 요청만 메모리에 유지 (메모리 누수 방지)
        self.completed_requests = deque(maxlen=max_completed_requests)
        # 최근 200개 성능 이슈만 메모리에 유지
        self.performance_logs = deque(maxlen=max_performance_logs)
    
    def start_request(self, endpoint: str, user_info: Dict[str, Any] = None) -> str:
        """요청 시작 추적"""
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        self.active_requests[request_id] = {
            "request_id": request_id,
            "endpoint": endpoint,
            "start_time": start_time,
            "user_info": user_info or {},
            "status": "STARTED",
            "steps": []
        }
        
        print(f"\n🚀 [{datetime.now().strftime('%H:%M:%S')}] 요청 시작: {request_id}")
        print(f"   📍 엔드포인트: {endpoint}")
        if user_info:
            print(f"   👤 사용자: {user_info.get('user_id', 'Unknown')}")
        
        return request_id
    
    def add_step(self, request_id: str, step_name: str, details: Dict[str, Any] = None):
        """요청 단계 추가"""
        if request_id in self.active_requests:
            step_time = time.time()
            step_info = {
                "step_name": step_name,
                "timestamp": step_time,
                "details": details or {}
            }
            self.active_requests[request_id]["steps"].append(step_info)
            
            print(f"   ⚡ [{datetime.now().strftime('%H:%M:%S')}] 단계: {step_name}")
            if details:
                print(f"      📋 세부사항: {json.dumps(details, ensure_ascii=False)[:200]}...")
    
    def complete_request(self, request_id: str, success: bool = True, error: Exception = None):
        """요청 완료 추적"""
        if request_id in self.active_requests:
            request_info = self.active_requests.pop(request_id)
            end_time = time.time()
            duration = end_time - request_info["start_time"]
            
            request_info.update({
                "end_time": end_time,
                "duration": duration,
                "success": success,
                "error": str(error) if error else None,
                "status": "COMPLETED" if success else "FAILED"
            })
            
            self.completed_requests.append(request_info)
            
            status_icon = "✅" if success else "❌"
            print(f"\n{status_icon} [{datetime.now().strftime('%H:%M:%S')}] 요청 완료: {request_id}")
            print(f"   ⏱️  소요시간: {duration:.2f}초")
            print(f"   📊 상태: {'성공' if success else '실패'}")
            if error:
                print(f"   💥 에러: {str(error)}")
            
            return request_info
    
    def get_request_stats(self) -> Dict[str, Any]:
        """요청 통계 반환"""
        if not self.completed_requests:
            return {"message": "완료된 요청이 없습니다."}
        
        total_requests = len(self.completed_requests)
        successful_requests = len([r for r in self.completed_requests if r["success"]])
        failed_requests = total_requests - successful_requests
        
        avg_duration = sum(r["duration"] for r in self.completed_requests) / total_requests
        
        # 최근 에러들
        recent_errors = [
            r for r in self.completed_requests[-10:] 
            if not r["success"] and r["error"]
        ]
        
        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "success_rate": (successful_requests / total_requests) * 100,
            "average_duration": avg_duration,
            "recent_errors": recent_errors
        }

# 전역 요청 추적기
request_tracker = RequestTracker()

def track_request(endpoint: str, user_info: Dict[str, Any] = None) -> str:
    """요청 추적 시작"""
    return request_tracker.start_request(endpoint, user_info)

def track_step(request_id: str, step_name: str, details: Dict[str, Any] = None):
    """요청 단계 추적"""
    request_tracker.add_step(request_id, step_name, details)

def complete_track(request_id: str, success: bool = True, error: Exception = None):
    """요청 추적 완료"""
    return request_tracker.complete_request(request_id, success, error)

def get_stats():
    """요청 통계 조회"""
    return request_tracker.get_request_stats()

def log_performance_issue(operation: str, duration: float, threshold: float = 30.0):
    """성능 이슈 로깅"""
    if duration > threshold:
        timestamp = datetime.now()
        performance_log = {
            "timestamp": timestamp,
            "operation": operation,
            "duration": duration,
            "threshold": threshold
        }
        
        # 성능 로그 저장 (최근 200개만 유지)
        request_tracker.performance_logs.append(performance_log)
        
        print(f"\n⚠️  [{timestamp.strftime('%H:%M:%S')}] 성능 이슈 감지")
        print(f"   🔧 작업: {operation}")
        print(f"   ⏱️  소요시간: {duration:.2f}초 (임계값: {threshold}초)")
        print(f"   💡 권장사항: 타임아웃 설정 또는 워커 수 증가 검토")

def analyze_error_patterns():
    """에러 패턴 분석"""
    stats = get_stats()
    if "recent_errors" in stats:
        errors = stats["recent_errors"]
        if errors:
            print(f"\n📊 에러 패턴 분석 (최근 {len(errors)}개 실패 요청)")
            
            # 에러 타입별 분류
            error_types = {}
            for error in errors:
                error_msg = error["error"]
                error_type = error_msg.split(":")[0] if ":" in error_msg else error_msg
                error_types[error_type] = error_types.get(error_type, 0) + 1
            
            print("   🔍 에러 타입별 발생 횟수:")
            for error_type, count in sorted(error_types.items(), key=lambda x: x[1], reverse=True):
                print(f"      - {error_type}: {count}회")
            
            # 가장 많이 실패한 엔드포인트
            endpoints = {}
            for error in errors:
                endpoint = error["endpoint"]
                endpoints[endpoint] = endpoints.get(endpoint, 0) + 1
            
            print("   📍 에러가 많이 발생한 엔드포인트:")
            for endpoint, count in sorted(endpoints.items(), key=lambda x: x[1], reverse=True):
                print(f"      - {endpoint}: {count}회")
