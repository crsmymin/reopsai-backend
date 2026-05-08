#!/usr/bin/env python3
"""
개선된 RAG 데이터베이스 구축 스크립트
자동 생성된 매니페스트를 사용하여 데이터베이스를 구축합니다.
"""

import os
import shutil
import json
from datetime import datetime
from rag_system.improved.improved_vector_db_service import VectorDBServiceWrapper
from rag_system.improved.improved_rag_database_builder import ImprovedRAGDatabaseBuilder
from auto_metadata_generator import AutoMetadataGenerator

def backup_existing_database(db_path="./chroma_db", enable_backup=False):
    """기존 데이터베이스 백업 (선택적)"""
    if enable_backup and os.path.exists(db_path):
        backup_path = f"{db_path}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copytree(db_path, backup_path)
        print(f"기존 데이터베이스가 {backup_path}로 백업되었습니다.")
        return backup_path
    elif os.path.exists(db_path):
        print("백업 없이 기존 데이터베이스 내용을 삭제합니다.")
    return None

def clear_database_directory(db_path="./chroma_db"):
    """ChromaDB 디렉터리 내부만 비운다.

    Docker volume은 db_path 자체가 마운트 지점일 수 있어 디렉터리 자체를 삭제하면
    Device or resource busy 오류가 발생한다.
    """
    os.makedirs(db_path, exist_ok=True)

    removed_count = 0
    for entry in os.scandir(db_path):
        entry_path = entry.path
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry_path)
            else:
                os.remove(entry_path)
            removed_count += 1
        except Exception as e:
            raise RuntimeError(f"기존 ChromaDB 항목 삭제 실패 ({entry_path}): {e}") from e

    if removed_count > 0:
        print(f"기존 데이터베이스 내용 {removed_count}개 항목이 삭제되었습니다.")
    else:
        print("기존 데이터베이스가 비어 있습니다.")

def cleanup_old_backups(db_path="./chroma_db"):
    """기존 백업 폴더들 정리"""
    current_dir = os.path.dirname(os.path.abspath(db_path))
    backup_pattern = os.path.basename(db_path) + "_backup_"
    
    removed_count = 0
    for item in os.listdir(current_dir):
        if item.startswith(backup_pattern):
            backup_full_path = os.path.join(current_dir, item)
            if os.path.isdir(backup_full_path):
                try:
                    shutil.rmtree(backup_full_path)
                    print(f"기존 백업 폴더 삭제: {item}")
                    removed_count += 1
                except Exception as e:
                    print(f"백업 폴더 삭제 실패 ({item}): {e}")
    
    if removed_count > 0:
        print(f"총 {removed_count}개의 백업 폴더를 정리했습니다.")
    else:
        print("정리할 백업 폴더가 없습니다.")

def build_improved_database():
    """개선된 데이터베이스 구축"""
    print("=" * 60)
    print("개선된 RAG 데이터베이스 구축을 시작합니다...")
    print("=" * 60)
    db_path = os.getenv("RAG_DB_PATH", "./chroma_db")
    
    # 1. 기존 백업 폴더들 정리
    cleanup_old_backups(db_path)
    
    # 2. 기존 데이터베이스 백업 (비활성화)
    backup_path = backup_existing_database(db_path=db_path, enable_backup=False)
    
    # 3. 기존 데이터베이스 내용 삭제
    clear_database_directory(db_path)
    
    # 4. 자동 매니페스트 생성
    print("\n📄 자동 매니페스트 생성 중...")
    generator = AutoMetadataGenerator()
    manifest = generator.generate_manifest()
    
    if not manifest:
        print("❌ 매니페스트 생성에 실패했습니다.")
        return False
    
    # 5. 개선된 데이터베이스 구축
    try:
        # 데이터베이스 빌더 초기화
        builder = ImprovedRAGDatabaseBuilder()
        
        # 자동 생성된 매니페스트로 데이터베이스 구축
        database_data = builder.build_database_from_manifest(manifest)
        
        # 6. 개선된 VectorDB 서비스로 데이터베이스 생성
        vector_service = VectorDBServiceWrapper(
            db_path=db_path,
            collection_name="ux_rag"
        )
        
        # 7. 데이터 임베딩 및 저장
        print("\n데이터를 임베딩하고 저장하는 중...")
        embeddings = vector_service.improved_service.model.encode(database_data["chunks"]).tolist()
        
        vector_service.improved_service.collection.add(
            embeddings=embeddings,
            documents=database_data["chunks"],
            metadatas=database_data["metadata"],
            ids=database_data["ids"]
        )
        
        print(f"\n✅ 개선된 데이터베이스 구축 완료!")
        print(f"   - 총 청크 수: {len(database_data['chunks'])}")
        print(f"   - 평균 품질 점수: {sum(meta['quality_score'] for meta in database_data['metadata']) / len(database_data['metadata']):.2f}")
        
        # 8. 검색 테스트
        print("\n" + "=" * 60)
        print("검색 테스트를 실행합니다...")
        print("=" * 60)
        
        test_queries = [
            "설문조사 설계 원칙",
            "계획서 작성 가이드",
            "사용자 테스트 방법론"
        ]
        
        def print_preview(label: str, content: str, max_chars: int = 500):
            if not content:
                print(f"   {label}: (응답 없음)")
                return
            preview = content if len(content) <= max_chars else content[:max_chars] + "..."
            print(f"   {label} ({len(content)}자):")
            print(preview)
            print()

        for query in test_queries:
            print(f"\n🔍 테스트 쿼리: '{query}'")
            
            # 하이브리드 검색 테스트
            results = vector_service.improved_service.hybrid_search(
                query_text=query,
                principles_n=3,
                examples_n=2
            )
            
            print_preview("원칙 검색 결과", results['principles'])
            print_preview("예시 검색 결과", results['examples'])
            
            # 향상된 검색 테스트
            enhanced_results = vector_service.improved_service.enhanced_search(
                query_text=query,
                n_results=5
            )
            
            print_preview("향상된 검색 결과", enhanced_results)
        
        print("\n" + "=" * 60)
        print("✅ 모든 테스트가 성공적으로 완료되었습니다!")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ 데이터베이스 구축 중 오류 발생: {e}")
        
        # 백업 복원 기능 비활성화 (백업을 생성하지 않으므로)
        print("백업이 비활성화되어 있습니다. 필요시 수동으로 복원해주세요.")
        
        return False

def main():
    """메인 실행 함수"""
    print("Smart Research Manager - 개선된 RAG 데이터베이스 구축 도구")
    print("=" * 60)
    
    # 자동 실행 (백업 없이)
    print("기존 데이터베이스를 정리하고 새로운 개선된 데이터베이스를 구축합니다...")
    
    # 데이터베이스 구축 실행
    success = build_improved_database()
    
    if success:
        print("\n🎉 개선된 RAG 데이터베이스 구축이 완료되었습니다!")
        print("\n다음 단계:")
        print("1. Flask 앱을 재시작하여 새로운 데이터베이스를 사용하세요.")
        print("2. 각 기능을 테스트하여 성능 개선을 확인하세요.")
        print("3. 필요시 추가 데이터를 improved_file_manifest.json에 추가하세요.")
    else:
        print("\n❌ 데이터베이스 구축에 실패했습니다.")
        print("백업된 데이터베이스로 복원되었습니다.")

if __name__ == "__main__":
    main()
