# RAG 데이터베이스 구축 개선 방안

import json
import os
from typing import List, Dict, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

class DataType(Enum):
    PRINCIPLE = "원칙"
    EXAMPLE = "예시"
    TEMPLATE = "템플릿"
    CHECKLIST = "체크리스트"

class Domain(Enum):
    UX_RESEARCH = "ux_research"
    FINANCIAL_SERVICES = "financial_services"
    FINTECH = "fintech"
    E_COMMERCE = "e_commerce"
    HEALTHCARE = "healthcare"

class Methodology(Enum):
    USABILITY_TEST = "usability_test"
    INTERVIEW = "interview"
    SURVEY = "survey"
    SCREENER_SURVEY = "screener_survey"
    FOCUS_GROUP = "focus_group"

@dataclass
class ChunkMetadata:
    """개선된 청크 메타데이터 구조"""
    source_file: str
    chunk_id: int
    data_type: DataType
    topics: List[str]
    methodology: List[Methodology]
    domain: Domain
    priority: str  # high, medium, low
    quality_score: float  # 0.0 - 1.0
    word_count: int
    char_count: int
    last_updated: str
    tags: List[str]
    difficulty_level: str  # beginner, intermediate, advanced
    target_audience: str  # researcher, designer, product_manager

class ImprovedRAGDatabaseBuilder:
    """개선된 RAG 데이터베이스 구축 클래스"""
    
    def __init__(self):
        self.chunks = []
        self.metadata_schema = {}
        
    def build_enhanced_manifest(self) -> List[Dict]:
        """
        향상된 파일 매니페스트 생성
        """
        enhanced_manifest = [
            {
                "file_path": "survey_principles.txt",
                "chunk_splitter": "[CHUNK_DIVIDER]",
                "chunking_strategy": "semantic",  # 의미 단위 청킹
                "min_chunk_size": 100,
                "max_chunk_size": 800,
                "base_metadata": {
                    "data_type": "원칙",
                    "topics": ["설문설계", "공통가이드", "UX리서치"],
                    "methodology": ["survey", "screener_survey"],
                    "domain": "ux_research",
                    "priority": "high",
                    "quality_score": 0.9,
                    "difficulty_level": "intermediate",
                    "target_audience": "researcher"
                }
            },
            {
                "file_path": "research_plan_principles.txt",
                "chunk_splitter": "[CHUNK_DIVIDER]",
                "chunking_strategy": "semantic",
                "min_chunk_size": 150,
                "max_chunk_size": 1000,
                "base_metadata": {
                    "data_type": "원칙",
                    "topics": ["계획서", "원칙", "공통가이드", "리서치방법론"],
                    "methodology": ["usability_test", "interview", "survey"],
                    "domain": "ux_research",
                    "priority": "high",
                    "quality_score": 0.95,
                    "difficulty_level": "advanced",
                    "target_audience": "researcher"
                }
            },
            {
                "file_path": "guideline_principles.txt",
                "chunk_splitter": "[CHUNK_DIVIDER]",
                "chunking_strategy": "semantic",
                "min_chunk_size": 120,
                "max_chunk_size": 900,
                "base_metadata": {
                    "data_type": "원칙",
                    "topics": ["가이드라인", "모더레이션", "UT", "질문설계"],
                    "methodology": ["usability_test", "interview"],
                    "domain": "ux_research",
                    "priority": "high",
                    "quality_score": 0.85,
                    "difficulty_level": "intermediate",
                    "target_audience": "researcher"
                }
            },
            {
                "file_path": "good_surveys.txt",
                "chunk_splitter": "[CHUNK_DIVIDER]",
                "chunking_strategy": "semantic",
                "min_chunk_size": 200,
                "max_chunk_size": 1200,
                "base_metadata": {
                    "data_type": "예시",
                    "topics": ["설문", "스크리너", "금융", "KB증권"],
                    "methodology": ["screener_survey"],
                    "domain": "financial_services",
                    "priority": "medium",
                    "quality_score": 0.8,
                    "difficulty_level": "beginner",
                    "target_audience": "researcher",
                    "sample_size": "large",
                    "success_rate": 0.85
                }
            },
            {
                "file_path": "good_surveys2.txt",
                "chunk_splitter": "[CHUNK_DIVIDER]",
                "chunking_strategy": "semantic",
                "min_chunk_size": 200,
                "max_chunk_size": 1200,
                "base_metadata": {
                    "data_type": "예시",
                    "topics": ["설문", "스크리너", "핀테크", "SOL페이"],
                    "methodology": ["screener_survey"],
                    "domain": "fintech",
                    "priority": "medium",
                    "quality_score": 0.8,
                    "difficulty_level": "beginner",
                    "target_audience": "researcher",
                    "sample_size": "medium",
                    "success_rate": 0.82
                }
            },
            {
                "file_path": "plan_examples.txt",
                "chunk_splitter": "[CHUNK_DIVIDER]",
                "chunking_strategy": "semantic",
                "min_chunk_size": 300,
                "max_chunk_size": 1500,
                "base_metadata": {
                    "data_type": "예시",
                    "topics": ["계획서", "핀테크", "송금", "UT"],
                    "methodology": ["usability_test"],
                    "domain": "fintech",
                    "priority": "medium",
                    "quality_score": 0.85,
                    "difficulty_level": "intermediate",
                    "target_audience": "researcher",
                    "sample_size": "small",
                    "success_rate": 0.88
                }
            }
        ]
        
        return enhanced_manifest
    
    def semantic_chunking(self, text: str, splitter: str, min_size: int = 100, max_size: int = 800) -> List[str]:
        """
        의미 단위 청킹 - 문장과 단락을 고려한 스마트 분할
        """
        # 기본 분할
        basic_chunks = text.split(splitter)
        semantic_chunks = []
        
        for chunk in basic_chunks:
            chunk = chunk.strip()
            if len(chunk) < 30:  # 최소 길이를 30자로 낮춤
                continue
            
            if len(chunk) <= max_size:
                semantic_chunks.append(chunk)
            else:
                # 긴 청크를 의미 단위로 재분할
                sub_chunks = self.split_by_meaning(chunk, max_size)
                semantic_chunks.extend(sub_chunks)
        
        return semantic_chunks
    
    def split_by_meaning(self, text: str, max_size: int) -> List[str]:
        """
        의미 단위로 텍스트 분할
        """
        # 문단 단위로 먼저 분할
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        
        for paragraph in paragraphs:
            if len(current_chunk + paragraph) <= max_size:
                current_chunk += paragraph + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph + "\n\n"
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        # 여전히 긴 청크는 문장 단위로 분할
        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= max_size:
                final_chunks.append(chunk)
            else:
                sentences = chunk.split('. ')
                temp_chunk = ""
                
                for sentence in sentences:
                    if len(temp_chunk + sentence) <= max_size:
                        temp_chunk += sentence + ". "
                    else:
                        if temp_chunk:
                            final_chunks.append(temp_chunk.strip())
                        temp_chunk = sentence + ". "
                
                if temp_chunk:
                    final_chunks.append(temp_chunk.strip())
        
        return final_chunks
    
    def calculate_quality_score(self, chunk: str, metadata: Dict) -> float:
        """
        청크 품질 점수 계산
        """
        score = 0.0
        
        # 길이 점수 (적절한 길이일수록 높은 점수)
        length_score = min(len(chunk) / 500, 1.0)  # 500자 기준
        score += length_score * 0.2
        
        # 구조 점수 (제목, 목록 등이 있으면 높은 점수)
        structure_indicators = ['##', '###', '-', '•', '1.', '2.', '3.']
        structure_score = sum(1 for indicator in structure_indicators if indicator in chunk) / 5
        score += min(structure_score, 1.0) * 0.3
        
        # 전문성 점수 (전문 용어가 있으면 높은 점수)
        professional_terms = ['사용자', '경험', 'UX', 'UI', '리서치', '조사', '설문', '인터뷰', '테스트']
        professional_score = sum(1 for term in professional_terms if term in chunk) / 10
        score += min(professional_score, 1.0) * 0.3
        
        # 메타데이터 기반 점수
        if metadata.get('priority') == 'high':
            score += 0.2
        
        return min(score, 1.0)
    
    def build_database_from_manifest(self, manifest: List[Dict]) -> Dict[str, List]:
        """
        자동 생성된 매니페스트로 의미 기반 데이터베이스 구축
        """
        print("의미 기반 매니페스트로 데이터베이스 구축을 시작합니다...")
        
        all_chunks = []
        all_metadata = []
        all_ids = []
        
        for item in manifest:
            file_path = item["file_path"]
            base_metadata = item["base_metadata"]
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()
            except Exception as e:
                print(f"경고: {file_path} 파일을 읽는 중 오류 발생: {e}")
                continue
            
            # 의미 기반 청킹 사용 여부 확인
            if item.get("chunk_splitter") == "SEMANTIC_CHUNKING":
                print(f"   🧠 의미 기반 청킹 적용: {file_path}")
                chunks = self.semantic_chunking_from_manifest(text_content, item)
            else:
                # 기존 방식 사용
                splitter = item.get("chunk_splitter", "\n\n")
                min_size = item.get("min_chunk_size", 100)
                max_size = item.get("max_chunk_size", 800)
                chunks = self.semantic_chunking(text_content, splitter, min_size, max_size)
            
            for i, chunk in enumerate(chunks):
                # 향상된 메타데이터 생성 (ChromaDB 호환 형식으로 변환)
                chunk_metadata = base_metadata.copy()
                
                # 리스트 타입을 문자열로 변환
                if isinstance(chunk_metadata.get("topics"), list):
                    chunk_metadata["topics"] = ", ".join(chunk_metadata["topics"])
                if isinstance(chunk_metadata.get("tags"), list):
                    chunk_metadata["tags"] = ", ".join(chunk_metadata["tags"])
                if isinstance(chunk_metadata.get("methodology"), list):
                    chunk_metadata["methodology"] = ", ".join(chunk_metadata["methodology"])
                
                # 딕셔너리 타입을 문자열로 변환
                if isinstance(chunk_metadata.get("semantic_properties"), dict):
                    semantic_props = chunk_metadata["semantic_properties"]
                    chunk_metadata["semantic_properties"] = f"context_score:{semantic_props.get('context_score', 0):.3f}, diversity:{semantic_props.get('chunk_diversity', 0):.3f}, coherence:{semantic_props.get('semantic_coherence', 0):.3f}"
                
                chunk_metadata.update({
                    "source": file_path,
                    "chunk_id": i,
                    "chunk_length": len(chunk),
                    "word_count": len(chunk.split()),
                    "quality_score": self.calculate_quality_score(chunk, chunk_metadata),
                    "last_updated": datetime.now().isoformat(),
                    "difficulty_level": base_metadata.get("difficulty_level", "intermediate"),
                    "target_audience": base_metadata.get("target_audience", "researcher")
                })
                
                all_chunks.append(chunk)
                all_metadata.append(chunk_metadata)
                all_ids.append(f"{file_path}_chunk_{i}")
        
        print(f"총 {len(all_chunks)}개의 청크가 생성되었습니다.")
        
        # 품질 필터링 제거 - 모든 청크를 데이터베이스에 저장
        return {
            "chunks": all_chunks,
            "metadata": all_metadata,
            "ids": all_ids
        }
    
    def semantic_chunking_from_manifest(self, text_content: str, manifest_item: Dict) -> List[str]:
        """매니페스트 정보를 바탕으로 의미 기반 청킹 수행"""
        splitter = manifest_item.get("chunk_splitter", "\n\n")
        min_size = manifest_item.get("min_chunk_size", 100)
        max_size = manifest_item.get("max_chunk_size", 800)
        
        # SEMANTIC_CHUNKING인 경우 의미 기반 청킹 사용
        if splitter == "SEMANTIC_CHUNKING":
            return self.semantic_chunking(text_content, "\n\n", min_size, max_size)
        else:
            return self.semantic_chunking(text_content, splitter, min_size, max_size)

    def build_database(self, manifest_path: str = "improved_file_manifest.json"):
        """
        개선된 데이터베이스 구축
        """
        print("개선된 RAG 데이터베이스 구축을 시작합니다...")
        
        # 향상된 매니페스트 사용
        manifest = self.build_enhanced_manifest()
        
        all_chunks = []
        all_metadata = []
        all_ids = []
        
        for item in manifest:
            file_path = item["file_path"]
            splitter = item["chunk_splitter"]
            base_metadata = item["base_metadata"]
            min_size = item.get("min_chunk_size", 100)
            max_size = item.get("max_chunk_size", 800)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()
            except Exception as e:
                print(f"경고: {file_path} 파일을 읽는 중 오류 발생: {e}")
                continue
            
            # 의미 단위 청킹
            chunks = self.semantic_chunking(text_content, splitter, min_size, max_size)
            
            for i, chunk in enumerate(chunks):
                # 향상된 메타데이터 생성
                chunk_metadata = base_metadata.copy()
                chunk_metadata.update({
                    "source": file_path,
                    "chunk_id": i,
                    "chunk_length": len(chunk),
                    "word_count": len(chunk.split()),
                    "quality_score": self.calculate_quality_score(chunk, chunk_metadata),
                    "last_updated": "2024-01-01",  # 실제로는 파일 수정 시간
                    "tags": self.extract_tags(chunk),
                    "difficulty_level": base_metadata.get("difficulty_level", "intermediate"),
                    "target_audience": base_metadata.get("target_audience", "researcher")
                })
                
                all_chunks.append(chunk)
                all_metadata.append(chunk_metadata)
                all_ids.append(f"{file_path}_chunk_{i}")
        
        print(f"총 {len(all_chunks)}개의 고품질 청크가 생성되었습니다.")
        
        # 품질 기반 필터링
        high_quality_chunks = []
        high_quality_metadata = []
        high_quality_ids = []
        
        for chunk, metadata, chunk_id in zip(all_chunks, all_metadata, all_ids):
            if metadata["quality_score"] >= 0.6:  # 품질 임계값
                high_quality_chunks.append(chunk)
                high_quality_metadata.append(metadata)
                high_quality_ids.append(chunk_id)
        
        print(f"품질 필터링 후 {len(high_quality_chunks)}개의 청크가 선택되었습니다.")
        
        return {
            "chunks": high_quality_chunks,
            "metadata": high_quality_metadata,
            "ids": high_quality_ids
        }
    
    def extract_tags(self, text: str) -> List[str]:
        """
        텍스트에서 태그 추출
        """
        tags = []
        
        # 키워드 기반 태그 추출
        keyword_tags = {
            "설문": ["설문", "서베이", "문항", "질문"],
            "인터뷰": ["인터뷰", "면담", "대화"],
            "테스트": ["테스트", "실험", "검증"],
            "분석": ["분석", "해석", "평가"],
            "계획": ["계획", "설계", "기획"]
        }
        
        for tag, keywords in keyword_tags.items():
            if any(keyword in text for keyword in keywords):
                tags.append(tag)
        
        return tags

# 사용 예시
if __name__ == "__main__":
    builder = ImprovedRAGDatabaseBuilder()
    database_data = builder.build_database()
    
    print("데이터베이스 구축 완료!")
    print(f"총 청크 수: {len(database_data['chunks'])}")
    
    # 품질 분포 확인
    quality_scores = [meta["quality_score"] for meta in database_data["metadata"]]
    print(f"평균 품질 점수: {sum(quality_scores) / len(quality_scores):.2f}")
    print(f"최고 품질 점수: {max(quality_scores):.2f}")
    print(f"최저 품질 점수: {min(quality_scores):.2f}")
