"""
RAG 기반 페르소나 생성 파이프라인 - ETL Phase 1 (전처리/구조화)

- 입력: backend/data/원본/*.txt (인터뷰 원본 텍스트)
- 처리: LLM 기반 전처리(PII 마스킹, STT 교정, 구조화)
- 출력: backend/data/전처리데이터/user_001.json ... 형태로 파일 저장

실행 예시:
  python backend/persona_preprocess_pipeline.py
  python backend/persona_preprocess_pipeline.py --provider openai --model gpt-5.2
  python backend/persona_preprocess_pipeline.py --input-dir backend/data/원본 --output-dir backend/data/전처리데이터
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.openai_service import openai_service
from services.gemini_service import gemini_service


PREPROCESS_PROMPT = r"""
# Role Definition
당신은 핀테크/증권 도메인 전문 **'Qualitative Data Specialist'**입니다.
업로드된 인터뷰 스크립트를 분석하여, 사용자의 **[인간적 라이프스타일]**과 **[전문적 투자 행동]**을 분리하여 구조화하고 json으로 출력하십시오.

---

# Phase 1: Pre-processing (전처리 및 보안)
1. **Sanitization:** 실명, 전화번호, 주소, 가족 구성 등 개인정보(PII)는 마스킹하지 말고 **해당 문구를 삭제**하십시오. 문맥이 끊기지 않도록 필요한 경우만 간단히 대명사 등으로 이어주십시오.
   - *단, 토스, 키움, 삼성증권, Webull, 삼프로TV 등 구체적인 서비스/채널명은 리얼리티를 위해 그대로 둡니다.*
2. **STT Correction:** 문맥상 명백한 오타(STT 오류)를 수정하십시오. (예: 미장 -> 미국주식, 국장 -> 국내주식, 예수금 -> 예치금 등 문맥 고려)
3. **Format:** 줄바꿈을 공백으로 치환하여 한 줄(Single Line)의 `Sanitized_Transcript`를 생성하십시오.

# Phase 2: Extraction (메타데이터 및 오픈 코딩)
1. **Demographics:** 나이, 직업, 라이프스타일, 가족 구성 등을 파악하십시오.
2. **Open Coding:** 텍스트 내에서 반복되는 핵심 키워드, 감정, 사용하는 앱/채널을 추출하십시오.

# Phase 3: Axial Coding (축 코딩 - 근거이론 기반 구조화)
사용자의 경험을 **[조건(맥락) -> 현상 -> 상호작용 -> 결과]**의 논리적 서사로 연결하십시오.

- **구조:**
  1. **[조건/맥락 (Conditions)]:** 어떤 상황이나 배경 지식, 환경 때문에 (예: "직장 생활로 바쁘고 꼼꼼하지 못한 성격이라")
  2. **[현상 (Phenomenon)]:** 어떤 문제나 중심 사건을 경험했고 (예: "복잡한 기능을 보면 스트레스를 받고 회피함")
  3. **[상호작용/전략 (Interaction)]:** 이를 해결하기 위해 어떻게 행동하거나 앱을 사용했으며 (예: "직관적인 토스만 사용하거나 지인에게 대신 물어봄")
  4. **[결과 (Consequences)]:** 그 결과 어떤 상태가 되었는가 (예: "깊이 있는 정보는 포기하더라도 편의성을 택함")

- **Output:** 위 4단계 흐름이 자연스럽게 이어지는 3~5문장의 요약글 (`Axial_Summary`)을 작성하십시오.

# Phase 4: CC Mapping & Investment Profiling (이원화 분류) [Critical]
분석 내용을 바탕으로 **[A. 인간적 특성]**과 **[B. 투자자 특성]**을 철저히 분리하여 매핑하십시오.

## A. General CC (보편적 인간 특성 - Human Nature)
*이 섹션에서는 '주식/투자' 이야기를 배제하고, 사람 자체의 성향에 집중하십시오.*
1. **Goals_Motivations:** 삶에서 추구하는 핵심 가치나 욕구 (예: 효율성 추구, 안정감, 인정 욕구, 트렌드 동참).
2. **Pain_Points:** 일상생활이나 디지털 서비스 전반에서 느끼는 스트레스 요인 (예: 복잡한 것 질색, 기다리는 것 못 참음, 새로운 기술에 대한 두려움).
3. **Attitudes_Values:** 세상을 대하는 태도 및 성격 (예: 꼼꼼하고 완벽주의적, 느긋하고 낙관적, 의심이 많음).
4. **Interaction_Style:** 타인과 대화할 때의 말투와 태도 (예: 논리정연함, 감정적 호소, 겸손함, 직설적).

## B. Investment Profile (투자자 특성 - Investor DNA)
아래 6가지 축에 대해 해당 사용자의 위치를 **[Tag]** 형태로 정의하고 괄호 안에 근거를 요약하십시오.

**[Group 1: 내적 사고 및 판단 (Thinking)]**
1. **Investment_Activeness (관심도/적극성):**
   - **[High]:** 매일/수시 확인, 알림 민감.
   - **[Medium]:** 주 1~2회 확인, 이슈 있을 때만.
   - **[Low]:** 장기 방치, 자동 적립.
2. **Decision_Dependency (의사결정 의존성):**
   - **[Self_Driven]:** 직접 분석(재무/차트), 뉴스 불신.
   - **[Social_Driven]:** 유튜브, 커뮤니티, 지인 추천.
   - **[Expert_Driven]:** 증권사 리포트, PB 상담.
3. **Investment_Literacy (투자 이해도):**
   - **[High]:** 전문 용어/지표 이해 및 활용.
   - **[Mid]:** 기본 구조 이해.
   - **[Low]:** 용어 어려움, 주린이.

**[Group 2: 외적 매매 스타일 (Action)]**
4. **Time_Horizon (투자 호흡):**
   - **[Scalping/Day]:** 초단타, 당일 매매.
   - **[Swing]:** 며칠~몇 주 보유.
   - **[Long-term]:** 수개월 이상 장기 보유.
5. **Risk_Appetite (위험 감수성):**
   - **[Aggressive]:** 코인, 급등주, 레버리지 선호.
   - **[Moderate]:** 우량주 위주, 적절한 분산.
   - **[Conservative]:** 원금 보장형, 배당주, 채권 선호.
6. **App_Usage (앱 사용 패턴):**
   - **[All-in-one]:** 하나의 앱에서 정보~거래 해결.
   - **[Multi-App]:** 정보용 앱과 거래용 앱을 분리해서 사용.

작성 예시: "•Activeness: High •Dependency: Self_Driven •Literacy: High •Time: Swing •Risk: Aggressive •Usage: Multi-App"

Quantitative Scoring (정량화): 각 6가지 축에 대해 1~5점 척도로 점수를 부여하십시오.
Activeness Score (1-5): 1(방치형) ~ 5(초단타/실시간 대응)
Dependency Score (1-5): 1(완전 독자 판단) ~ 5(전적으로 타인/전문가 의존)
Literacy Score (1-5): 1(용어 모름) ~ 5(전문가 수준/HTS 능숙)
Time Horizon Score (1-5): 1(데이/스캘핑) ~ 5(1년 이상 장기/은퇴준비)
Risk Score (1-5): 1(원금보장 추구) ~ 5(레버리지/급등주 선호)
Tech Fluency Score (1-5): 1(디지털 소외/어려움) ~ 5(다양한 툴/PC/해외앱 자유 활용)

---

# Final Action: CSV Output Generation
위 내용을 모두 포함하여 CSV 포맷으로 출력하십시오.

**[CSV Header]**
`ID, Demographics, Investment_Profile, Open_Codes, Axial_Summary, Goals_Motivations, Pain_Points, Attitudes_Values, Interaction_Style, Sanitized_Transcript`

**[작성 규칙]**
- **Investment_Profile:** 위에서 정의한 6가지 태그와 설명이 반드시 포함되어야 함.
- **Safe Quoting:** 모든 셀의 데이터는 반드시 **큰따옴표(" ")**로 감싸 CSV 구조가 깨지지 않게 하십시오.
"""


EXPECTED_KEYS = [
    "ID",
    "Demographics",
    "Investment_Profile",
    "Open_Codes",
    "Axial_Summary",
    "Goals_Motivations",
    "Pain_Points",
    "Attitudes_Values",
    "Interaction_Style",
    "Sanitized_Transcript",
]


def _blank_record(persona_id: str) -> Dict[str, str]:
    return {k: (persona_id if k == "ID" else "") for k in EXPECTED_KEYS}


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"```(?:json|python)?\s*\n", "", t)
    t = re.sub(r"\n\s*```", "", t)
    return t.strip()


def _parse_json_like(text: str) -> Dict[str, Any]:
    """
    JSON 모드가 아닌 모델이 섞여도 최대한 JSON을 복구.
    (backend/app.py의 parse_llm_json_response와 동일한 의도)
    """
    t = _strip_code_fences(text)
    t = re.sub(r"^#+\s+.*$", "", t, flags=re.MULTILINE).strip()

    start = t.find("{")
    if start != -1:
        t = t[start:]
    end = t.rfind("}")
    if end != -1:
        t = t[: end + 1]

    try:
        return json.loads(t)
    except json.JSONDecodeError:
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", t)
        return json.loads(fixed)


def _parse_csv_row(text: str) -> Dict[str, str]:
    """
    LLM이 CSV 한 줄(헤더 포함/미포함)을 반환했을 때를 대비한 fallback.
    """
    t = _strip_code_fences(text)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("CSV 파싱 실패: 빈 응답")

    # 헤더가 포함된 경우: 마지막 줄이 row일 가능성이 높음(모델이 헤더+row로 내기도 함)
    header_line_idx = None
    for i, ln in enumerate(lines[:3]):
        if "ID" in ln and "Sanitized_Transcript" in ln:
            header_line_idx = i
            break

    if header_line_idx is not None:
        header = lines[header_line_idx]
        row = lines[header_line_idx + 1] if header_line_idx + 1 < len(lines) else ""
        reader = csv.DictReader([row], fieldnames=[h.strip() for h in header.split(",")])
        parsed = next(reader)
        return {k.strip(): (v or "").strip().strip('"') for k, v in parsed.items() if k}

    # 헤더가 없는 경우: EXPECTED_KEYS 순서대로 한 줄이라고 가정(안전한 가정은 아니지만 fallback)
    reader2 = csv.reader(lines)
    first = next(reader2)
    if len(first) < len(EXPECTED_KEYS):
        raise ValueError("CSV 파싱 실패: 컬럼 수 부족")
    mapped = {EXPECTED_KEYS[i]: (first[i] or "").strip().strip('"') for i in range(len(EXPECTED_KEYS))}
    return mapped


def _coerce_record(obj: Dict[str, Any], fallback_id: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k in EXPECTED_KEYS:
        v = obj.get(k, "")
        if v is None:
            v = ""
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        out[k] = v

    if not out["ID"]:
        out["ID"] = fallback_id

    # Sanitized_Transcript는 반드시 single-line
    out["Sanitized_Transcript"] = re.sub(r"\s+", " ", out["Sanitized_Transcript"]).strip()
    return out


@dataclass(frozen=True)
class PreprocessResult:
    record: Dict[str, str]
    raw_output: str
    repaired_from: Optional[str]
    provider: str
    model: str


def _repair_to_json_via_openai(*, broken_text: str, persona_id: str, model: Optional[str]) -> Dict[str, Any]:
    """
    LLM 출력이 JSON 파싱에 실패한 경우, JSON 모드로 '복구'를 시도한다.
    (응답이 잘렸거나 따옴표/개행 때문에 깨진 경우를 구제)
    """
    repair_prompt = f"""
아래 텍스트는 LLM이 생성한 결과인데 JSON이 깨져 있습니다. 이를 **유효한 JSON 객체**로만 다시 출력하세요.

규칙:
- 출력은 반드시 JSON 객체만. (설명/마크다운/코드블록 금지)
- 키는 정확히 다음 10개만: {", ".join(EXPECTED_KEYS)}
- 모든 값은 문자열
- ID는 반드시 "{persona_id}"
- Sanitized_Transcript는 single line(개행 금지). 너무 길면 12000자까지만 포함하고 뒤에 "...(truncated)"를 붙이세요.

깨진 출력:
<<<BROKEN_START>>>
{broken_text}
<<<BROKEN_END>>>
""".strip()

    generation_config = {
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    raw = openai_service.generate_response(repair_prompt, generation_config, model_name=model)
    if not raw.get("success"):
        raise RuntimeError(f"OpenAI repair 호출 실패: {raw.get('error')}")
    return _parse_json_like(raw.get("content") or "")


def preprocess_transcript_via_llm(
    *,
    transcript_text: str,
    persona_id: str,
    provider: str = "openai",
    model: Optional[str] = None,
) -> PreprocessResult:
    """
    원본 인터뷰 텍스트 → 프롬프트 기반 전처리/구조화 → JSON 레코드 반환
    """
    instructions = f"""
아래 인터뷰 텍스트를 위 프롬프트(전처리~CC Mapping~정량화) 기준으로 분석하십시오.

중요:
- 출력은 반드시 JSON 객체만 반환하십시오. (마크다운/코드블록/설명 금지)
- JSON 키는 정확히 다음 10개만 사용하십시오:
  {", ".join(EXPECTED_KEYS)}
- 모든 값은 문자열로만 채우십시오. (모르면 빈 문자열)
- ID는 반드시 "{persona_id}" 로 설정하십시오.
- Sanitized_Transcript는 줄바꿈 없이 공백으로 연결된 single line이어야 합니다.
  - 단, 너무 길면 12000자까지만 포함하고 뒤에 "...(truncated)"를 붙이세요.
  - 다른 필드들도 불필요하게 길게 쓰지 마세요(간결하게 요약).

인터뷰 원문:
<<<TRANSCRIPT_START>>>
{transcript_text}
<<<TRANSCRIPT_END>>>
""".strip()

    prompt = PREPROCESS_PROMPT.strip() + "\n\n" + instructions + "\n"

    if provider == "gemini":
        raw = gemini_service.generate_response(
            prompt,
            {"temperature": 0.2, "max_output_tokens": 8192},
            model_name=model,
        )
        if not raw.get("success"):
            raise RuntimeError(f"Gemini 호출 실패: {raw.get('error')}")
        content = raw.get("content") or ""
        try:
            obj = _parse_json_like(content)
        except Exception:
            # Gemini는 JSON 모드 강제가 어려워 깨질 수 있음 → CSV fallback
            obj = _parse_csv_row(content)
        rec = _coerce_record(obj, persona_id)
        return PreprocessResult(
            record=rec,
            raw_output=content,
            repaired_from=None,
            provider="gemini",
            model=model or "gemini-2.0-flash",
        )

    # default: openai
    generation_config = {
        "temperature": 0.2,
        # Sanitized_Transcript가 길어질 수 있어 넉넉하게(너무 길면 모델이 잘라야 함)
        "max_output_tokens": 12000,
        # JSON 모드: 모델이 JSON만 내도록 강제 (가능한 모델에서만 동작)
        "response_format": {"type": "json_object"},
    }
    raw = openai_service.generate_response(prompt, generation_config, model_name=model)
    if not raw.get("success"):
        raise RuntimeError(f"OpenAI 호출 실패: {raw.get('error')}")

    content = raw.get("content") or ""
    try:
        obj = _parse_json_like(content)
        rec = _coerce_record(obj, persona_id)
        return PreprocessResult(
            record=rec,
            raw_output=content,
            repaired_from=None,
            provider="openai",
            model=model or "gpt-5.2",
        )
    except Exception:
        # JSON이 깨졌을 때: (1) repair 시도 (2) 그래도 실패 시 CSV fallback (3) 최후에 빈 레코드
        repaired_from = content
        try:
            repaired_obj = _repair_to_json_via_openai(broken_text=content, persona_id=persona_id, model=model)
            rec = _coerce_record(repaired_obj, persona_id)
            return PreprocessResult(
                record=rec,
                raw_output=_strip_code_fences(json.dumps(repaired_obj, ensure_ascii=False)),
                repaired_from=repaired_from,
                provider="openai",
                model=model or "gpt-5.2",
            )
        except Exception:
            try:
                obj2 = _parse_csv_row(content)
                rec = _coerce_record(obj2, persona_id)
                return PreprocessResult(
                    record=rec,
                    raw_output=content,
                    repaired_from=repaired_from,
                    provider="openai",
                    model=model or "gpt-5.2",
                )
            except Exception:
                return PreprocessResult(
                    record=_blank_record(persona_id),
                    raw_output=content,
                    repaired_from=repaired_from,
                    provider="openai",
                    model=model or "gpt-5.2",
                )


def _extract_user_id_from_filename(path: Path) -> Optional[str]:
    m = re.match(r"^(user_(\d{3,}))\b", path.stem)
    if not m:
        return None
    return m.group(1)


def _next_available_user_id(output_dir: Path) -> str:
    existing = []
    if output_dir.exists():
        for p in output_dir.glob("user_*.json"):
            m = re.match(r"^user_(\d+)\.json$", p.name)
            if m:
                existing.append(int(m.group(1)))
    n = (max(existing) + 1) if existing else 1
    return f"user_{n:03d}"


def iter_input_files(input_dir: Path) -> List[Path]:
    if not input_dir.exists():
        return []
    return sorted([p for p in input_dir.glob("*.txt") if p.is_file()])


def run_phase1_preprocess(
    *,
    input_dir: Path,
    output_dir: Path,
    provider: str,
    model: Optional[str],
    overwrite: bool,
) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    for in_path in iter_input_files(input_dir):
        persona_id = _extract_user_id_from_filename(in_path)
        if not persona_id:
            persona_id = _next_available_user_id(output_dir)

        out_path = output_dir / f"{persona_id}.json"
        if out_path.exists() and not overwrite:
            print(f"[SKIP] exists: {out_path.name}")
            continue

        text = in_path.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            print(f"[SKIP] empty: {in_path.name}")
            continue

        print(f"[RUN] {in_path.name} -> {out_path.name} ({provider}{'/' + model if model else ''})")
        try:
            result = preprocess_transcript_via_llm(
                transcript_text=text,
                persona_id=persona_id,
                provider=provider,
                model=model,
            )
            payload = {
                **result.record,
                "_meta": {
                    "source_file": in_path.name,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "provider": result.provider,
                    "model": result.model,
                    "repaired": bool(result.repaired_from),
                },
                "_raw_output": result.raw_output,
            }
            if result.repaired_from:
                payload["_raw_output_before_repair"] = result.repaired_from

            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(out_path)
            print(f"[OK] wrote: {out_path.name}")
        except Exception as e:
            # 파일별로 실패해도 전체 파이프라인이 멈추지 않도록, 최소한의 결과 파일을 남긴다.
            payload = {
                **_blank_record(persona_id),
                "_meta": {
                    "source_file": in_path.name,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "provider": provider,
                    "model": model or "",
                    "repaired": False,
                    "error": str(e),
                },
            }
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(out_path)
            print(f"[ERROR] {in_path.name}: {e}")
            print(f"        wrote blank output: {out_path.name}")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="ETL Phase 1: 인터뷰 원본 -> 전처리 JSON 저장")
    parser.add_argument("--input-dir", default="backend/data/원본", help="원본 텍스트 폴더")
    parser.add_argument("--output-dir", default="backend/data/전처리데이터", help="전처리 JSON 출력 폴더")
    parser.add_argument("--provider", choices=["openai", "gemini"], default="openai", help="LLM provider")
    parser.add_argument("--model", default="gpt-5.2", help="OpenAI 모델명 (기본: gpt-5.2)")
    parser.add_argument("--overwrite", action="store_true", help="기존 json 파일이 있어도 덮어쓰기")
    args = parser.parse_args()

    # 실행 위치(CWD)에 따라 경로가 꼬이지 않도록, 항상 "레포 루트" 기준으로 상대경로를 해석한다.
    # 예) backend/ 폴더에서 실행하더라도 "backend/data/원본"은 올바르게 해석되어야 함.
    repo_root = Path(__file__).resolve().parent.parent

    def _resolve_from_repo(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else (repo_root / pp)

    input_dir = _resolve_from_repo(args.input_dir)
    output_dir = _resolve_from_repo(args.output_dir)

    input_files = iter_input_files(input_dir)
    if not input_files:
        print(f"[WARN] input files not found. looked at: {input_dir}")
        print("       tip: try --input-dir backend/data/원본 (repo root 기준) 또는 절대경로를 지정하세요.")

    written = run_phase1_preprocess(
        input_dir=input_dir,
        output_dir=output_dir,
        provider=args.provider,
        model=args.model,
        overwrite=args.overwrite,
    )

    print(f"[DONE] wrote {len(written)} files -> {output_dir}")


if __name__ == "__main__":
    main()

