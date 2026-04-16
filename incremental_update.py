#!/usr/bin/env python3
"""
증분 업데이트를 위한 스마트 데이터베이스 관리 시스템
새 파일만 추가하고 기존 데이터는 유지합니다.
"""

import os
import json
import hashlib
from datetime import datetime
from pathlib import Path
from rag_system.improved.improved_vector_db_service import VectorDBServiceWrapper
from rag_system.improved.improved_rag_database_builder import ImprovedRAGDatabaseBuilder
from auto_metadata_generator import AutoMetadataGenerator

class IncrementalDatabaseManager:
    """증분 데이터베이스 관리 클래스"""
    
    def __init__(self, db_path="./chroma_db", collection_name="ux_rag"):
        self.db_path = db_path
        self.collection_name = collection_name
        self.manifest_file = "auto_generated_manifest.json"
        self.file_hashes_file = "file_hashes.json"
        
        # 기존 해시 로드
        self.file_hashes = self.load_file_hashes()
        
        # VectorDB 서비스 초기화
        self.vector_service = VectorDBServiceWrapper(
            db_path=db_path,
            collection_name=collection_name
        )
        
        print("🔄 증분 업데이트 시스템 초기화 완료")
    
    def load_file_hashes(self) -> dict:
        """파일 해시 로드"""
        if os.path.exists(self.file_hashes_file):
            with open(self.file_hashes_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def save_file_hashes(self):
        """파일 해시 저장"""
        with open(self.file_hashes_file, 'w', encoding='utf-8') as f:
            json.dump(self.file_hashes, f, ensure_ascii=False, indent=2)
    
    def calculate_file_hash(self, file_path: str) -> str:
        """파일 해시 계산"""
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
                return hashlib.md5(content).hexdigest()
        except Exception as e:
            print(f"파일 해시 계산 오류 {file_path}: {e}")
            return ""
    
    def get_changed_files(self) -> list:
        """변경된 파일 목록 반환"""
        changed_files = []
        
        # data 폴더 스캔
        data_dir = Path("./data")
        if not data_dir.exists():
            print("❌ data 폴더가 존재하지 않습니다.")
            return []
        
        for txt_file in data_dir.rglob("*.txt"):
            file_path = str(txt_file)
            current_hash = self.calculate_file_hash(file_path)
            
            if file_path not in self.file_hashes or self.file_hashes[file_path] != current_hash:
                changed_files.append(file_path)
                print(f"📝 변경된 파일 감지: {file_path}")
        
        return changed_files
    
    def remove_deleted_files_from_db(self):
        """삭제된 파일의 청크들을 데이터베이스에서 제거"""
        data_dir = Path("./data")
        existing_files = set()
        
        # 현재 존재하는 파일들 수집
        for txt_file in data_dir.rglob("*.txt"):
            existing_files.add(str(txt_file))
        
        # 데이터베이스에서 삭제된 파일의 청크들 찾기
        try:
            # 모든 메타데이터 조회
            results = self.vector_service.improved_service.collection.get()
            
            if results and 'metadatas' in results and 'ids' in results:
                ids_to_remove = []
                
                for i, metadata in enumerate(results['metadatas']):
                    if metadata and 'source' in metadata:
                        source_file = metadata['source']
                        if source_file not in existing_files:
                            ids_to_remove.append(results['ids'][i])
                            print(f"🗑️ 삭제된 파일의 청크 제거: {source_file}")
                
                # 삭제 실행
                if ids_to_remove:
                    self.vector_service.improved_service.collection.delete(ids=ids_to_remove)
                    print(f"✅ {len(ids_to_remove)}개의 청크가 제거되었습니다.")
                    
        except Exception as e:
            print(f"⚠️ 삭제된 파일 청크 제거 중 오류: {e}")
    
    def incremental_update(self):
        """증분 업데이트 실행"""
        print("=" * 60)
        print("🔄 증분 업데이트를 시작합니다...")
        print("=" * 60)
        
        # 1. 변경된 파일 감지
        changed_files = self.get_changed_files()
        
        if not changed_files:
            print("✅ 변경된 파일이 없습니다. 업데이트가 필요하지 않습니다.")
            return True
        
        print(f"📝 {len(changed_files)}개의 파일이 변경되었습니다.")
        
        # 2. 삭제된 파일의 청크들 제거
        self.remove_deleted_files_from_db()
        
        # 3. 변경된 파일들만 처리
        generator = AutoMetadataGenerator()
        builder = ImprovedRAGDatabaseBuilder()
        
        for file_path in changed_files:
            print(f"\n📄 처리 중: {file_path}")
            
            try:
                # 파일 분석
                file_metadata = generator.analyze_file_content(file_path)
                if not file_metadata:
                    continue
                
                # 기존 청크들 제거 (같은 파일의 기존 청크들)
                self.remove_file_chunks_from_db(file_path)
                
                # 새 청크들 생성
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 의미 기반 청킹
                if file_metadata.get("chunk_splitter") == "SEMANTIC_CHUNKING":
                    chunks = builder.semantic_chunking_from_manifest(content, file_metadata)
                else:
                    splitter = file_metadata.get("chunk_splitter", "\n\n")
                    min_size = file_metadata.get("min_chunk_size", 100)
                    max_size = file_metadata.get("max_chunk_size", 800)
                    chunks = builder.semantic_chunking(content, splitter, min_size, max_size)
                
                # 메타데이터 생성
                chunk_metadata_list = []
                chunk_ids = []
                
                for i, chunk in enumerate(chunks):
                    chunk_metadata = file_metadata["base_metadata"].copy()
                    chunk_metadata.update({
                        "source": file_path,
                        "chunk_id": i,
                        "chunk_length": len(chunk),
                        "word_count": len(chunk.split()),
                        "quality_score": builder.calculate_quality_score(chunk, chunk_metadata),
                        "last_updated": datetime.now().isoformat(),
                        "tags": builder.extract_tags(chunk),
                        "difficulty_level": file_metadata["base_metadata"].get("difficulty_level", "intermediate"),
                        "target_audience": file_metadata["base_metadata"].get("target_audience", "researcher")
                    })
                    
                    chunk_metadata_list.append(chunk_metadata)
                    chunk_ids.append(f"{file_path}_chunk_{i}")
                
                # 임베딩 생성 및 저장
                if chunks:
                    embeddings = self.vector_service.improved_service.model.encode(chunks).tolist()
                    
                    self.vector_service.improved_service.collection.add(
                        embeddings=embeddings,
                        documents=chunks,
                        metadatas=chunk_metadata_list,
                        ids=chunk_ids
                    )
                    
                    print(f"   ✅ {len(chunks)}개의 청크가 추가되었습니다.")
                
                # 파일 해시 업데이트
                self.file_hashes[file_path] = generator.calculate_file_hash(file_path)
                
            except Exception as e:
                print(f"   ❌ 파일 처리 오류 {file_path}: {e}")
                continue
        
        # 4. 해시 저장
        self.save_file_hashes()
        
        print("\n" + "=" * 60)
        print("✅ 증분 업데이트가 완료되었습니다!")
        print("=" * 60)
        
        return True
    
    def remove_file_chunks_from_db(self, file_path: str):
        """특정 파일의 모든 청크를 데이터베이스에서 제거"""
        try:
            # 해당 파일의 모든 청크 찾기
            results = self.vector_service.improved_service.collection.get(
                where={"source": file_path}
            )
            
            if results and 'ids' in results and results['ids']:
                self.vector_service.improved_service.collection.delete(ids=results['ids'])
                print(f"   🗑️ 기존 청크 {len(results['ids'])}개 제거됨")
                
        except Exception as e:
            print(f"   ⚠️ 기존 청크 제거 중 오류: {e}")

def main():
    """메인 실행 함수"""
    print("Smart Research Manager - 증분 업데이트 도구")
    print("=" * 60)
    
    manager = IncrementalDatabaseManager()
    
    # 사용자 확인
    response = input("변경된 파일들만 증분 업데이트하시겠습니까? (y/N): ")
    if response.lower() != 'y':
        print("작업이 취소되었습니다.")
        return
    
    # 증분 업데이트 실행
    success = manager.incremental_update()
    
    if success:
        print("\n🎉 증분 업데이트가 완료되었습니다!")
        print("\n다음 단계:")
        print("1. Flask 앱을 재시작하여 업데이트된 데이터베이스를 사용하세요.")
        print("2. 각 기능을 테스트하여 성능을 확인하세요.")
    else:
        print("\n❌ 증분 업데이트에 실패했습니다.")

if __name__ == "__main__":
    main()
