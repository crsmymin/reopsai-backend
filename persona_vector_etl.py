"""
RAG 기반 페르소나 벡터 DB 적재 - ETL Phase 2

- 입력: structured_data(전처리 JSON) 폴더의 *.json
- 메타데이터: 나이, 직업, 성별, demographics 등 → ChromaDB 메타데이터 (필터링용)
- 문서: 성향/행동 요약/인터뷰 내용 → 임베딩 후 벡터 저장 (의미 기반 검색용)

실행 예시:
  python backend/persona_vector_etl.py
  python backend/persona_vector_etl.py --structured-dir backend/data/전처리데이터 --db-path ./chroma_db --collection persona
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 문서로 합칠 필드 (의미 기반 검색용 임베딩)
DOCUMENT_FIELDS = [
    "Axial_Summary",
    "Goals_Motivations",
    "Pain_Points",
    "Attitudes_Values",
    "Interaction_Style",
    "Sanitized_Transcript",
]

# 메타데이터로 쓸 필드명 (JSON 키)
META_DEMOGRAPHICS_KEY = "Demographics"
ID_KEY = "ID"

# ChromaDB 메타데이터는 str, int, float, bool 만 허용
META_AGE_KEY = "age"
META_JOB_KEY = "job"
META_GENDER_KEY = "gender"
META_PERSONA_ID_KEY = "persona_id"
META_DEMOGRAPHICS_META_KEY = "demographics"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_path(p: str, base: Path) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else (base / pp)


def _parse_demographics_for_metadata(demographics: str) -> Dict[str, Any]:
    """
    Demographics 문장에서 나이/직업/성별을 추출해 ChromaDB 메타데이터용으로 반환.
    값은 반드시 str | int | float | bool (Chroma 호환).
    """
    out: Dict[str, Any] = {
        META_AGE_KEY: "",
        META_JOB_KEY: "",
        META_GENDER_KEY: "",
    }
    if not demographics or not isinstance(demographics, str):
        return out

    text = demographics.strip()

    # 나이: "NNNN년생" -> 출생년도 기준 나이 (현재년도 - NNNN)
    birth_match = re.search(r"(\d{4})\s*년생", text)
    if birth_match:
        try:
            from datetime import datetime
            birth_year = int(birth_match.group(1))
            age = datetime.now().year - birth_year
            if 0 <= age <= 120:
                out[META_AGE_KEY] = age  # int
        except (ValueError, TypeError):
            pass

    # 성별: 남성/여성/남/여 등
    if re.search(r"남성|남\s*자|남\b", text):
        out[META_GENDER_KEY] = "남"
    elif re.search(r"여성|여\s*자|여\b", text):
        out[META_GENDER_KEY] = "여"

    # 직업: 첫 문장 또는 "직장인", "공공기관", "데이터 분석" 등 직업/소속 관련 앞부분 (최대 200자)
    job_candidates = []
    for part in re.split(r"[.]\s+", text, maxsplit=2):
        part = part.strip()
        if not part:
            continue
        if any(kw in part for kw in ("직장인", "기관", "업무", "담당", "직장", "회사", "공공", "데이터", "분석", "기록물")):
            job_candidates.append(part)
        elif not job_candidates:
            job_candidates.append(part)
    if job_candidates:
        job_str = job_candidates[0][:200].strip()
        if job_str:
            out[META_JOB_KEY] = job_str

    # Chroma는 메타데이터 값에 빈 문자열이 있으면 문제될 수 있으므로, 없으면 "unknown"
    for k in (META_JOB_KEY, META_GENDER_KEY):
        if out[k] == "":
            out[k] = "unknown"
    if out.get(META_AGE_KEY) == "":
        out[META_AGE_KEY] = "unknown"
    return out


def _build_document_text(record: Dict[str, Any]) -> str:
    """한 페르소나 레코드에서 임베딩용 문서 텍스트를 만든다."""
    parts: List[str] = []
    for field in DOCUMENT_FIELDS:
        val = record.get(field)
        if val is None:
            val = ""
        if not isinstance(val, str):
            val = str(val)
        val = val.strip()
        if val:
            parts.append(f"[{field}]\n{val}")
    return "\n\n".join(parts) if parts else ""


def _build_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    """ChromaDB 메타데이터 생성 (필터링용). 값은 str | int | float | bool 만."""
    persona_id = (record.get(ID_KEY) or "").strip() or "unknown"
    demographics = (record.get(META_DEMOGRAPHICS_KEY) or "").strip()
    parsed = _parse_demographics_for_metadata(demographics)

    meta: Dict[str, Any] = {
        META_PERSONA_ID_KEY: persona_id,
        META_DEMOGRAPHICS_META_KEY: demographics[:2000] if demographics else "unknown",  # Chroma 메타 길이 제한 대비
    }
    # 나이: int 또는 보조용 문자열
    if isinstance(parsed.get(META_AGE_KEY), int):
        meta[META_AGE_KEY] = parsed[META_AGE_KEY]
    else:
        meta[META_AGE_KEY] = parsed.get(META_AGE_KEY, "unknown")
    meta[META_JOB_KEY] = parsed.get(META_JOB_KEY, "unknown")
    meta[META_GENDER_KEY] = parsed.get(META_GENDER_KEY, "unknown")
    return meta


def _load_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _iter_structured_jsons(structured_dir: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    """structured_dir 내 *.json 파일을 읽어 (path, record) 리스트 반환. _meta/_raw_output 제외한 필드만 사용."""
    results: List[Tuple[Path, Dict[str, Any]]] = []
    for p in sorted(structured_dir.glob("*.json")):
        data = _load_json_safe(p)
        if not data:
            continue
        # 내부 키 제외
        record = {k: v for k, v in data.items() if k not in ("_meta", "_raw_output")}
        if not record.get(ID_KEY):
            record[ID_KEY] = p.stem
        results.append((p, record))
    return results


def load_structured_data_to_chromadb(
    structured_data_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    collection_name: str = "persona",
    model_name: str = "jhgan/ko-sbert-nli",
    upsert: bool = True,
) -> int:
    """
    structured_data의 JSON 파일들을 읽어 ChromaDB에 적재한다.

    - Metadata: 나이, 직업, 성별, demographics → 필터링용 메타데이터
    - Documents: 성향/행동 요약/인터뷰 내용 → 임베딩 후 벡터 저장

    Returns:
        적재된 문서 수
    """
    from rag_system.improved.improved_vector_db_service import ImprovedVectorDBService

    repo = _repo_root()
    structured_dir = _resolve_path(
        structured_data_dir or "backend/data/전처리데이터",
        repo,
    )
    db_path_resolved = _resolve_path(db_path or "backend/chroma_db", repo)

    if not structured_dir.is_dir():
        raise FileNotFoundError(f"structured_data 디렉터리가 없습니다: {structured_dir}")

    items = _iter_structured_jsons(structured_dir)
    if not items:
        return 0

    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []
    ids: List[str] = []

    for _path, record in items:
        persona_id = (record.get(ID_KEY) or _path.stem).strip()
        doc_text = _build_document_text(record)
        if not doc_text.strip():
            continue
        meta = _build_metadata(record)
        # Chroma 메타데이터 값 타입 제한: str, int, float, bool
        for k, v in list(meta.items()):
            if v is None:
                meta[k] = "unknown"
            elif isinstance(v, (list, dict)):
                meta[k] = json.dumps(v, ensure_ascii=False)[:1000]

        ids.append(f"persona_{persona_id}")
        documents.append(doc_text)
        metadatas.append(meta)

    if not documents:
        return 0

    service = ImprovedVectorDBService(
        db_path=str(db_path_resolved),
        collection_name=collection_name,
        model_name=model_name,
    )
    embeddings = service.model.encode(documents).tolist()

    if upsert:
        service.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
    else:
        service.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    return len(documents)


def main() -> None:
    parser = argparse.ArgumentParser(description="ETL Phase 2: 전처리 JSON → ChromaDB 적재")
    parser.add_argument(
        "--structured-dir",
        default="backend/data/전처리데이터",
        help="전처리 JSON 폴더 (repo root 기준)",
    )
    parser.add_argument(
        "--db-path",
        default="backend/chroma_db",
        help="ChromaDB 저장 경로 (repo root 기준)",
    )
    parser.add_argument(
        "--collection",
        default="persona",
        help="ChromaDB 컬렉션 이름",
    )
    parser.add_argument(
        "--model",
        default="jhgan/ko-sbert-nli",
        help="임베딩 모델명",
    )
    parser.add_argument(
        "--no-upsert",
        action="store_true",
        help="upsert 대신 add 사용 (기존 id와 충돌 시 실패)",
    )
    args = parser.parse_args()

    repo = _repo_root()
    structured_dir = _resolve_path(args.structured_dir, repo)
    db_path = _resolve_path(args.db_path, repo)

    if not structured_dir.is_dir():
        print(f"[ERROR] structured_data 디렉터리가 없습니다: {structured_dir}")
        return

    n = load_structured_data_to_chromadb(
        structured_data_dir=str(structured_dir),
        db_path=str(db_path),
        collection_name=args.collection,
        model_name=args.model,
        upsert=not args.no_upsert,
    )
    print(f"[DONE] ChromaDB 적재 완료: {n}건 (collection={args.collection})")


if __name__ == "__main__":
    main()
