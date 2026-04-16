import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
import json
import hashlib 
import os
import time
import numpy as np
from typing import List, Dict, Any, Optional

from telemetry import log_rag

class ImprovedVectorDBService:
    def __init__(self, db_path="./chroma_db", collection_name="ux_rag", model_name='jhgan/ko-sbert-nli'):
        """
        개선된 Vector DB 서비스 - RAG 성능 최적화
        """
        print("ImprovedVectorDBService: 초기화 시작...")
        
        # DB 경로 저장
        self.db_path = db_path
        
        # 1. 임베딩 모델 로드
        try:
            self.model = SentenceTransformer(model_name)
            print(f"ImprovedVectorDBService: 임베딩 모델 '{model_name}' 로드 성공.")
        except Exception as e:
            print(f"ImprovedVectorDBService: 치명적 오류! 임베딩 모델 로드 실패: {e}")
            raise
            
        # 2. ChromaDB 클라이언트 연결
        try:
            self.client = chromadb.PersistentClient(
                path=self.db_path,
                settings=Settings(anonymized_telemetry=False)
            )
            print(f"ImprovedVectorDBService: DB 클라이언트 연결 성공. (경로: {db_path})")
        except Exception as e:
            print(f"ImprovedVectorDBService: 치명적 오류! DB 클라이언트 연결 실패: {e}")
            raise

        # 3. Collection 가져오기
        try:
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            print(f"ImprovedVectorDBService: Collection '{collection_name}' 로드 성공.")
        except Exception as e:
            print(f"ImprovedVectorDBService: 치명적 오류! Collection 로드 실패: {e}")
            raise
            
        print("ImprovedVectorDBService: 초기화 완료.")

    def enhanced_search(self, 
                       query_text: str, 
                       n_results: int = 5, 
                       data_type: Optional[str] = None,
                       topics: Optional[List[str]] = None,
                       methodology: Optional[str] = None,
                       min_score: float = 0.45,
                       domain_keywords: Optional[List[str]] = None) -> str:
        """
        개선된 검색 기능 - 다중 필터링 및 점수 기반 필터링
        """
        if not query_text:
            return "참고 자료 없음 (검색어 누락)"
        
        try:
            # 1. 메타데이터 필터 구성 (데이터 타입 위주로 제한)
            where_filter = {"data_type": data_type} if data_type else None
            
            # 2. 벡터 검색 실행 (간단한 쿼리 확장 적용)
            expanded_query = self.query_expansion(query_text)
            query_vector = self.model.encode(expanded_query).tolist()
            domain_keywords_lower = []
            if domain_keywords:
                domain_keywords_lower = [
                    dk.strip().lower() for dk in domain_keywords if isinstance(dk, str) and dk.strip()
                ]
            query_params = {
                "query_embeddings": [query_vector],
                "n_results": min(n_results * 3, 50),  # 더 많이 검색 후 필터링
                "include": ["documents", "metadatas", "distances"]
            }
            
            if where_filter:
                query_params["where"] = where_filter
                
            results = self.collection.query(**query_params)
            
            if not results or not results.get('documents') or not results['documents'][0]:
                return "참고 자료 없음 (검색 결과 없음)"
            
            # 3. 결과 필터링 및 정렬
            filtered_results = []
            documents = results['documents'][0]
            metadatas = results['metadatas'][0]
            distances = results['distances'][0]

            query_keywords = self._extract_query_keywords(query_text)
            
            for i, (doc, metadata, distance) in enumerate(zip(documents, metadatas, distances)):
                # 거리 기반 점수 계산 (0-1 범위로 정규화)
                similarity_score = 1 - distance

                metadata_topics_raw = metadata.get('topics', '')
                if isinstance(metadata_topics_raw, str):
                    metadata_topics_list = [t.strip().lower() for t in metadata_topics_raw.split(',') if t.strip()]
                elif isinstance(metadata_topics_raw, list):
                    metadata_topics_list = [str(t).strip().lower() for t in metadata_topics_raw if str(t).strip()]
                else:
                    metadata_topics_list = []

                metadata_methodology_raw = metadata.get('methodology', '')
                if isinstance(metadata_methodology_raw, str):
                    metadata_methodology_list = [t.strip().lower() for t in metadata_methodology_raw.split(',') if t.strip()]
                elif isinstance(metadata_methodology_raw, list):
                    metadata_methodology_list = [str(t).strip().lower() for t in metadata_methodology_raw if str(t).strip()]
                else:
                    metadata_methodology_list = []

                primary_method = str(metadata.get('primary_method', '') or '').lower()
                
                # 최소 점수 필터링
                if similarity_score < min_score:
                    continue
                
                # methodology 필터링 (포함 여부 확인)
                if methodology and methodology.lower() not in metadata_methodology_list:
                    continue
                
                topic_bonus = 0.0
                topic_penalty = 0.0
                if topics:
                    doc_topics = metadata_topics_list

                    if doc_topics:
                        matches = 0
                        for topic in topics:
                            topic_lower = topic.strip().lower()
                            if any(topic_lower in doc_topic or doc_topic in topic_lower for doc_topic in doc_topics):
                                matches += 1
                        if matches > 0:
                            topic_bonus += 0.04 * matches
                        else:
                            topic_penalty += 0.05
                    else:
                        topic_penalty += 0.05

                domain_bonus = 0.0
                domain_penalty = 0.0
                if domain_keywords_lower:
                    metadata_domain = str(metadata.get('domain', '') or '').lower()
                    metadata_tags_raw = metadata.get('tags', '')
                    if isinstance(metadata_tags_raw, str):
                        metadata_tags = [t.strip().lower() for t in metadata_tags_raw.split(',') if t.strip()]
                    elif isinstance(metadata_tags_raw, list):
                        metadata_tags = [str(t).strip().lower() for t in metadata_tags_raw if str(t).strip()]
                    else:
                        metadata_tags = []

                    domain_matched = False
                    if metadata_domain and any(dk in metadata_domain for dk in domain_keywords_lower):
                        domain_matched = True
                    if metadata_tags and any(
                        dk in tag or tag in dk for dk in domain_keywords_lower for tag in metadata_tags
                    ):
                        domain_matched = True

                    if domain_matched:
                        domain_bonus += 0.06
                    else:
                        domain_penalty += 0.04

                if not self._passes_keyword_requirements(query_keywords, metadata_topics_list, metadata_methodology_list, primary_method):
                    continue

                # 쿼리 키워드 기반 보정
                keyword_adjustment = self._score_by_keywords(
                    query_keywords,
                    metadata_topics_list,
                    metadata_methodology_list,
                    primary_method
                )
                
                composite_score = similarity_score + topic_bonus - topic_penalty + domain_bonus - domain_penalty + keyword_adjustment
                composite_score = max(0.0, min(1.0, composite_score))
                
                filtered_results.append({
                    'document': doc,
                    'metadata': metadata,
                    'score': composite_score,
                    'similarity': similarity_score
                })
            
            # 4. 점수 기준 정렬 및 상위 결과 선택
            filtered_results.sort(key=lambda x: x['score'], reverse=True)
            top_results = filtered_results[:n_results]
            
            if not top_results:
                return "참고 자료 없음 (필터링 후 결과 없음)"
            
            # 5. 컨텍스트 구성
            context_snippets = []
            for i, result in enumerate(top_results):
                metadata = result['metadata']
                source = metadata.get('source', '알 수 없음')
                data_type = metadata.get('data_type', 'N/A')
                score = result['score']
                
                snippet = f"[참고 자료 {i+1} (출처: {source}, 타입: {data_type}, 관련도: {score:.2f})]\n{result['document']}\n"
                context_snippets.append(snippet)
            
            return "\n".join(context_snippets)
            
        except Exception as e:
            print(f"ImprovedVectorDBService: 검색 오류 발생: {e}")
            return f"참고 자료 검색 중 오류 발생: {e}"

    def hybrid_search(self, 
                     query_text: str, 
                     principles_n: int = 5,
                     examples_n: int = 3,
                     topics: Optional[List[str]] = None,
                     methodology: Optional[str] = None,
                     domain_keywords: Optional[List[str]] = None) -> Dict[str, str]:
        """
        하이브리드 검색 - 원칙과 예시를 분리하여 검색
        """
        t0 = time.time()
        # 원칙 검색
        principles_context = self.enhanced_search(
            query_text=query_text,
            n_results=principles_n,
            data_type="원칙",
            topics=topics,
            methodology=methodology,
            min_score=0.5  # 기본 검색보다 조금만 높게 유지
        )
        
        # 예시 검색 (examples_n이 0이면 빈 문자열 반환)
        if examples_n > 0:
            examples_context = self.enhanced_search(
                query_text=query_text,
                n_results=examples_n,
                data_type="예시",
                topics=topics,
                methodology=methodology,
                min_score=0.4,  # 예시는 보다 넉넉한 임계값
                domain_keywords=domain_keywords
            )
        else:
            examples_context = "참고 예시 없음 (진단 모드)"
        
        out = {
            "principles": principles_context,
            "examples": examples_context
        }
        duration = time.time() - t0
        log_rag(
            f"hybrid_search p={principles_n} e={examples_n}",
            query_text,
            out,
            duration_s=duration,
        )
        return out

    def _extract_query_keywords(self, query_text: str) -> List[str]:
        """쿼리에서 주요 키워드를 추출하여 검색 점수 보정에 활용"""
        keyword_map = {
            "survey": ["설문", "survey", "서베이", "questionnaire"],
            "plan": ["계획서", "플랜", "plan", "proposal"],
            "diary": ["다이어리", "diary", "일지"],
            "interview": ["인터뷰", "interview", "면담", "fgi", "focus group"],
            "usability_test": ["사용성", "usability", "테스트", "ut"]
        }
        text_lower = query_text.lower()
        detected = []
        for key, variants in keyword_map.items():
            if any(variant in text_lower for variant in variants):
                detected.append(key)
        return detected

    def _score_by_keywords(
        self,
        query_keywords: List[str],
        metadata_topics: List[str],
        metadata_methodology: List[str],
        primary_method: str
    ) -> float:
        if not query_keywords:
            return 0.0

        keyword_configs = {
            "survey": {
                "preferred": ["설문", "survey"],
                "primary": ["survey", "screener_survey"],
                "penalize": ["다이어리", "일지", "diary", "diary_study"],
                "bonus": 0.12,
                "penalty": 0.14,
                "miss_penalty": 0.4
            },
            "plan": {
                "preferred": ["계획서", "플랜", "plan", "guide", "가이드"],
                "primary": [],
                "penalize": ["다이어리", "일지", "diary", "diary_study"],
                "bonus": 0.08,
                "penalty": 0.12,
                "miss_penalty": 0.2
            },
            "diary": {
                "preferred": ["다이어리", "일지", "diary", "diary_study"],
                "primary": ["diary_study"],
                "penalize": ["설문", "survey"],
                "bonus": 0.09,
                "penalty": 0.12,
                "miss_penalty": 0.2
            },
            "interview": {
                "preferred": ["인터뷰", "interview", "면담", "fgi"],
                "primary": ["interview"],
                "penalize": [],
                "bonus": 0.07,
                "penalty": 0.08,
                "miss_penalty": 0.15
            },
            "usability_test": {
                "preferred": ["사용성", "usability", "ut", "테스트", "usability_test"],
                "primary": ["usability_test"],
                "penalize": ["다이어리", "diary", "diary_study"],
                "bonus": 0.09,
                "penalty": 0.12,
                "miss_penalty": 0.25
            }
        }

        adjustment = 0.0
        for keyword in query_keywords:
            config = keyword_configs.get(keyword)
            if not config:
                continue

            preferred = config["preferred"]
            penalize = config["penalize"]
            primary_pref = config.get("primary", [])
            miss_penalty = config.get("miss_penalty", 0.0)

            preferred_match_topics = any(
                pref.lower() in metadata_topics for pref in preferred
            )
            preferred_match_methodology = any(
                pref.lower() in metadata_methodology for pref in preferred
            )
            preferred_match_primary = any(
                pref in primary_method for pref in primary_pref
            )

            has_preferred = preferred_match_topics or preferred_match_methodology or preferred_match_primary

            has_penalty = any(
                pen.lower() in metadata_topics or pen.lower() in metadata_methodology or pen.lower() in primary_method
                for pen in penalize
            )

            bonus = config.get("bonus", 0.05)
            penalty_value = config.get("penalty", 0.05)

            if has_preferred:
                adjustment += bonus
            elif miss_penalty:
                adjustment -= miss_penalty

            if has_penalty:
                adjustment -= penalty_value

        return adjustment

    def _passes_keyword_requirements(
        self,
        query_keywords: List[str],
        metadata_topics: List[str],
        metadata_methodology: List[str],
        primary_method: str
    ) -> bool:
        if not query_keywords:
            return True

        if "survey" in query_keywords:
            if primary_method not in ("survey", "screener_survey"):
                return False
            if not any(term in metadata_topics for term in ["설문", "survey"]):
                if not any(term in metadata_methodology for term in ["survey", "screener_survey"]):
                    return False

        if "diary" in query_keywords:
            if primary_method not in ("diary_study",):
                return False
        if "interview" in query_keywords:
            if primary_method not in ("interview",):
                return False

        return True

    def query_expansion(self, original_query: str) -> str:
        """
        쿼리 확장 - 동의어 및 관련어 추가
        """
        # 간단한 동의어 매핑 (실제로는 더 정교한 방법 사용 가능)
        synonym_map = {
            "설문": ["설문조사", "서베이", "조사", "문항"],
            "계획서": ["계획", "플랜", "리서치 계획"],
            "가이드라인": ["가이드", "지침", "매뉴얼"],
            "사용자": ["사용자", "고객", "소비자", "이용자"],
            "경험": ["경험", "체험", "사용 경험"],
            "문제": ["문제", "이슈", "불편함", "어려움"]
        }
        
        expanded_terms = [original_query]
        
        for term, synonyms in synonym_map.items():
            if term in original_query:
                expanded_terms.extend(synonyms)
        
        return " ".join(expanded_terms)

    def context_optimization(self, context: str, max_length: int = 2000) -> str:
        """
        컨텍스트 최적화 - 길이 제한 및 중요도 기반 선택
        """
        if len(context) <= max_length:
            return context
        
        # 간단한 길이 기반 자르기 (실제로는 더 정교한 방법 사용 가능)
        lines = context.split('\n')
        optimized_lines = []
        current_length = 0
        
        for line in lines:
            if current_length + len(line) <= max_length:
                optimized_lines.append(line)
                current_length += len(line)
            else:
                break
        
        return '\n'.join(optimized_lines)

    def ingest_from_file_manifest(self, manifest_path="file_manifest.json"):
        """
        개선된 데이터 색인 - 더 나은 청킹 및 메타데이터
        """
        print(f"ImprovedVectorDBService: '{manifest_path}' 파일 매니페스트 기반 색인을 시작합니다...")
        
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest_list = json.load(f)
        except Exception as e:
            print(f"오류: '{manifest_path}' 파일을 읽을 수 없습니다. {e}")
            return

        documents, metadatas, ids = [], [], []
        total_chunks = 0

        for item in manifest_list:
            file_path = item.get("file_path")
            splitter = item.get("chunk_splitter")
            base_meta = item.get("base_metadata", {}) 

            if not file_path or not splitter:
                print(f"경고: 항목 {item}에 'file_path' 또는 'chunk_splitter'가 없습니다. 건너뜁니다.")
                continue

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()
            except Exception as e:
                print(f"경고: {file_path} 파일을 읽는 중 오류 발생. 건너뜁니다. {e}")
                continue

            # 개선된 청킹 - 의미 단위로 분할
            chunks = self.smart_chunking(text_content, splitter)
            print(f"-> '{file_path}' 처리 중... {len(chunks)}개 조각 발견.")
            
            for i, chunk in enumerate(chunks):
                if len(chunk.strip()) < 30:  # 최소 길이 증가
                    continue

                doc_id = f"{file_path}_chunk_{i}"
                
                # 향상된 메타데이터
                chunk_meta = base_meta.copy() 
                chunk_meta["source"] = file_path
                chunk_meta["chunk_id"] = i
                chunk_meta["chunk_length"] = len(chunk)
                chunk_meta["word_count"] = len(chunk.split())

                documents.append(chunk)
                metadatas.append(chunk_meta)
                ids.append(doc_id)
                total_chunks += 1
        
        if documents:
            print(f"\n총 {total_chunks}개 항목을 임베딩합니다... (시간 소요)")
            embeddings = self.model.encode(documents).tolist()
            
            print("DB에 데이터를 Add합니다...")
            self.collection.add(
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            print(f"--- 총 {total_chunks}개의 항목이 DB에 성공적으로 추가되었습니다. ---")
        else:
            print("색인할 항목이 없습니다.")

    def smart_chunking(self, text: str, splitter: str) -> List[str]:
        """
        스마트 청킹 - 의미 단위로 텍스트 분할
        """
        chunks = text.split(splitter)
        optimized_chunks = []
        
        for chunk in chunks:
            chunk = chunk.strip()
            if len(chunk) < 30:
                continue
            
            # 너무 긴 청크는 추가로 분할
            if len(chunk) > 1000:
                # 문장 단위로 분할
                sentences = chunk.split('. ')
                current_chunk = ""
                
                for sentence in sentences:
                    if len(current_chunk + sentence) > 800:
                        if current_chunk:
                            optimized_chunks.append(current_chunk.strip())
                        current_chunk = sentence
                    else:
                        current_chunk += sentence + ". "
                
                if current_chunk:
                    optimized_chunks.append(current_chunk.strip())
            else:
                optimized_chunks.append(chunk)
        
        return optimized_chunks

# 기존 VectorDBService와의 호환성을 위한 래퍼
class VectorDBServiceWrapper:
    def __init__(self, *args, **kwargs):
        self.improved_service = ImprovedVectorDBService(*args, **kwargs)
    
    def search(self, query_text, n_results=5, filter_metadata=None, domain_keywords=None):
        """
        기존 인터페이스와 호환되는 검색 메서드
        """
        t0 = time.time()
        if filter_metadata:
            data_type = filter_metadata.get("data_type")
            topics = filter_metadata.get("topics")
            methodology = filter_metadata.get("methodology")
            
            if isinstance(topics, str):
                topics = [topics]
            
            out = self.improved_service.enhanced_search(
                query_text=query_text,
                n_results=n_results,
                data_type=data_type,
                topics=topics,
                methodology=methodology,
                domain_keywords=domain_keywords
            )
            log_rag(f"search n={n_results} type={data_type or 'any'}", query_text, out, duration_s=time.time() - t0)
            return out
        else:
            out = self.improved_service.enhanced_search(
                query_text=query_text,
                n_results=n_results,
                domain_keywords=domain_keywords
            )
            log_rag(f"search n={n_results} type=any", query_text, out, duration_s=time.time() - t0)
            return out
    
    def ingest_from_file_manifest(self, manifest_path="file_manifest.json"):
        return self.improved_service.ingest_from_file_manifest(manifest_path)
