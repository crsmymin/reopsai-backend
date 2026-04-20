"""
Vector DB 서비스 싱글턴.

app.py와 각 Blueprint들이 동일한 인스턴스를 공유하기 위해 분리됨.
"""
from rag_system.improved.improved_vector_db_service import VectorDBServiceWrapper

try:
    vector_service = VectorDBServiceWrapper(
        db_path="./chroma_db",
        collection_name="ux_rag"
    )
    print("vector_service: 개선된 VectorDBService 초기화 성공.")
except Exception as e:
    print(f"vector_service: 치명적 오류! 개선된 VectorDBService 초기화 실패: {e}")
    vector_service = None
