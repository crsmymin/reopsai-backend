#!/usr/bin/env python3
"""
자동 텍스트 파일 감지 및 의미 기반 메타데이터 생성 시스템
텍스트 파일을 자동으로 감지하고 의미 기반 청킹으로 메타데이터를 생성합니다.
"""

import os
import json
import re
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime
import hashlib
# 의미 기반 청킹은 향후 구현 예정
# from semantic_chunker import SemanticChunker, AdvancedSemanticChunker

class AutoMetadataGenerator:
    """자동 메타데이터 생성 클래스"""
    
    def __init__(self, data_directory: str = "./data"):
        self.data_directory = data_directory
        self.manifest_file = "auto_generated_manifest.json"
        
        # 의미 기반 청킹은 향후 구현 예정
        # self.semantic_chunker = AdvancedSemanticChunker()
        print("🤖 메타데이터 생성 시스템 초기화 완료")
        
        # 키워드 기반 자동 태깅 규칙 - 대폭 확장된 버전
        self.keyword_rules = {
            "data_type": {
                "원칙": ["원칙", "가이드라인", "규칙", "지침", "매뉴얼", "principle", "guideline", "방법론", "methodology"],
                "예시": ["예시", "샘플", "케이스", "사례", "example", "sample", "case", "instance", "보기", "실제사례"],
                "템플릿": ["템플릿", "양식", "폼", "template", "form", "틀", "양식서"],
                "체크리스트": ["체크리스트", "체크", "리스트", "checklist", "check", "검토목록", "점검표"],
                "가이드": ["가이드", "안내서", "guide", "매뉴얼", "수행가이드", "실행가이드"],
                "프로세스": ["프로세스", "절차", "process", "절차서", "순서", "단계별"],
                "도구": ["도구", "툴", "tool", "기법", "방법", "기술"],
                "분석보고서": ["분석보고서", "리포트", "report", "분석결과", "결과보고서", "연구보고서"]
            },
            
            # 연구 단계별 태그 (계획서 생성 시 단계별 참조용)
            "research_stage": {
                "planning": ["계획", "기획", "planning", "설계", "준비", "사전준비", "사전계획", "계획수립"],
                "recruitment": ["모집", "리크루팅", "recruitment", "참여자모집", "대상자선정", "스크리닝", "선별"],
                "preparation": ["준비", "사전준비", "preparation", "사전작업", "준비단계", "사전점검"],
                "execution": ["실행", "수행", "execution", "진행", "실시", "조사실행", "인터뷰실행"],
                "analysis": ["분석", "해석", "analysis", "데이터분석", "결과분석", "통계분석", "정성분석"],
                "reporting": ["보고", "리포팅", "reporting", "결과보고", "보고서작성", "결과정리"],
                "follow_up": ["후속조치", "follow-up", "후속작업", "개선방안", "액션플랜", "후속연구"]
            },
            
            # 구체적인 연구 방법론 태그
            "methodology": {
                # 설문 관련
                "survey": ["설문", "서베이", "survey", "questionnaire", "설문조사", "웹설문", "온라인설문"],
                "screener_survey": ["스크리너", "선별", "screener", "screening", "스크리닝", "참여자선별"],
                "post_test_survey": ["사후설문", "후속설문", "post-test", "완료후설문", "피드백설문"],
                
                # 인터뷰 관련
                "interview": ["인터뷰", "면담", "interview", "in-depth", "심층인터뷰", "개별인터뷰"],
                "focus_group": ["포커스그룹", "FGI", "focus group", "집단인터뷰", "그룹인터뷰"],
                "guerilla_interview": ["게릴라인터뷰", "guerilla", "현장인터뷰", "즉석인터뷰"],
                "contextual_inquiry": ["맥락적질문", "contextual inquiry", "현장조사", "맥락분석"],
                
                # 테스트 관련
                "usability_test": ["사용성", "UT", "usability", "user test", "사용성테스트", "UT테스트"],
                "a_b_test": ["A/B테스트", "AB테스트", "split test", "대조실험", "비교실험"],
                "tree_test": ["트리테스트", "tree test", "네비게이션테스트", "정보구조테스트"],
                "card_sorting": ["카드소팅", "card sorting", "정렬", "분류테스트", "정보분류"],
                "eye_tracking": ["아이트래킹", "eye tracking", "시선추적", "시선분석"],
                "click_test": ["클릭테스트", "click test", "클릭분석", "클릭패턴"],
                
                # 관찰 관련
                "observation": ["관찰", "observation", "행동관찰", "사용자관찰", "현장관찰"],
                "ethnography": ["민족지학", "ethnography", "참여관찰", "문화분석"],
                "diary_study": ["다이어리스터디", "diary study", "일지연구", "일상기록"],
                
                # 분석 관련
                "heuristic_evaluation": ["휴리스틱평가", "heuristic", "전문가평가", "전문가검토"],
                "competitive_analysis": ["경쟁사분석", "competitive", "벤치마킹", "경쟁분석"],
                "persona_creation": ["페르소나", "persona", "사용자모델", "타겟설정"],
                "journey_mapping": ["저니맵", "journey", "사용자여정", "경험맵"],
                
                # 특수 방법론
                "co_design": ["코디자인", "co-design", "협업디자인", "참여디자인"],
                "stakeholder_interview": ["이해관계자", "stakeholder", "관계자인터뷰", "사업자인터뷰"],
                "expert_interview": ["전문가인터뷰", "expert", "전문가면담", "전문가조사"]
            },
            
            # 산업/도메인별 구체적 태그
            "domain": {
                # 금융 서비스
                "banking": ["은행", "banking", "bank", "뱅킹", "KB", "신한", "우리", "하나", "국민은행"],
                "securities": ["증권", "securities", "주식", "투자", "KB증권", "키움", "대신", "한국투자증권"],
                "insurance": ["보험", "insurance", "생명보험", "손해보험", "삼성생명", "현대해상"],
                "fintech": ["핀테크", "fintech", "금융기술", "토스", "카카오뱅크", "네이버페이", "페이코"],
                "payment": ["결제", "payment", "송금", "이체", "페이", "모바일결제", "간편결제"],
                
                # 전자상거래
                "ecommerce": ["전자상거래", "이커머스", "쇼핑", "ecommerce", "쿠팡", "11번가", "G마켓"],
                "marketplace": ["마켓플레이스", "marketplace", "오픈마켓", "온라인몰", "쇼핑몰"],
                "food_delivery": ["배달", "food delivery", "배달앱", "배민", "요기요", "쿠팡이츠"],
                
                # 헬스케어
                "healthcare": ["헬스케어", "의료", "건강", "healthcare", "medical", "health"],
                "telemedicine": ["원격진료", "telemedicine", "온라인진료", "비대면진료"],
                "fitness": ["피트니스", "fitness", "운동", "헬스", "헬스장", "요가"],
                
                # 교육
                "education": ["교육", "학습", "education", "learning", "에듀테크", "온라인교육"],
                "elearning": ["이러닝", "e-learning", "온라인학습", "원격교육", "MOOC"],
                
                # 게임/엔터테인먼트
                "gaming": ["게임", "게이밍", "game", "gaming", "모바일게임", "온라인게임"],
                "streaming": ["스트리밍", "streaming", "VOD", "영상", "동영상", "넷플릭스", "왓챠"],
                
                # 부동산
                "real_estate": ["부동산", "real estate", "임대", "매매", "직방", "다방"],
                
                # 교통/모빌리티
                "mobility": ["모빌리티", "mobility", "교통", "택시", "카카오택시", "우버", "카셰어링"],
                
                # 여행
                "travel": ["여행", "travel", "관광", "숙박", "야놀자", "여기어때", "부킹닷컴"],
                
                # 뉴스/미디어
                "media": ["미디어", "media", "뉴스", "news", "네이버뉴스", "다음뉴스"],
                
                # 소셜/커뮤니티
                "social": ["소셜", "social", "커뮤니티", "community", "카카오톡", "인스타그램", "페이스북"]
            },
            
            # 사용자 특성별 태그
            "user_characteristics": {
                "age_group": ["연령", "age", "20대", "30대", "40대", "50대", "60대", "청년", "중년", "고령"],
                "gender": ["성별", "gender", "남성", "여성", "male", "female"],
                "tech_savviness": ["기술수용성", "tech", "디지털원住民", "디지털이민자", "tech-savvy", "non-tech"],
                "income_level": ["소득", "income", "고소득", "중소득", "저소득", "소득수준"],
                "lifestyle": ["라이프스타일", "lifestyle", "직장인", "주부", "학생", "은퇴자", "프리랜서"],
                "usage_frequency": ["사용빈도", "frequency", "자주사용", "가끔사용", "거의안함", "heavy user", "light user"]
            },
            
            # 연구 목적별 태그
            "research_purpose": {
                "discovery": ["발견", "discovery", "탐색", "exploration", "문제발견", "니즈발견"],
                "validation": ["검증", "validation", "가설검증", "아이디어검증", "concept test"],
                "optimization": ["최적화", "optimization", "개선", "improvement", "효율화"],
                "comparison": ["비교", "comparison", "대조", "A/B", "경쟁사비교"],
                "exploration": ["탐색", "exploration", "심층탐구", "깊이있는조사"],
                "evaluation": ["평가", "evaluation", "성과측정", "효과측정", "만족도측정"]
            },
            
            # 도구/플랫폼별 태그
            "tools_platforms": {
                "online_tools": ["온라인도구", "online", "구글폼", "typeform", "survey monkey", "온라인설문"],
                "interview_tools": ["인터뷰도구", "zoom", "teams", "화상회의", "녹화", "녹음"],
                "analysis_tools": ["분석도구", "excel", "spss", "r", "python", "분석소프트웨어"],
                "design_tools": ["디자인도구", "figma", "sketch", "adobe", "프로토타입", "와이어프레임"],
                "testing_tools": ["테스트도구", "usertesting", "maze", "hotjar", "사용성테스트도구"],
                "recruitment_platforms": ["모집플랫폼", "크라우드웍스", "오픈서베이", "참여자모집"]
            },
            
            # 연구 규모/복잡도별 태그
            "research_scale": {
                "small_scale": ["소규모", "small", "파일럿", "pilot", "예비조사", "간단조사"],
                "medium_scale": ["중규모", "medium", "일반조사", "표준조사"],
                "large_scale": ["대규모", "large", "종합조사", "전국조사", "대형프로젝트"],
                "quick_research": ["빠른조사", "quick", "긴급조사", "급한조사", "짧은조사"],
                "comprehensive": ["종합조사", "comprehensive", "전체조사", "포괄적조사"]
            },
            
            # 데이터 수집 방법별 태그
            "data_collection": {
                "quantitative": ["정량", "quantitative", "통계", "수치", "데이터", "측정"],
                "qualitative": ["정성", "qualitative", "인터뷰", "관찰", "질적", "이해"],
                "mixed_method": ["혼합", "mixed", "정량+정성", "통합방법", "다각도"],
                "behavioral": ["행동", "behavioral", "실제행동", "사용패턴", "클릭패턴"],
                "attitudinal": ["태도", "attitudinal", "의견", "인식", "만족도", "선호도"]
            },
            
            # 기존 카테고리들도 확장
            "topics": {
                "설문": ["설문", "서베이", "문항", "질문", "survey", "questionnaire", "웹설문", "온라인설문"],
                "계획서": ["계획서", "계획", "플랜", "plan", "planning", "연구계획", "조사계획"],
                "가이드라인": ["가이드라인", "가이드", "지침", "guideline", "guide", "수행가이드"],
                "인터뷰": ["인터뷰", "면담", "대화", "interview", "심층인터뷰", "개별인터뷰"],
                "테스트": ["테스트", "실험", "검증", "test", "testing", "사용성테스트"],
                "분석": ["분석", "해석", "평가", "analysis", "evaluation", "데이터분석"],
                "UX": ["UX", "사용자경험", "사용자", "user", "experience", "사용자경험디자인"],
                "UI": ["UI", "인터페이스", "interface", "design", "사용자인터페이스"],
                "리서치": ["리서치", "조사", "연구", "research", "study", "사용자리서치"],
                "핀테크": ["핀테크", "금융", "fintech", "financial", "금융서비스"],
                "금융": ["금융", "은행", "증권", "보험", "financial", "bank", "securities", "금융기관"],
                "전자상거래": ["전자상거래", "이커머스", "쇼핑", "ecommerce", "shopping", "온라인쇼핑"],
                "헬스케어": ["헬스케어", "의료", "건강", "healthcare", "medical", "health", "의료서비스"],
                "교육": ["교육", "학습", "education", "learning", "에듀테크", "온라인교육"],
                "게임": ["게임", "게이밍", "game", "gaming", "모바일게임"],
                "부동산": ["부동산", "real estate", "임대", "매매", "부동산서비스"],
                "교통": ["교통", "모빌리티", "mobility", "택시", "교통서비스"],
                "여행": ["여행", "travel", "관광", "숙박", "여행서비스"]
            },
            
            "difficulty_level": {
                "beginner": ["기초", "초급", "입문", "basic", "beginner", "intro", "처음", "신규"],
                "intermediate": ["중급", "중간", "intermediate", "medium", "보통", "일반적"],
                "advanced": ["고급", "전문", "advanced", "expert", "professional", "숙련", "전문가"],
                "expert": ["전문가", "expert", "마스터", "master", "최고급", "최상급"]
            },
            
            "target_audience": {
                "researcher": ["리서처", "연구자", "researcher", "analyst", "조사자", "UX리서처"],
                "designer": ["디자이너", "designer", "UX", "UI", "UX디자이너", "UI디자이너"],
                "product_manager": ["PM", "프로덕트", "product", "manager", "기획자", "프로덕트매니저"],
                "developer": ["개발자", "developer", "engineer", "programmer", "엔지니어"],
                "marketer": ["마케터", "marketer", "marketing", "마케팅", "마케터"],
                "business": ["사업자", "business", "경영진", "사업부", "비즈니스"],
                "stakeholder": ["이해관계자", "stakeholder", "관계자", "관련자", "참여자"]
            }
        }
    
    def scan_directory(self) -> List[Dict[str, Any]]:
        """디렉토리를 스캔하여 텍스트 파일들을 찾습니다."""
        if not os.path.exists(self.data_directory):
            print(f"데이터 디렉토리 {self.data_directory}가 존재하지 않습니다. 생성합니다.")
            os.makedirs(self.data_directory, exist_ok=True)
        
        text_files = []
        for file_path in Path(self.data_directory).rglob("*.txt"):
            if file_path.is_file():
                text_files.append({
                    "file_path": str(file_path),
                    "file_name": file_path.name,
                    "file_size": file_path.stat().st_size,
                    "modified_time": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                })
        
        print(f"총 {len(text_files)}개의 텍스트 파일을 발견했습니다.")
        return text_files
    
    def analyze_file_content(self, file_path: str) -> Dict[str, Any]:
        """파일 내용을 의미 기반으로 분석하여 메타데이터를 생성합니다."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"파일 읽기 오류 {file_path}: {e}")
            return {}
        
        print(f"   🧠 의미 기반 분석 시작...")
        
        # 의미 기반 청킹 분석
        semantic_analysis = self.analyze_semantic_structure(content)
        
        # 자동 태깅
        auto_tags = self.auto_tag_content(content, file_path)
        refined_tags = self.refine_auto_tags(auto_tags, file_path, content)
        
        # 의미 기반 품질 점수 계산
        quality_score = self.calculate_semantic_quality_score(content, semantic_analysis, refined_tags)
        
        # 우선순위 결정
        priority = self.determine_priority(refined_tags, len(content.split()))
        
        # 의미 기반 청킹 전략 결정
        chunking_strategy = self.determine_semantic_chunking_strategy(content, semantic_analysis)
        
        print(f"   📊 의미 청크 수: {semantic_analysis['semantic_chunks']}")
        print(f"   📊 맥락 점수: {semantic_analysis['avg_context_score']:.3f}")
        print(f"   📊 품질 점수: {quality_score:.2f}")
        
        return {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "chunk_splitter": "SEMANTIC_CHUNKING",  # 의미 기반 청킹 사용
            "chunking_strategy": "semantic_context_aware",
            "min_chunk_size": chunking_strategy["min_size"],
            "max_chunk_size": chunking_strategy["max_size"],
            "semantic_analysis": semantic_analysis,  # 의미 분석 결과 포함
            "base_metadata": {
                "data_type": refined_tags["data_type"],
                "topics": ", ".join(refined_tags["topics"]) if refined_tags["topics"] else "",
                "methodology": ", ".join(refined_tags["methodology"]) if refined_tags["methodology"] else "",
                "primary_method": refined_tags.get("primary_method", ""),
                "domain": refined_tags["domain"],
                "priority": priority,
                "quality_score": quality_score,
                "difficulty_level": refined_tags["difficulty_level"],
                "target_audience": refined_tags["target_audience"],
                "research_stage": ", ".join(refined_tags["research_stage"]) if refined_tags["research_stage"] else "",
                "user_characteristics": ", ".join(refined_tags["user_characteristics"]) if refined_tags["user_characteristics"] else "",
                "research_purpose": ", ".join(refined_tags["research_purpose"]) if refined_tags["research_purpose"] else "",
                "tools_platforms": ", ".join(refined_tags["tools_platforms"]) if refined_tags["tools_platforms"] else "",
                "research_scale": ", ".join(refined_tags["research_scale"]) if refined_tags["research_scale"] else "",
                "data_collection": ", ".join(refined_tags["data_collection"]) if refined_tags["data_collection"] else "",
                "word_count": len(content.split()),
                "char_count": len(content),
                "semantic_properties": {
                    "context_score": semantic_analysis['avg_context_score'],
                    "chunk_diversity": semantic_analysis['chunk_diversity'],
                    "semantic_coherence": semantic_analysis['avg_context_score']
                },
                "auto_generated": True,
                "generated_at": datetime.now().isoformat(),
                "tags": ", ".join(auto_tags["additional_tags"]) if auto_tags["additional_tags"] else "",
                "chunking_method": "semantic_context_aware"
            }
        }
    
    def determine_chunking_strategy(self, content: str) -> Dict[str, Any]:
        """내용을 분석하여 최적의 청킹 전략을 결정합니다."""
        # 내용 길이에 따른 전략 결정
        if len(content) < 1000:
            return {
                "strategy": "simple",
                "splitter": "\n\n",
                "min_size": 50,
                "max_size": 500
            }
        elif len(content) < 5000:
            return {
                "strategy": "semantic",
                "splitter": "[CHUNK_DIVIDER]",
                "min_size": 100,
                "max_size": 800
            }
        else:
            return {
                "strategy": "semantic",
                "splitter": "[CHUNK_DIVIDER]",
                "min_size": 150,
                "max_size": 1000
            }
    
    def auto_tag_content(self, content: str, file_path: str) -> Dict[str, Any]:
        """내용과 파일명을 분석하여 자동으로 태그를 생성합니다."""
        # 파일명과 내용을 모두 분석
        analysis_text = f"{os.path.basename(file_path)} {content}".lower()
        
        tags = {
            "data_type": "예시",  # 기본값
            "topics": [],
            "methodology": [],
            "domain": "ux_research",  # 기본값
            "difficulty_level": "intermediate",  # 기본값
            "target_audience": "researcher",  # 기본값
            "research_stage": [],
            "user_characteristics": [],
            "research_purpose": [],
            "tools_platforms": [],
            "research_scale": [],
            "data_collection": [],
            "primary_method": "",
            "additional_tags": []
        }
        
        # 각 카테고리별로 키워드 매칭
        for category, rules in self.keyword_rules.items():
            if category == "data_type":
                for tag, keywords in rules.items():
                    if any(keyword in analysis_text for keyword in keywords):
                        tags[category] = tag
                        break
            elif category in ["topics", "methodology", "domain", "difficulty_level", "target_audience"]:
                matched_tags = []
                for tag, keywords in rules.items():
                    if any(keyword in analysis_text for keyword in keywords):
                        matched_tags.append(tag)
                
                if category == "topics":
                    tags[category] = matched_tags[:8]  # 최대 8개로 확장
                elif category == "methodology":
                    tags[category] = matched_tags[:5]  # 최대 5개로 확장
                else:
                    tags[category] = matched_tags[0] if matched_tags else tags[category]
            
            # 새로운 카테고리들 처리
            elif category in ["research_stage", "user_characteristics", "research_purpose", 
                            "tools_platforms", "research_scale", "data_collection"]:
                matched_tags = []
                for tag, keywords in rules.items():
                    if any(keyword in analysis_text for keyword in keywords):
                        matched_tags.append(tag)
                tags[category] = matched_tags[:3]  # 최대 3개씩
        
        # 추가 태그 생성 - 더 구체적이고 뾰족한 태그들
        additional_tags = []
        
        # 기업/서비스별 태그
        if "KB" in content or "kb" in content.lower():
            additional_tags.append("KB증권")
        if "SOL" in content or "sol" in content.lower():
            additional_tags.append("SOL페이")
        if "토스" in content:
            additional_tags.append("토스")
        if "카카오뱅크" in content:
            additional_tags.append("카카오뱅크")
        if "네이버페이" in content:
            additional_tags.append("네이버페이")
        if "쿠팡" in content:
            additional_tags.append("쿠팡")
        if "배민" in content or "배달의민족" in content:
            additional_tags.append("배달의민족")
        if "11번가" in content:
            additional_tags.append("11번가")
        if "G마켓" in content:
            additional_tags.append("G마켓")
        if "야놀자" in content:
            additional_tags.append("야놀자")
        if "여기어때" in content:
            additional_tags.append("여기어때")
        if "직방" in content:
            additional_tags.append("직방")
        if "다방" in content:
            additional_tags.append("다방")
        
        # 기능별 태그
        if "송금" in content:
            additional_tags.append("송금서비스")
        if "결제" in content:
            additional_tags.append("결제서비스")
        if "대출" in content:
            additional_tags.append("대출서비스")
        if "투자" in content:
            additional_tags.append("투자서비스")
        if "보험" in content:
            additional_tags.append("보험서비스")
        if "예금" in content or "적금" in content:
            additional_tags.append("예적금서비스")
        if "카드" in content:
            additional_tags.append("카드서비스")
        if "포인트" in content:
            additional_tags.append("포인트서비스")
        if "적립" in content:
            additional_tags.append("적립서비스")
        
        # 플랫폼별 태그
        if "모바일" in content or "앱" in content:
            additional_tags.append("모바일")
        if "웹" in content or "웹사이트" in content:
            additional_tags.append("웹")
        if "온라인" in content:
            additional_tags.append("온라인")
        if "오프라인" in content:
            additional_tags.append("오프라인")
        if "키오스크" in content:
            additional_tags.append("키오스크")
        if "ATM" in content:
            additional_tags.append("ATM")
        
        # 사용자 세그먼트별 태그
        if "신규" in content or "신규고객" in content:
            additional_tags.append("신규고객")
        if "기존" in content or "기존고객" in content:
            additional_tags.append("기존고객")
        if "VIP" in content or "프리미엄" in content:
            additional_tags.append("VIP고객")
        if "20대" in content:
            additional_tags.append("20대")
        if "30대" in content:
            additional_tags.append("30대")
        if "40대" in content:
            additional_tags.append("40대")
        if "50대" in content:
            additional_tags.append("50대")
        if "60대" in content:
            additional_tags.append("60대")
        if "직장인" in content:
            additional_tags.append("직장인")
        if "주부" in content:
            additional_tags.append("주부")
        if "학생" in content:
            additional_tags.append("학생")
        
        # 연구 방법별 세부 태그
        if "원격" in content or "비대면" in content:
            additional_tags.append("원격연구")
        if "대면" in content or "오프라인" in content:
            additional_tags.append("대면연구")
        if "실시간" in content:
            additional_tags.append("실시간연구")
        if "비동기" in content:
            additional_tags.append("비동기연구")
        if "화상" in content or "zoom" in content:
            additional_tags.append("화상연구")
        if "전화" in content:
            additional_tags.append("전화연구")
        if "현장" in content:
            additional_tags.append("현장연구")
        
        # 연구 규모별 태그
        if "파일럿" in content or "pilot" in content.lower():
            additional_tags.append("파일럿연구")
        if "소규모" in content:
            additional_tags.append("소규모연구")
        if "대규모" in content:
            additional_tags.append("대규모연구")
        if "빠른" in content or "quick" in content.lower():
            additional_tags.append("빠른연구")
        
        # 데이터 유형별 태그
        if "정량" in content or "quantitative" in content.lower():
            additional_tags.append("정량연구")
        if "정성" in content or "qualitative" in content.lower():
            additional_tags.append("정성연구")
        if "혼합" in content or "mixed" in content.lower():
            additional_tags.append("혼합연구")
        if "행동" in content or "behavioral" in content.lower():
            additional_tags.append("행동연구")
        if "태도" in content or "attitudinal" in content.lower():
            additional_tags.append("태도연구")
        
        # 도구별 태그
        if "구글폼" in content:
            additional_tags.append("구글폼")
        if "typeform" in content.lower():
            additional_tags.append("Typeform")
        if "excel" in content.lower():
            additional_tags.append("Excel")
        if "spss" in content.lower():
            additional_tags.append("SPSS")
        if "figma" in content.lower():
            additional_tags.append("Figma")
        if "sketch" in content.lower():
            additional_tags.append("Sketch")
        
        tags["additional_tags"] = additional_tags
        
        return tags
    
    def refine_auto_tags(self, tags: Dict[str, Any], file_path: str, content: str) -> Dict[str, Any]:
        """파일 경로/내용 기반으로 태그를 보정합니다."""
        refined = {k: v[:] if isinstance(v, list) else v for k, v in tags.items()}
        file_lower = os.path.basename(file_path).lower()
        path_lower = file_path.lower()
        path_obj = Path(file_path).resolve()
        data_root = Path(self.data_directory).resolve()

        def normalize_folder_name(name: str) -> str:
            cleaned = re.sub(r'[^0-9a-zA-Z가-힣]+', ' ', name).strip()
            if not cleaned:
                return ""
            return cleaned.lower().replace(" ", "_")

        def ensure_list(value):
            if isinstance(value, list):
                return value
            if not value:
                return []
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            return [str(value)]

        topics = ensure_list(refined.get("topics", []))
        methodology = ensure_list(refined.get("methodology", []))
        additional_tags = ensure_list(refined.get("additional_tags", []))

        # 경로 기반 태깅 - 상위 폴더 정보를 토픽 및 태그로 추가
        try:
            relative_parts = path_obj.relative_to(data_root).parts
        except ValueError:
            relative_parts = path_obj.parts

        if relative_parts:
            # 마지막 요소는 파일명 => 제외
            folder_parts = [normalize_folder_name(part) for part in relative_parts[:-1]]
            folder_parts = [part for part in folder_parts if part]

            if folder_parts:
                # 최상위 카테고리를 토픽 최상단에 배치 (예: examples, principles)
                top_category = folder_parts[0]
                if top_category and top_category not in topics:
                    topics.insert(0, top_category)

                # 하위 폴더도 토픽/태그에 추가하여 도메인/세부 카테고리 활용
                for sub_category in folder_parts[1:]:
                    if sub_category and sub_category not in topics:
                        topics.append(sub_category)

                # 전체 경로 태그 저장 (예: examples/finance)
                category_path = "/".join(folder_parts)
                if category_path and category_path not in additional_tags:
                    additional_tags.append(f"path:{category_path}")

                # 도메인이 아직 기본값이라면 하위 폴더를 도메인으로 사용
                if refined.get("domain") in [None, "", "ux_research"] and len(folder_parts) > 1:
                    refined["domain"] = folder_parts[-1]

        # 1. data_type을 파일 위치 기반으로 강제 보정
        if "/principles/" in path_lower or "/원칙/" in path_lower:
            refined["data_type"] = "원칙"
        elif "/examples/" in path_lower or "/예시/" in path_lower:
            refined["data_type"] = "예시"
        elif "/templates/" in path_lower or "/템플릿/" in path_lower or "template" in file_lower:
            refined["data_type"] = "템플릿"

        # 2. 파일명 기반 주요 방법론/토픽 보정
        content_lower = content.lower()
        diary_detected = "diary" in file_lower or "다이어리" in content
        survey_detected = "survey" in file_lower or "설문" in content
        interview_detected = "interview" in file_lower or "인터뷰" in content
        usability_detected = (
            "usability" in file_lower or "사용성" in content or "테스트" in content_lower
        )

        method_priority = {
            "survey": 4,
            "interview": 3,
            "usability_test": 2,
            "diary_study": 1,
        }

        current_priority = method_priority.get(str(refined.get("primary_method") or "").lower(), 0)

        def set_primary(method: str, force: bool = False) -> None:
            nonlocal current_priority
            priority = method_priority.get(method, 0)
            if force:
                priority += 100
            if priority >= current_priority:
                refined["primary_method"] = method
                current_priority = priority

        def apply_method_tags(method: str, force: bool = False) -> None:
            nonlocal topics, methodology
            if method == "diary_study":
                topics = [t for t in topics if t not in ["설문", "survey"]]
                for t in ["다이어리", "일지"]:
                    if t not in topics:
                        topics.insert(0, t)
                methodology = [m for m in methodology if m not in ["survey", "screener_survey"]]
                if "diary_study" not in methodology:
                    methodology.insert(0, "diary_study")
            elif method == "survey":
                topics = [t for t in topics if t not in ["다이어리", "일지"]]
                if "설문" not in topics:
                    topics.insert(0, "설문")
                if "survey" not in methodology:
                    methodology.insert(0, "survey")
            elif method == "interview":
                if "인터뷰" not in topics:
                    topics.insert(0, "인터뷰")
                if "interview" not in methodology:
                    methodology.insert(0, "interview")
            elif method == "usability_test":
                if "테스트" not in topics:
                    topics.insert(0, "테스트")
                if "usability_test" not in methodology:
                    methodology.insert(0, "usability_test")

            if method in method_priority:
                set_primary(method, force=force)

        method_alias_from_path = {
            "설문": "survey",
            "survey": "survey",
            "인터뷰": "interview",
            "interview": "interview",
            "fgi": "interview",
            "focus_group": "interview",
            "사용성테스트": "usability_test",
            "usability_test": "usability_test",
            "사용성": "usability_test",
            "ut": "usability_test",
            "다이어리": "diary_study",
            "일지": "diary_study",
            "diary": "diary_study",
            "diary_study": "diary_study",
        }

        method_from_path = None
        for part in reversed(folder_parts):
            if part in method_alias_from_path:
                method_from_path = method_alias_from_path[part]
                break

        if not method_from_path:
            file_tokens = re.split(r"[^a-zA-Z0-9가-힣]+", file_lower)
            for token in file_tokens:
                if token in method_alias_from_path:
                    method_from_path = method_alias_from_path[token]
                    break

        if method_from_path:
            apply_method_tags(method_from_path, force=True)

        if diary_detected:
            apply_method_tags("diary_study")

        if survey_detected:
            apply_method_tags("survey")

        if interview_detected:
            apply_method_tags("interview")

        if usability_detected:
            apply_method_tags("usability_test")

        # 3. topics, methodology 중복 제거 및 정렬 유지
        def dedupe(seq):
            seen = set()
            deduped = []
            for item in seq:
                if not item:
                    continue
                key = item.lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(item)
            return deduped

        refined["topics"] = dedupe(topics)
        refined["methodology"] = dedupe(methodology)

        # 4. 연구 목적/단계 등은 기본 태그 사용
        refined["research_stage"] = ensure_list(refined.get("research_stage", []))
        refined["user_characteristics"] = ensure_list(refined.get("user_characteristics", []))
        refined["research_purpose"] = ensure_list(refined.get("research_purpose", []))
        refined["tools_platforms"] = ensure_list(refined.get("tools_platforms", []))
        refined["research_scale"] = ensure_list(refined.get("research_scale", []))
        refined["data_collection"] = ensure_list(refined.get("data_collection", []))
        refined["additional_tags"] = additional_tags

        # 5. domain 보정 (파일명 기반)
        if "fintech" in path_lower or "핀테크" in content:
            refined["domain"] = "fintech"
        elif "bank" in path_lower or "은행" in content:
            refined["domain"] = "banking"
        elif "insurance" in path_lower or "보험" in content:
            refined["domain"] = "insurance"

        # difficulty/target defaults 유지
        refined["difficulty_level"] = refined.get("difficulty_level", "intermediate")
        refined["target_audience"] = refined.get("target_audience", "researcher")

        return refined
    
    def analyze_semantic_structure(self, content: str) -> Dict[str, Any]:
        """텍스트의 의미적 구조를 분석합니다. (현재는 기본 분석)"""
        # 기본적인 의미 분석 (향후 고도화 예정)
        sentences = content.split('.')
        semantic_chunks = max(1, len(sentences) // 3)  # 문장 수에 따른 청크 수 추정
        
        # 기본 맥락 점수 계산 (문장 길이와 구조 기반)
        avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences) if sentences else 0
        context_score = min(avg_sentence_length / 20, 1.0)  # 문장당 평균 단어 수 기반
        
        return {
            'semantic_chunks': semantic_chunks,
            'avg_context_score': context_score,
            'chunk_diversity': 0.5,  # 기본값
            'context_groups': []
        }
    
    def calculate_semantic_quality_score(self, content: str, semantic_analysis: Dict, tags: Dict[str, Any]) -> float:
        """의미 기반 품질 점수를 계산합니다."""
        score = 0.0
        
        # 1. 길이 점수 (적절한 길이일수록 높은 점수)
        length_score = min(len(content) / 2000, 1.0)  # 2000자 기준
        score += length_score * 0.15
        
        # 2. 의미적 일관성 점수 (맥락 점수 기반)
        context_score = semantic_analysis['avg_context_score']
        score += context_score * 0.35  # 가장 중요한 요소
        
        # 3. 구조 점수 (제목, 목록 등이 있으면 높은 점수)
        structure_indicators = ['##', '###', '-', '•', '1.', '2.', '3.', '*']
        structure_score = sum(1 for indicator in structure_indicators if indicator in content) / 10
        score += min(structure_score, 1.0) * 0.2
        
        # 4. 전문성 점수 (전문 용어가 있으면 높은 점수)
        professional_terms = ['사용자', '경험', 'UX', 'UI', '리서치', '조사', '설문', '인터뷰', '테스트', '분석']
        professional_score = sum(1 for term in professional_terms if term in content) / 15
        score += min(professional_score, 1.0) * 0.2
        
        # 5. 의미적 다양성 점수
        diversity_score = min(semantic_analysis['chunk_diversity'] * 2, 1.0)  # 다양성을 점수로 변환
        score += diversity_score * 0.1
        
        return min(score, 1.0)
    
    def determine_semantic_chunking_strategy(self, content: str, semantic_analysis: Dict) -> Dict[str, Any]:
        """의미 분석 결과를 바탕으로 청킹 전략을 결정합니다."""
        semantic_chunks = semantic_analysis['semantic_chunks']
        avg_context_score = semantic_analysis['avg_context_score']
        
        # 의미적 특성에 따른 청킹 전략 조정
        if avg_context_score > 0.8:
            # 높은 맥락 점수: 더 큰 청크 허용
            return {
                "min_size": 150,
                "max_size": 1000,
                "strategy": "semantic_large_chunks"
            }
        elif avg_context_score > 0.6:
            # 중간 맥락 점수: 표준 청크
            return {
                "min_size": 100,
                "max_size": 800,
                "strategy": "semantic_standard_chunks"
            }
        else:
            # 낮은 맥락 점수: 작은 청크로 세분화
            return {
                "min_size": 50,
                "max_size": 500,
                "strategy": "semantic_small_chunks"
            }
    
    def calculate_quality_score(self, content: str, tags: Dict[str, Any]) -> float:
        """내용 품질 점수를 계산합니다."""
        score = 0.0
        
        # 길이 점수 (적절한 길이일수록 높은 점수)
        length_score = min(len(content) / 2000, 1.0)  # 2000자 기준
        score += length_score * 0.2
        
        # 구조 점수 (제목, 목록 등이 있으면 높은 점수)
        structure_indicators = ['##', '###', '-', '•', '1.', '2.', '3.', '*']
        structure_score = sum(1 for indicator in structure_indicators if indicator in content) / 10
        score += min(structure_score, 1.0) * 0.3
        
        # 전문성 점수 (전문 용어가 있으면 높은 점수)
        professional_terms = ['사용자', '경험', 'UX', 'UI', '리서치', '조사', '설문', '인터뷰', '테스트', '분석']
        professional_score = sum(1 for term in professional_terms if term in content) / 15
        score += min(professional_score, 1.0) * 0.3
        
        # 태그 다양성 점수
        tag_diversity = len(tags["topics"]) + len(tags["methodology"])
        diversity_score = min(tag_diversity / 8, 1.0)
        score += diversity_score * 0.2
        
        return min(score, 1.0)
    
    def determine_priority(self, tags: Dict[str, Any], word_count: int) -> str:
        """우선순위를 결정합니다."""
        # 원칙 파일은 높은 우선순위
        if tags["data_type"] == "원칙":
            return "high"
        
        # 품질이 높고 전문적인 내용은 중간 우선순위
        if word_count > 1000 and len(tags["topics"]) > 2:
            return "medium"
        
        # 나머지는 낮은 우선순위
        return "low"
    
    def generate_manifest(self) -> List[Dict[str, Any]]:
        """자동으로 매니페스트를 생성합니다."""
        print("=" * 60)
        print("자동 매니페스트 생성을 시작합니다...")
        print("=" * 60)
        
        # 디렉토리 스캔
        text_files = self.scan_directory()
        
        if not text_files:
            print("텍스트 파일이 없습니다. data/ 디렉토리에 .txt 파일을 추가하세요.")
            return []
        
        manifest = []
        
        for file_info in text_files:
            print(f"\n📄 분석 중: {file_info['file_name']}")
            
            # 파일 내용 분석
            analysis = self.analyze_file_content(file_info['file_path'])
            
            if analysis:
                manifest.append(analysis)
                
                # 분석 결과 출력
                metadata = analysis["base_metadata"]
                print(f"   📊 데이터 타입: {metadata['data_type']}")
                print(f"   🏷️  주제: {', '.join(metadata['topics']) if metadata['topics'] else '없음'}")
                print(f"   🔬 방법론: {', '.join(metadata['methodology']) if metadata['methodology'] else '없음'}")
                print(f"   🌐 도메인: {metadata['domain']}")
                print(f"   📋 연구단계: {', '.join(metadata['research_stage']) if metadata['research_stage'] else '없음'}")
                print(f"   👥 사용자특성: {', '.join(metadata['user_characteristics']) if metadata['user_characteristics'] else '없음'}")
                print(f"   🎯 연구목적: {', '.join(metadata['research_purpose']) if metadata['research_purpose'] else '없음'}")
                print(f"   🛠️  도구/플랫폼: {', '.join(metadata['tools_platforms']) if metadata['tools_platforms'] else '없음'}")
                print(f"   📏 연구규모: {', '.join(metadata['research_scale']) if metadata['research_scale'] else '없음'}")
                print(f"   📊 데이터수집: {', '.join(metadata['data_collection']) if metadata['data_collection'] else '없음'}")
                print(f"   🏷️  추가태그: {', '.join(metadata['tags']) if metadata['tags'] else '없음'}")
                print(f"   ⭐ 품질 점수: {metadata['quality_score']:.2f}")
                print(f"   📈 우선순위: {metadata['priority']}")
        
        # 매니페스트 저장
        with open(self.manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ 매니페스트 생성 완료: {self.manifest_file}")
        print(f"   총 {len(manifest)}개 파일 처리됨")
        
        return manifest
    
    def create_sample_structure(self):
        """샘플 디렉토리 구조를 생성합니다."""
        sample_structure = {
            "data": {
                "principles": {
                    "survey_principles.txt": "# 설문조사 설계 원칙\n\n## 기본 원칙\n- 명확한 질문 작성\n- 편향 방지\n\n[CHUNK_DIVIDER]\n\n## 고급 원칙\n- 통계적 유의성\n- 표본 크기 결정",
                    "interview_principles.txt": "# 인터뷰 가이드라인\n\n## 준비 단계\n- 질문 순서 설계\n- 시간 관리\n\n[CHUNK_DIVIDER]\n\n## 실행 단계\n- 라포 형성\n- 깊이 있는 질문"
                },
                "examples": {
                    "fintech_survey_example.txt": "# 핀테크 설문 예시\n\n## 개인정보 수집\n귀하의 연령대는?\n1) 20대 2) 30대 3) 40대 4) 50대 이상\n\n[CHUNK_DIVIDER]\n\n## 서비스 이용 경험\n핀테크 서비스를 얼마나 자주 이용하시나요?\n1) 매일 2) 주 2-3회 3) 월 1-2회 4) 거의 안함",
                    "banking_interview_example.txt": "# 은행 서비스 인터뷰 예시\n\n## 웜업 질문\n안녕하세요. 오늘 시간 내주셔서 감사합니다.\n\n[CHUNK_DIVIDER]\n\n## 핵심 질문\n은행 앱을 사용할 때 가장 불편했던 점은 무엇인가요?"
                },
                "templates": {
                    "survey_template.txt": "# 설문 템플릿\n\n## 기본 구조\n1. 인사말\n2. 개인정보 수집\n3. 핵심 질문\n4. 마무리\n\n[CHUNK_DIVIDER]\n\n## 주의사항\n- 개인정보 보호\n- 응답 시간 고려"
                }
            }
        }
        
        def create_directory_structure(base_path: str, structure: dict):
            for name, content in structure.items():
                path = os.path.join(base_path, name)
                if isinstance(content, dict):
                    os.makedirs(path, exist_ok=True)
                    create_directory_structure(path, content)
                else:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(content)
        
        create_directory_structure(".", sample_structure)
        print("샘플 디렉토리 구조가 생성되었습니다.")

def main():
    """메인 실행 함수"""
    print("🤖 자동 텍스트 파일 감지 및 메타데이터 생성 시스템")
    print("=" * 60)
    
    generator = AutoMetadataGenerator()
    
    # 샘플 구조 생성 여부 확인
    if not os.path.exists("data"):
        response = input("data/ 디렉토리가 없습니다. 샘플 구조를 생성하시겠습니까? (y/N): ")
        if response.lower() == 'y':
            generator.create_sample_structure()
            print("\n샘플 파일들을 확인하고 수정한 후 다시 실행하세요.")
            return
    
    # 매니페스트 생성
    manifest = generator.generate_manifest()
    
    if manifest:
        print("\n" + "=" * 60)
        print("✅ 자동 매니페스트 생성 완료!")
        print("=" * 60)
        print("\n다음 단계:")
        print("1. 생성된 매니페스트를 확인하세요.")
        print("2. 필요시 메타데이터를 수정하세요.")
        print("3. python build_improved_database.py를 실행하여 데이터베이스를 구축하세요.")
    else:
        print("\n❌ 매니페스트 생성에 실패했습니다.")
        print("data/ 디렉토리에 .txt 파일을 추가하고 다시 시도하세요.")

if __name__ == "__main__":
    main()
