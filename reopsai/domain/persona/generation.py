from __future__ import annotations

import concurrent.futures
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "nemotron-personas-korea-sample.jsonl"
DEFAULT_TEXT_MODEL = os.getenv("PERSONA_LLM_SEGMENTATION_IDENTITY_MODEL") or os.getenv("PERSONA_GEMINI_TEXT_MODEL") or "gemini-2.5-flash"
DEFAULT_PERSONA_GENERATION_MAX_CONCURRENCY = 3
PERSONA_TAG_MAX_LENGTH = 20

TELECOM_CONTEXT_PATTERN = re.compile(
    r"통신|요금제|번호이동|멤버십|결합|부가서비스|대리점|carrier|telecom|wireless|mobile plan|phone plan|subscription",
    re.IGNORECASE,
)
TOKEN_STOPWORDS = {
    "그리고",
    "하지만",
    "있는",
    "없는",
    "합니다",
    "한다",
    "서비스",
    "사용자",
    "고객",
    "퍼소나",
    "대한민국",
}


def resolve_persona_generation_max_concurrency(persona_count: int) -> int:
    configured = os.getenv("PERSONA_GENERATION_MAX_CONCURRENCY") or os.getenv("PERSONA_EMBODIMENT_CONCURRENCY") or ""
    try:
        max_workers = int(configured)
    except Exception:
        max_workers = DEFAULT_PERSONA_GENERATION_MAX_CONCURRENCY
    if max_workers <= 0:
        max_workers = DEFAULT_PERSONA_GENERATION_MAX_CONCURRENCY
    return max(1, min(max(1, int(persona_count or 0)), max_workers))


def _map_with_concurrency(items: list, concurrency: int, mapper: Callable[[object], object]) -> list:
    if not items:
        return []
    max_workers = max(1, min(len(items), int(concurrency or 1)))
    if max_workers == 1:
        return [mapper(item) for item in items]

    results = [None] * len(items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="persona-generation") as executor:
        future_to_index = {executor.submit(mapper, item): index for index, item in enumerate(items)}
        for future in concurrent.futures.as_completed(future_to_index):
            results[future_to_index[future]] = future.result()
    return results


def _log_persona_generation_event(message: str, **values):
    details = " ".join(f"{key}={value}" for key, value in values.items() if value is not None)
    suffix = f" | {details}" if details else ""
    print(f"[persona-generation] {message}{suffix}", flush=True)


def _normalize_persona_tag(value) -> str | None:
    tag = str(value or "").strip()
    return tag[:PERSONA_TAG_MAX_LENGTH] if tag else None


def _resolve_persona_tag(profile: dict, segment: dict, payload: dict) -> str | None:
    segment_inputs = payload.get("segmentInputs")
    if isinstance(segment_inputs, list) and segment_inputs:
        for item in segment_inputs:
            if not isinstance(item, dict):
                continue
            if item.get("id") in {profile.get("segmentId"), segment.get("id")}:
                return _normalize_persona_tag(item.get("name") or segment.get("name"))
    return _normalize_persona_tag(segment.get("name"))


def _with_persona_tag(persona: dict, profile: dict, segment: dict, payload: dict) -> dict:
    return {
        **persona,
        "tag": _normalize_persona_tag(persona.get("tag")) or _resolve_persona_tag(profile, segment, payload),
    }


TELECOM_DIMENSION_GROUPS = {
    "brandRetention": ("brandRetentionTendency", "premiumInfraBenefitOrientation"),
    "optimizationResource": ("optimizationResourceInvestment", "paymentResistanceLine"),
    "informationControl": ("informationExplorationStyle", "problemSolvingAutonomy"),
    "digitalAiOpenness": ("aiProviderTrust", "personalizationDataSharingScope"),
    "telecomLifeCharacteristics": (
        "householdDecisionLeadership",
        "productServiceUnderstanding",
        "telecomServiceUsageContext",
    ),
}
TELECOM_SCORE_GROUPS = {
    "brandRetention": "브랜드 유지 성향",
    "optimizationResource": "최적화 리소스 투입",
    "informationControl": "정보탐색 및 통제 욕구",
    "digitalAiOpenness": "디지털 및 AI 개방성",
}
TELECOM_INTERVIEW_REFERENCES = [
    {
        "typeId": "유형1",
        "sourceUserIds": ["user01"],
        "summary": "장기 통신사 이용자이며 결합 구조와 번호 변경 부담 때문에 이동성이 낮다.",
    },
    {
        "typeId": "유형2",
        "sourceUserIds": ["user02", "user03", "user05", "user06"],
        "summary": "가족 결합, 멤버십, 사용량을 직접 비교하는 실용적 관리형 이용자다.",
    },
    {
        "typeId": "유형3",
        "sourceUserIds": ["user04"],
        "summary": "알뜰폰, 자급제, 커뮤니티 정보를 적극 비교하는 고관여 절약형 이용자다.",
    },
    {
        "typeId": "유형4",
        "sourceUserIds": ["user07", "user08", "user09"],
        "summary": "가격과 데이터량, OTT 조건, 앱 AI 검색을 빠르게 훑는 젊은 변경 검토층이다.",
    },
    {
        "typeId": "유형5",
        "sourceUserIds": ["user11"],
        "summary": "가족 전체 회선과 인터넷/IPTV 결합을 관리하는 장기 이용 관리자다.",
    },
    {
        "typeId": "유형6",
        "sourceUserIds": ["user10", "user12"],
        "summary": "프리미엄 요금제, OTT/구독 혜택, 앱 기반 탐색과 AI 상담 기대가 큰 이용자다.",
    },
]
NARRATIVE_REQUIRED_FIELDS = [
    "attitudes",
    "biography",
    "demeanour",
    "interests",
    "behaviours",
    "motivation",
    "personality",
    "preferences",
    "culturalBackground",
    "quote",
    "imagePrompt",
]
LANGUAGE_LABELS = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "de": "German",
    "zh": "Chinese",
}

SEGMENT_SUGGESTION_MAX_CONTEXT_LENGTH = 4000
SEGMENT_SUGGESTION_DEFAULT_MAX_SEGMENTS = 4
MIN_SERVICE_DESCRIPTION_LENGTH = 10
MIN_SEGMENT_NAME_LENGTH = 2
MIN_SEGMENT_DESCRIPTION_LENGTH = 10


class PersonaGenerationQualityError(ValueError):
    """Raised when a stage returns syntactically valid but incomplete persona data."""


def resolve_seed_path(path: Path | None = None) -> Path:
    configured = os.getenv("PERSONA_NEMOTRON_SEED_PATH")
    if path is not None:
        return Path(path)
    if configured:
        return Path(configured)
    return DEFAULT_SEED_PATH


def load_seed_personas(path: Path | None = None, *, limit: int = 50) -> list[dict]:
    seed_path = resolve_seed_path(path)
    if not seed_path.exists():
        raise FileNotFoundError(f"Nemotron seed file not found: {seed_path}")
    seeds = []
    with seed_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if len(seeds) >= limit:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                seeds.append(value)
    if not seeds:
        raise ValueError(f"Nemotron seed file did not contain usable JSON objects: {seed_path}")
    return seeds


def infer_persona_source_type(payload: dict) -> str:
    source_type = payload.get("sourceType")
    if source_type in {"service_based", "segment_based"}:
        return source_type
    segment_inputs = payload.get("segmentInputs")
    if isinstance(segment_inputs, list) and segment_inputs:
        return "segment_based"
    return "service_based"


def _coerce_count(value, default: int = 1) -> int:
    try:
        return max(1, min(50, int(round(float(value)))))
    except Exception:
        return default


def _parse_existing_personas(value) -> tuple[list[dict] | None, bool]:
    if value is None:
        return None, True
    if not isinstance(value, list):
        return None, False
    parsed = []
    for persona in value:
        if not isinstance(persona, dict) or not isinstance(persona.get("name"), str) or not persona["name"].strip():
            return None, False
        parsed.append(
            {
                "name": persona["name"].strip(),
                "age": persona.get("age") if isinstance(persona.get("age"), int) else None,
                "generation": persona.get("generation") if isinstance(persona.get("generation"), str) else None,
                "title": persona.get("title") if isinstance(persona.get("title"), str) else None,
                "roleArea": persona.get("roleArea") if isinstance(persona.get("roleArea"), str) else None,
                "personality": persona.get("personality") if isinstance(persona.get("personality"), str) else None,
            }
        )
    return parsed, True


def _parse_nemotron_options(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    options = {}
    if isinstance(value.get("candidateMultiplier"), (int, float)):
        options["candidateMultiplier"] = max(3, min(50, int(round(float(value["candidateMultiplier"])))))
    if isinstance(value.get("sampleLimit"), (int, float)):
        options["sampleLimit"] = max(100, min(50_000, int(round(float(value["sampleLimit"])))))
    return options or None


def validate_generation_payload(payload: dict) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return None, ["Request body must be an object"]

    source_type = payload.get("sourceType")
    if source_type is not None and source_type not in {"service_based", "segment_based"}:
        errors.append("sourceType must be service_based or segment_based")

    locale = payload.get("locale")
    if not isinstance(locale, dict) or not isinstance(locale.get("country"), str) or not isinstance(locale.get("language"), str):
        errors.append("locale.country and locale.language are required")
        locale = None
    else:
        locale = {
            "country": locale["country"].strip().upper(),
            "language": locale["language"].strip().lower(),
            **({"region": locale["region"].strip()} if isinstance(locale.get("region"), str) and locale.get("region").strip() else {}),
        }

    try:
        total_count = int(round(float(payload.get("totalCount"))))
    except Exception:
        total_count = 0
    if total_count < 1 or total_count > 50:
        errors.append("totalCount must be an integer between 1 and 50")

    segment_inputs = payload.get("segmentInputs")
    parsed_segments = None
    if segment_inputs is not None:
        if not isinstance(segment_inputs, list):
            errors.append("segmentInputs must be an array of valid segment objects")
        else:
            parsed_segments = []
            for index, segment in enumerate(segment_inputs):
                if not isinstance(segment, dict):
                    errors.append("segmentInputs must be an array of valid segment objects")
                    break
                try:
                    target_count = int(round(float(segment["targetCount"])))
                    if target_count < 1:
                        raise ValueError("targetCount must be positive")
                    segment_name = str(segment["name"]).strip()
                    segment_description = str(segment["description"]).strip()
                    if len(segment_name) < MIN_SEGMENT_NAME_LENGTH:
                        errors.append(f"segmentInputs[{index}].name must be at least {MIN_SEGMENT_NAME_LENGTH} characters")
                    if len(segment_description) < MIN_SEGMENT_DESCRIPTION_LENGTH:
                        errors.append(f"segmentInputs[{index}].description must be at least {MIN_SEGMENT_DESCRIPTION_LENGTH} characters")
                    parsed_segments.append(
                        {
                            "id": str(segment["id"]).strip(),
                    "name": _normalize_persona_tag(segment_name) or segment_name,
                            "description": segment_description,
                            "targetCount": target_count,
                            **({"criteria": str(segment["criteria"]).strip()} if segment.get("criteria") else {}),
                        }
                    )
                except Exception:
                    errors.append("segmentInputs must be an array of valid segment objects")
                    break

    existing_personas, existing_valid = _parse_existing_personas(payload.get("existingPersonas"))
    if not existing_valid:
        errors.append("existingPersonas must be an array of valid persona summaries")

    service_description = payload.get("serviceDescription")
    service_description = service_description.strip() if isinstance(service_description, str) else None
    target_audience = payload.get("targetAudience")
    target_audience = target_audience.strip() if isinstance(target_audience, str) else None
    inferred_source_type = source_type or ("segment_based" if parsed_segments else "service_based")

    if inferred_source_type == "segment_based" and not parsed_segments:
        errors.append("segmentInputs must be provided when sourceType is segment_based")
    if inferred_source_type == "segment_based" and parsed_segments:
        segment_total_count = sum(segment["targetCount"] for segment in parsed_segments)
        if segment_total_count != total_count:
            errors.append("totalCount must match the sum of segmentInputs.targetCount")
    if inferred_source_type == "service_based" and len(service_description or "") < MIN_SERVICE_DESCRIPTION_LENGTH:
        errors.append(f"serviceDescription must be at least {MIN_SERVICE_DESCRIPTION_LENGTH} characters")

    if errors:
        return None, errors

    return {
        "sourceType": inferred_source_type,
        "serviceDescription": service_description,
        "targetAudience": target_audience,
        "segmentInputs": parsed_segments,
        "totalCount": total_count,
        "locale": locale,
        "includeImages": payload.get("includeImages") is not False,
        "skipExistingPersonas": payload.get("skipExistingPersonas") is True,
        "existingPersonas": existing_personas,
        "generationMode": "nemotron_seed_telecom_polished",
        "nemotronSeedOptions": _parse_nemotron_options(payload.get("nemotronSeedOptions")),
        **({"seed": payload["seed"]} if "seed" in payload else {}),
    }, []


def validate_segment_suggestion_payload(payload: dict) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return None, ["Request body must be an object"]

    context = payload.get("context")
    context = context.strip() if isinstance(context, str) else ""
    if len(context) < 10:
        errors.append("세그먼트 초안 생성을 위해 최소 10자 이상의 컨텍스트가 필요합니다.")
    if len(context) > SEGMENT_SUGGESTION_MAX_CONTEXT_LENGTH:
        errors.append("컨텍스트는 4000자 이내여야 합니다.")

    locale = payload.get("locale")
    if not isinstance(locale, dict) or not isinstance(locale.get("country"), str) or not isinstance(locale.get("language"), str):
        errors.append("locale.country and locale.language are required")
        locale = None
    else:
        locale = {
            "country": locale["country"].strip().upper(),
            "language": locale["language"].strip().lower(),
            **({"region": locale["region"].strip()} if isinstance(locale.get("region"), str) and locale.get("region").strip() else {}),
        }

    max_segments = payload.get("maxSegments")
    if max_segments is None:
        max_segments = SEGMENT_SUGGESTION_DEFAULT_MAX_SEGMENTS
    try:
        max_segments = int(round(float(max_segments)))
    except Exception:
        errors.append("maxSegments must be an integer between 2 and 6")
        max_segments = SEGMENT_SUGGESTION_DEFAULT_MAX_SEGMENTS
    max_segments = max(2, min(6, max_segments))

    if errors:
        return None, errors

    return {
        "context": context,
        "locale": locale,
        "maxSegments": max_segments,
    }, []


def _repair_missing_json_commas(text: str) -> str:
    repaired = text
    for _ in range(3):
        previous = repaired
        repaired = re.sub(r'([}\]"])\s*\n\s*("[^"\n]+"\s*:)', r"\1,\n\2", repaired)
        repaired = re.sub(r'([}\]"])\s*\n\s*([{\[])', r"\1,\n\2", repaired)
        if repaired == previous:
            break
    return repaired


def _json_loads_with_inserted_commas(text: str, *, max_repairs: int = 24) -> dict:
    repaired = text
    seen_positions: set[int] = set()
    for _ in range(max_repairs):
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            if "Expecting ',' delimiter" not in exc.msg:
                raise
            if exc.pos in seen_positions:
                raise
            seen_positions.add(exc.pos)
            insert_at = exc.pos
            if repaired[:insert_at].rstrip().endswith(","):
                raise
            repaired = f"{repaired[:insert_at]},{repaired[insert_at:]}"
    return json.loads(repaired)


def _json_extract(text: str) -> dict:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        stripped = stripped[start:end + 1]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        escaped = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", stripped)
        candidates = [
            escaped,
            _repair_missing_json_commas(stripped),
            _repair_missing_json_commas(escaped),
        ]
        last_error = exc
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as candidate_exc:
                last_error = candidate_exc
            try:
                return _json_loads_with_inserted_commas(candidate)
            except json.JSONDecodeError as candidate_exc:
                last_error = candidate_exc
        raise last_error


def _trim_context(context: str, max_length: int) -> str:
    normalized = context.strip()
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length].rstrip()}\n\n[Context truncated for segment generation]"


def _segment_suggestion_prompt(payload: dict) -> str:
    locale = payload.get("locale") or {}
    max_segments = max(2, min(int(payload.get("maxSegments") or SEGMENT_SUGGESTION_DEFAULT_MAX_SEGMENTS), 6))
    context = _trim_context(payload.get("context") or "", 1800)
    return f"""
STAGE: segment_suggestion
You are an expert in market segmentation and persona discovery.

Analyze the provided context and propose distinct, actionable customer segments.

Rules:
- Segments must be clearly differentiated
- Segment names should be concise
- Descriptions should be short and specific
- Criteria should be short keyword-style text
- target_count is optional and should usually be 1-3
- Generate between 2 and {max_segments} segments
- Generate all text in {locale.get("language") or "ko"}
- Respond with JSON only

Return this JSON shape:
{{
  "segments": [
    {{
      "name": "string",
      "description": "string",
      "criteria": "string",
      "target_count": 1
    }}
  ]
}}

Context:
{context}

Locale:
- Country: {locale.get("country") or "KR"}
- Language: {locale.get("language") or "ko"}
""".strip()


def _normalize_segment_suggestions(raw_segments: list, *, max_segments: int) -> list[dict]:
    normalized = []
    for index, segment in enumerate(raw_segments):
        if not isinstance(segment, dict):
            continue
        name = str(segment.get("name") or "").strip()
        description = str(segment.get("description") or "").strip()
        if not name or not description:
            continue
        criteria = str(segment.get("criteria") or "").strip()
        target_count = _coerce_count(segment.get("target_count") or segment.get("targetCount"), default=1)
        normalized.append(
            {
                "id": f"segment-suggested-{index + 1}",
                "name": name[:100],
                "description": description[:500],
                "criteria": criteria[:2000],
                "targetCount": max(1, min(10, target_count)),
            }
        )
        if len(normalized) >= max_segments:
            break
    return normalized


def generate_segment_suggestions_pipeline(payload: dict, text_generator: Callable[[str], tuple[str, dict]]) -> tuple[list[dict], dict]:
    max_segments = max(2, min(int(payload.get("maxSegments") or SEGMENT_SUGGESTION_DEFAULT_MAX_SEGMENTS), 6))

    def validate(parsed: dict):
        segments = parsed.get("segments")
        if not isinstance(segments, list) or len(segments) < 2:
            raise ValueError("segments missing or below requested count")

    parsed, usage = _execute_json_stage(
        text_generator,
        _segment_suggestion_prompt(payload),
        stage_name="segment_suggestion",
        validator=validate,
    )
    segments = _normalize_segment_suggestions(parsed.get("segments") or [], max_segments=max_segments)
    if len(segments) < 2:
        raise PersonaGenerationQualityError("segment_suggestion returned fewer than 2 usable segments")
    return segments, usage


def _usage_from(raw: dict | None) -> dict:
    raw = raw or {}
    input_tokens = int(raw.get("prompt_tokens") or raw.get("inputTokens") or 0)
    output_tokens = int(raw.get("completion_tokens") or raw.get("outputTokens") or 0)
    total_tokens = int(raw.get("total_tokens") or raw.get("totalTokens") or input_tokens + output_tokens)
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "model": raw.get("model") or DEFAULT_TEXT_MODEL,
    }


def _empty_usage() -> dict:
    return {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "model": DEFAULT_TEXT_MODEL}


def _add_usage(total: dict, usage: dict | None):
    usage = _usage_from(usage)
    total["inputTokens"] += usage["inputTokens"]
    total["outputTokens"] += usage["outputTokens"]
    total["totalTokens"] += usage["totalTokens"]
    total["model"] = usage.get("model") or total.get("model") or DEFAULT_TEXT_MODEL


def _normalize_text(value) -> str:
    return re.sub(r"[^\w\s-]", " ", str(value or "").lower())


def _tokenize(value) -> set[str]:
    return {token for token in _normalize_text(value).split() if len(token) >= 2 and token not in TOKEN_STOPWORDS}


def _count_token_overlap(query_tokens: set[str], text: str | None) -> int:
    if not query_tokens or not text:
        return 0
    target_tokens = _tokenize(text)
    return sum(1 for token in query_tokens if token in target_tokens)


def _combined_seed_text(seed: dict) -> str:
    return "\n".join(
        str(seed.get(key) or "")
        for key in (
            "persona",
            "professional_persona",
            "family_persona",
            "cultural_background",
            "skills_and_expertise",
            "hobbies_and_interests",
            "career_goals_and_ambitions",
            "occupation",
            "family_type",
            "housing_type",
            "education_level",
            "province",
            "district",
        )
        if seed.get(key)
    )


def _normalize_gender(value) -> str:
    normalized = str(value or "").lower()
    if re.search(r"female|woman|여", normalized):
        return "여자"
    if re.search(r"male|man|남", normalized):
        return "남자"
    return ""


def _normalize_seed_occupation_title(value) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"^(그 외|그 밖의|기타)\s+", "", str(value).strip())
    normalized = re.sub(r"\s+및\s+", "·", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized or None


def _to_generation(age) -> str | None:
    if not isinstance(age, int):
        return None
    if age <= 28:
        return "gen_z"
    if age <= 44:
        return "millennial"
    if age <= 60:
        return "gen_x"
    return "baby_boomer"


def _infer_seed_occupation_metadata(occupation) -> dict:
    value = str(occupation or "").strip()
    if not value or re.search(r"무직|학생|주부|은퇴|퇴직", value):
        return {"sector": None, "roleArea": None, "organisation": None, "roleLevel": None}

    rules = [
        (r"소프트웨어|컴퓨터|시스템|프로그래|개발|데이터|ICT|정보통신", "IT/소프트웨어", "시스템/소프트웨어 개발"),
        (r"마케팅|광고|브랜드|홍보", "마케팅/브랜드", "마케팅 전략/캠페인"),
        (r"변리사|변호사|법률|법무", "법률/전문 서비스", "법률/지식재산 자문"),
        (r"컨설턴트|컨설팅", "경영/컨설팅", "경영 자문/전략"),
        (r"기업\s*고위|고위\s*임원|임원|대표이사|최고경영|본부장", "경영/임원", "기업 경영/의사결정"),
        (r"보험", "금융/보험", "보험/상품 중개"),
        (r"공공행정|공무원|행정", "공공행정", "행정/문서 관리"),
        (r"회계|경리|세무|금융|재무", "금융/회계", "회계/재무 관리"),
        (r"경영 기획|기획|관리 사무원|자재 관리|총무", "경영/사무", "기획/운영 관리"),
        (r"디자이너|그래픽|디자인", "디자인/콘텐츠", "디자인/콘텐츠 제작"),
        (r"판매|영업|상담|서비스", "판매/서비스", "고객/판매 서비스"),
        (r"조리|음식|급식|요리", "음식/외식 서비스", "조리/식음 서비스"),
        (r"운전|버스|택시|배송|물류", "운송/물류", "운송/현장 운영"),
        (r"안전|건설|제조|품질|생산", "제조/건설", "현장/품질/안전 관리"),
        (r"교육|교사|강사|학원", "교육", "교육/학습 운영"),
    ]
    for pattern, sector, role_area in rules:
        if re.search(pattern, value):
            return {"sector": sector, "roleArea": role_area, "organisation": None, "roleLevel": None}
    return {"sector": None, "roleArea": _normalize_seed_occupation_title(value), "organisation": None, "roleLevel": None}


def _is_life_role_income_context(*values) -> bool:
    return any(re.search(r"주부|전업|가정|육아|돌봄|보호자|학생|대학생|취준|구직|은퇴|실버|가사|가족|무직", str(value or "")) for value in values)


def _estimate_annual_income_won(*, age=None, generation=None, title=None, role_level=None, sector=None, current_city=None, income_level_hint=None) -> int:
    estimate = {"low": 32_000_000, "middle": 48_000_000, "upper_middle": 68_000_000, "high": 96_000_000}.get(income_level_hint or "middle", 48_000_000)
    if re.search(r"기업\s*고위|고위\s*임원|임원|대표이사|최고경영|c[-\s]?level|executive|vp|본부장", str(title or "") + " " + str(role_level or ""), re.IGNORECASE):
        estimate = max(estimate, 120_000_000)
    role_level_text = str(role_level or "").lower()
    if re.search(r"intern|entry|주니어|신입", role_level_text):
        estimate -= 12_000_000
    elif re.search(r"senior|lead|manager|팀장|리드|매니저", role_level_text):
        estimate += 12_000_000
    elif re.search(r"director|head|executive|vp|c-level|임원|대표|본부장", role_level_text):
        estimate += 30_000_000
    sector_text = str(sector or "").lower()
    if re.search(r"it|tech|software|ai|핀테크|금융|finance", sector_text):
        estimate += 6_000_000
    elif re.search(r"public|education|ngo|비영리|교육|공공", sector_text):
        estimate -= 4_000_000
    if re.search(r"seoul|판교|강남|성수|서울", str(current_city or "").lower()):
        estimate += 4_000_000
    if isinstance(age, int):
        if age <= 27:
            estimate = min(estimate, 40_000_000)
        elif age >= 45:
            estimate += 4_000_000
    elif str(generation or "").lower() == "gen_z":
        estimate = min(estimate, 40_000_000)
    elif str(generation or "").lower() == "gen_x":
        estimate += 4_000_000
    floor = 120_000_000 if estimate >= 120_000_000 else 24_000_000
    return max(floor, round(estimate / 1_000_000) * 1_000_000)


def _format_won_amount(amount: int) -> str:
    return f"{int(round(amount)):,}원"


def _normalize_persona_income(value=None, *, allow_estimate=False, country="KR", age=None, generation=None, title=None, organisation=None, role_area=None, role_level=None, sector=None, current_city=None, income_level_hint=None) -> str | None:
    if country and country != "KR":
        return None
    if _is_life_role_income_context(title, organisation, role_area, role_level, sector):
        return None
    text = str(value or "").strip()
    if text:
        compact = re.sub(r"\s+", "", text)
        plain = re.match(r"^([0-9][0-9,]*)원?$", compact)
        if plain:
            return _format_won_amount(int(plain.group(1).replace(",", "")))
        man = re.search(r"([0-9][0-9,\.]*)만", compact)
        if man:
            amount = float(man.group(1).replace(",", "")) * 10_000
            if re.search(r"월급|월소득|월수입|monthly|month|매달|한달|한 달|월", compact, re.IGNORECASE) and not re.search(r"연봉|연소득|annual|yearly", compact, re.IGNORECASE):
                amount *= 12
            return _format_won_amount(int(amount))
    if not allow_estimate:
        return None
    return _format_won_amount(
        _estimate_annual_income_won(
            age=age,
            generation=generation,
            title=title,
            role_level=role_level,
            sector=sector,
            current_city=current_city,
            income_level_hint=income_level_hint,
        )
    )


def _score_seed(*, payload: dict, segment: dict, profile: dict, seed: dict) -> int:
    query = "\n".join(
        str(value)
        for value in [
            payload.get("serviceDescription"),
            payload.get("targetAudience"),
            segment.get("name"),
            segment.get("description"),
            " ".join((segment.get("characteristics") or {}).get("keyTraits") or []),
            " ".join((segment.get("characteristics") or {}).get("occupationHint") or []),
            (segment.get("characteristics") or {}).get("ageRangeHint"),
            profile.get("title"),
            profile.get("gender"),
            profile.get("currentCity"),
            profile.get("roleArea"),
        ]
        if value
    )
    query_tokens = _tokenize(query)
    score = _count_token_overlap(query_tokens, _combined_seed_text(seed)) * 3
    if isinstance(profile.get("age"), int) and isinstance(seed.get("age"), int):
        score += max(0, 16 - abs(profile["age"] - seed["age"]))
    if _normalize_gender(profile.get("gender")) and seed.get("sex") == _normalize_gender(profile.get("gender")):
        score += 8
    occupation_hints = " ".join(
        str(value)
        for value in [
            profile.get("title"),
            profile.get("roleArea"),
            *((segment.get("characteristics") or {}).get("occupationHint") or []),
        ]
        if value
    )
    score += _count_token_overlap(_tokenize(occupation_hints), seed.get("occupation")) * 6
    if profile.get("currentCity") and any(profile["currentCity"] in str(value or "") for value in [seed.get("province"), seed.get("district")]):
        score += 5
    return score


def _diversity_key(seed: dict) -> str:
    age = seed.get("age")
    age_bucket = f"{int(age / 10) * 10}대" if isinstance(age, int) else "unknown"
    return "|".join([age_bucket, *(str(seed.get(key) or "") for key in ("sex", "occupation", "family_type", "province"))])


def _identity_key(seed: dict) -> str:
    return f"{seed.get('age') or 'unknown'}|{_normalize_seed_occupation_title(seed.get('occupation')) or seed.get('occupation') or ''}"


def _parse_korean_name(seed: dict, index: int = 0) -> str:
    source = str(seed.get("persona") or seed.get("professional_persona") or "")
    match = re.match(r"^([가-힣]{2,4})\s*(?:씨는|님은|\s)", source)
    if match:
        return match.group(1)
    return str(seed.get("name") or f"네모트론{str(seed.get('uuid') or index)[:4]}")


def _ensure_unique_name(name: str, existing_personas: Iterable[dict], selected_names: Iterable[str]) -> str:
    used = {
        str(item.get("name", "")).strip().lower()
        for item in existing_personas
        if isinstance(item, dict) and item.get("name")
    }
    used.update(str(value).strip().lower() for value in selected_names if value)
    if name.lower() not in used:
        return name
    counter = 2
    candidate = f"{name} ({counter})"
    while candidate.lower() in used:
        counter += 1
        candidate = f"{name} ({counter})"
    return candidate


def _segments_for_payload(payload: dict) -> list[dict]:
    segment_inputs = payload.get("segmentInputs")
    if isinstance(segment_inputs, list) and segment_inputs:
        return [
            {
                "id": segment["id"],
                "name": segment["name"],
                "description": segment["description"],
                "targetCount": segment["targetCount"],
                "characteristics": {
                    "keyTraits": [],
                    "ageRangeHint": "",
                    "occupationHint": [],
                },
            }
            for segment in segment_inputs
        ]
    return [
        {
            "id": "service_based",
            "name": "Service based",
            "description": payload.get("serviceDescription") or "",
            "targetCount": payload["totalCount"],
            "characteristics": {
                "keyTraits": [],
                "ageRangeHint": "",
                "occupationHint": [],
            },
        }
    ]


def _fallback_profiles(payload: dict, segments: list[dict]) -> list[dict]:
    profiles = []
    for segment in segments:
        for _ in range(max(1, int(segment.get("targetCount") or 1))):
            profiles.append(
                {
                    "segmentId": segment["id"],
                    "name": "",
                    "title": None,
                    "age": None,
                    "gender": None,
                    "generation": None,
                    "currentCity": None,
                    "currentCountry": payload.get("locale", {}).get("country"),
                    "sector": None,
                    "organisation": None,
                    "roleArea": None,
                    "roleLevel": None,
                }
            )
    return profiles[: _coerce_count(payload.get("totalCount"))]


def _align_segment_counts(segments: list[dict], profiles: list[dict]) -> list[dict]:
    counts = {}
    for profile in profiles:
        counts[profile.get("segmentId")] = counts.get(profile.get("segmentId"), 0) + 1
    return [{**segment, "targetCount": counts.get(segment["id"], 0)} for segment in segments]


def _normalize_segments(raw_segments: list, payload: dict) -> list[dict]:
    if not raw_segments:
        return _segments_for_payload(payload)
    normalized = []
    for index, segment in enumerate(raw_segments):
        if not isinstance(segment, dict):
            continue
        characteristics = segment.get("characteristics") if isinstance(segment.get("characteristics"), dict) else {}
        normalized.append(
            {
                "id": str(segment.get("id") or f"segment_{index + 1}"),
                "name": _normalize_persona_tag(segment.get("name")) or f"Segment {index + 1}",
                "nameEn": segment.get("name_en") or segment.get("nameEn"),
                "description": str(segment.get("description") or ""),
                "targetCount": _coerce_count(segment.get("target_count") or segment.get("targetCount")),
                "characteristics": {
                    "keyTraits": list(characteristics.get("key_traits") or characteristics.get("keyTraits") or []),
                    "ageRangeHint": str(characteristics.get("age_range_hint") or characteristics.get("ageRangeHint") or ""),
                    "occupationHint": list(characteristics.get("occupation_hint") or characteristics.get("occupationHint") or []),
                    **({"incomeLevelHint": characteristics.get("income_level_hint") or characteristics.get("incomeLevelHint")} if characteristics.get("income_level_hint") or characteristics.get("incomeLevelHint") else {}),
                    **({"urbanRuralHint": characteristics.get("urban_rural_hint") or characteristics.get("urbanRuralHint")} if characteristics.get("urban_rural_hint") or characteristics.get("urbanRuralHint") else {}),
                },
            }
        )
    return normalized or _segments_for_payload(payload)


def _normalize_profiles(raw_profiles: list, segments: list[dict], payload: dict, existing_personas: list[dict]) -> list[dict]:
    segment_ids = {segment["id"] for segment in segments}
    segment_names = {segment["name"]: segment["id"] for segment in segments}
    profiles = []
    for index, profile in enumerate(raw_profiles):
        if not isinstance(profile, dict):
            continue
        segment_id = str(profile.get("segment_id") or profile.get("segmentId") or "")
        if segment_id not in segment_ids:
            segment_id = segment_names.get(segment_id) or segments[min(index, len(segments) - 1)]["id"]
        profiles.append(
            {
                "segmentId": segment_id,
                "name": str(profile.get("name") or "").strip(),
                "title": profile.get("title"),
                "age": int(profile["age"]) if isinstance(profile.get("age"), int) else None,
                "gender": profile.get("gender"),
                "generation": profile.get("generation"),
                "currentCity": profile.get("current_city") or profile.get("currentCity"),
                "currentCountry": profile.get("current_country") or profile.get("currentCountry") or payload.get("locale", {}).get("country"),
                "sector": profile.get("sector"),
                "organisation": profile.get("organisation"),
                "roleArea": profile.get("role_area") or profile.get("roleArea"),
                "roleLevel": profile.get("role_level") or profile.get("roleLevel"),
            }
        )
    profiles = profiles[: _coerce_count(payload.get("totalCount"))]
    seen = []
    for profile in profiles:
        if profile["name"]:
            profile["name"] = _ensure_unique_name(profile["name"], existing_personas, seen)
            seen.append(profile["name"])
    return profiles


def _service_context(payload: dict) -> str:
    if payload.get("serviceDescription"):
        return payload["serviceDescription"]
    segment_inputs = payload.get("segmentInputs") or []
    if segment_inputs:
        return "\n\n".join(
            "\n".join(
                str(value)
                for value in [
                    segment.get("name"),
                    segment.get("description"),
                    segment.get("criteria"),
                ]
                if value
            )
            for segment in segment_inputs
        )
    return payload.get("targetAudience") or ""


def _language_label(payload: dict) -> str:
    language = ((payload.get("locale") or {}).get("language") or "").lower()
    return LANGUAGE_LABELS.get(language) or language or "English"


def _is_likely_telecom_context(*values) -> bool:
    return bool(TELECOM_CONTEXT_PATTERN.search(" ".join(str(value or "") for value in values)))


def _execute_json_stage(
    text_generator: Callable[[str], tuple[str, dict]],
    prompt: str,
    *,
    stage_name: str,
    validator: Callable[[dict], None] | None = None,
    max_attempts: int = 3,
) -> tuple[dict, dict]:
    last_error = None
    for attempt in range(max_attempts):
        retry = "" if attempt == 0 else f"\n\nRetry because the previous {stage_name} response was invalid: {last_error}. Return complete JSON only."
        try:
            content, usage = text_generator(f"{prompt}{retry}")
            parsed = _json_extract(content)
            if validator:
                validator(parsed)
            return parsed, _usage_from(usage)
        except Exception as exc:
            last_error = exc
    raise PersonaGenerationQualityError(f"{stage_name} response was invalid: {last_error}")


def _segmentation_prompt(payload: dict, existing_personas: list[dict]) -> str:
    return f"""
STAGE: segmentation_identity
Return JSON with "segments" and "profiles".
Profiles count must be exactly {payload["totalCount"]}.
For segment_based requests, keep provided segment ids exactly.

Generation input:
{json.dumps(payload, ensure_ascii=False)}

Existing personas to avoid:
{json.dumps(existing_personas, ensure_ascii=False)}
""".strip()


def stage_segmentation_and_identity(payload: dict, existing_personas: list[dict], text_generator: Callable[[str], tuple[str, dict]]) -> tuple[list[dict], list[dict], dict]:
    def validate(parsed: dict):
        if not isinstance(parsed.get("segments"), list) or not parsed["segments"]:
            raise ValueError("segments missing")
        if not isinstance(parsed.get("profiles"), list) or len(parsed["profiles"]) < _coerce_count(payload.get("totalCount")):
            raise ValueError("profiles missing or below requested count")

    parsed, usage = _execute_json_stage(text_generator, _segmentation_prompt(payload, existing_personas), stage_name="segmentation", validator=validate)
    segments = _normalize_segments(parsed.get("segments") or [], payload)
    profiles = _normalize_profiles(parsed.get("profiles") or [], segments, payload, existing_personas)
    requested = _coerce_count(payload.get("totalCount"))
    if len(profiles) != requested:
        raise PersonaGenerationQualityError(f"segmentation returned {len(profiles)} profiles for requested total {requested}")
    return _align_segment_counts(segments, profiles), profiles, usage


def select_nemotron_korea_seeds(
    *,
    payload: dict,
    segments: list[dict],
    profiles: list[dict],
    existing_personas: list[dict],
    seed_path: Path | None = None,
) -> list[dict]:
    options = payload.get("nemotronSeedOptions") or {}
    sample_limit = options.get("sampleLimit") or 50_000
    seeds = load_seed_personas(seed_path, limit=max(1, min(50_000, int(sample_limit))))
    segment_by_id = {segment["id"]: segment for segment in segments}
    candidate_multiplier = int(options.get("candidateMultiplier") or 10)
    candidate_count = max(10, len(profiles) * candidate_multiplier)
    selected = []
    used_uuids = set()
    used_diversity = set()
    used_identity = set()

    for profile in profiles:
        segment = segment_by_id.get(profile.get("segmentId")) or segments[0]
        candidates = sorted(
            (
                {"seed": seed, "score": _score_seed(payload=payload, segment=segment, profile=profile, seed=seed)}
                for seed in seeds
            ),
            key=lambda item: item["score"],
            reverse=True,
        )[:candidate_count]
        ranked = [{**candidate, "rank": index + 1} for index, candidate in enumerate(candidates)]
        best = None
        for candidate in ranked:
            seed = candidate["seed"]
            if seed.get("uuid") not in used_uuids and _diversity_key(seed) not in used_diversity and _identity_key(seed) not in used_identity:
                best = candidate
                break
        if best is None:
            for candidate in ranked:
                seed = candidate["seed"]
                if seed.get("uuid") not in used_uuids and _identity_key(seed) not in used_identity:
                    best = candidate
                    break
        best = best or (ranked[0] if ranked else None)
        if best is None:
            continue
        seed = best["seed"]
        used_uuids.add(seed.get("uuid"))
        used_diversity.add(_diversity_key(seed))
        used_identity.add(_identity_key(seed))
        selected.append({"seed": seed, "profile": profile, "segment": segment, "score": best["score"], "rank": best["rank"]})

    if len(selected) < len(profiles):
        raise ValueError(f"Nemotron seed cache returned {len(selected)}/{len(profiles)} usable seed personas")
    return selected


def map_nemotron_seed_to_persona(selected: dict, existing_personas: list[dict], selected_names: list[str]) -> dict:
    seed = selected["seed"]
    profile = selected["profile"]
    name = _ensure_unique_name(_parse_korean_name(seed), existing_personas, selected_names)
    age = seed.get("age") if isinstance(seed.get("age"), int) else profile.get("age")
    raw_title = seed.get("occupation") or profile.get("title")
    title = _normalize_seed_occupation_title(raw_title)
    city = seed.get("district") or profile.get("currentCity")
    province = seed.get("province")
    country = seed.get("country") or "대한민국"
    metadata = _infer_seed_occupation_metadata(seed.get("occupation"))
    is_paid_worker = not re.search(r"무직|학생|주부|은퇴|퇴직", str(raw_title or ""))
    income = (
        _normalize_persona_income(
            seed.get("income"),
            allow_estimate=True,
            country="KR",
            age=age,
            generation=_to_generation(age),
            title=title,
            organisation=metadata.get("organisation"),
            role_area=metadata.get("roleArea"),
            role_level=metadata.get("roleLevel"),
            sector=metadata.get("sector"),
            current_city=city,
            income_level_hint=(selected.get("segment") or {}).get("characteristics", {}).get("incomeLevelHint"),
        )
        if is_paid_worker
        else None
    )
    return {
        "schemaVersion": 3,
        "name": name,
        "title": title,
        "gender": seed.get("sex") or profile.get("gender"),
        "age": age,
        "income": income,
        "sector": metadata.get("sector"),
        "generation": _to_generation(age),
        "ethnicity": "한국인",
        "currentCity": city,
        "currentCountry": country,
        "locations": [value for value in [province, city] if value],
        "organisation": metadata.get("organisation"),
        "roleArea": metadata.get("roleArea"),
        "roleLevel": metadata.get("roleLevel"),
        "attitudes": seed.get("career_goals_and_ambitions") or seed.get("cultural_background"),
        "biography": "\n\n".join(value for value in [seed.get("persona"), seed.get("professional_persona")] if value),
        "demeanour": seed.get("cultural_background") or seed.get("persona"),
        "interests": "\n\n".join(value for value in [seed.get("hobbies_and_interests"), seed.get("sports_persona"), seed.get("arts_persona"), seed.get("travel_persona"), seed.get("culinary_persona")] if value),
        "behaviours": "\n\n".join(value for value in [seed.get("professional_persona"), seed.get("family_persona"), seed.get("hobbies_and_interests")] if value),
        "motivation": seed.get("career_goals_and_ambitions"),
        "upbringing": seed.get("cultural_background"),
        "personality": seed.get("persona"),
        "preferences": "\n\n".join(value for value in [seed.get("culinary_persona"), seed.get("travel_persona"), seed.get("hobbies_and_interests")] if value),
        "socialContext": seed.get("family_persona"),
        "culturalBackground": seed.get("cultural_background"),
        "telecomUsage": {},
        "telecomValues": {},
        "uxInteraction": {},
        "telecomBehaviorDimensions": {},
        "quote": seed.get("persona"),
        "imagePrompt": " ".join(str(value) for value in ["Photorealistic Korean user persona profile portrait.", age, seed.get("sex"), title, city] if value),
        "imageUrl": None,
    }


def _seed_metadata(selected: dict, persona_name: str) -> dict:
    seed = selected["seed"]
    return {
        "personaName": persona_name,
        "seedUuid": seed.get("uuid") or "",
        "sourcePersona": seed.get("persona") or "",
        "age": seed.get("age") or 0,
        "sex": seed.get("sex") or "",
        "occupation": seed.get("occupation") or "",
        "province": seed.get("province") or "",
        "district": seed.get("district") or "",
        "familyType": seed.get("family_type") or "",
        "housingType": seed.get("housing_type") or "",
        "educationLevel": seed.get("education_level") or "",
        "score": selected.get("score") or 0,
        "rank": selected.get("rank") or 0,
    }


def _narrative_prompt(persona: dict, payload: dict, segment: dict, seed: dict) -> str:
    return f"""
STAGE: narrative_polish
Rewrite and enrich one GeneratedPersona. Return JSON with either "persona" or the persona fields.
Required narrative fields: attitudes, biography, demeanour, interests, behaviours, motivation, upbringing,
personality, preferences, socialContext, culturalBackground, quote, imagePrompt.
Do not omit required fields. If a detail is not explicit, infer a concrete value from the draft persona, segment, and Nemotron seed.

Generation input:
{json.dumps(payload, ensure_ascii=False)}

Segment:
{json.dumps(segment, ensure_ascii=False)}

Draft persona:
{json.dumps(persona, ensure_ascii=False)}

Nemotron seed:
{json.dumps(seed, ensure_ascii=False)}
""".strip()


def _has_text(value, *, min_length: int = 8) -> bool:
    return isinstance(value, str) and len(value.strip()) >= min_length


def _validate_narrative(persona: dict):
    missing = [field for field in NARRATIVE_REQUIRED_FIELDS if not _has_text(persona.get(field))]
    if missing:
        raise PersonaGenerationQualityError(f"narrative fields missing: {', '.join(missing)}")


def _merge_narrative_update(persona: dict, updates: dict) -> dict:
    merged = {**persona}
    for key, value in (updates or {}).items():
        if key in NARRATIVE_REQUIRED_FIELDS and not _has_text(value):
            continue
        merged[key] = value
    merged["schemaVersion"] = 3
    return merged


def stage_nemotron_seed_narrative_polish(persona: dict, payload: dict, segment: dict, seed: dict, text_generator: Callable[[str], tuple[str, dict]]) -> tuple[dict, dict]:
    def validate(parsed: dict):
        candidate = parsed.get("persona") if isinstance(parsed.get("persona"), dict) else parsed
        _validate_narrative(_merge_narrative_update(persona, candidate))

    parsed, usage = _execute_json_stage(text_generator, _narrative_prompt(persona, payload, segment, seed), stage_name="narrative", validator=validate)
    updates = parsed.get("persona") if isinstance(parsed.get("persona"), dict) else parsed
    merged = _merge_narrative_update(persona, updates)
    _validate_narrative(merged)
    return merged, usage


def _telecom_prompt(persona: dict, payload: dict, segment: dict, seed: dict) -> str:
    target_language = _language_label(payload)
    return f"""
STAGE: telecom_dimensions
You are an expert telecom UX researcher.

## Task
Generate telecom behavior dimensions and numeric behavior scores for a fixed persona.

## Critical Rules
1. Do not rewrite the persona identity or narrative.
2. Infer telecom behavior from the persona's life context, household situation, job habits, and service context.
3. Make each field concrete and behavior-based.
4. Avoid generic labels. Write 1-3 natural-language sentences per dimension field.
5. Score each of the four score groups from 1 to 5 based on the generated dimensions and fixed persona evidence.
6. Score semantics: 1 = very low, 2 = low, 3 = moderate, 4 = high, 5 = very high.
7. Each score must include a concise rationale and 1-3 evidence snippets grounded in the fixed persona or generated dimensions.
8. Generate all text in {target_language}.
9. Respond with JSON only.

## Output Format
{{
  "telecom_behavior_dimensions": {{
    "brandRetention": {{
      "brandRetentionTendency": "How strongly they keep the current carrier or brand",
      "premiumInfraBenefitOrientation": "How much they value premium network quality and bundled benefits"
    }},
    "optimizationResource": {{
      "optimizationResourceInvestment": "How they trade money against time and effort for plan optimization",
      "paymentResistanceLine": "The payment level or condition where they start resisting extra cost"
    }},
    "informationControl": {{
      "informationExplorationStyle": "How they explore and compare telecom information",
      "problemSolvingAutonomy": "How independently they solve telecom service problems"
    }},
    "digitalAiOpenness": {{
      "aiProviderTrust": "Trust in AI and provider recommendations",
      "personalizationDataSharingScope": "What information they will share for personalization"
    }},
    "telecomLifeCharacteristics": {{
      "householdDecisionLeadership": "Household structure and telecom decision leadership",
      "productServiceUnderstanding": "How well they understand telecom products and service conditions",
      "telecomServiceUsageContext": "Lived context of how they use, manage, and adjust telecom services"
    }}
  }},
  "telecom_behavior_scores": [
    {{
      "key": "brandRetention",
      "label": "브랜드 유지 성향",
      "score": 4,
      "maxScore": 5,
      "rationale": "Why this persona scores this way",
      "evidence": ["Specific supporting signal from persona or dimensions"]
    }},
    {{
      "key": "optimizationResource",
      "label": "최적화 리소스 투입",
      "score": 2,
      "maxScore": 5,
      "rationale": "Why this persona scores this way",
      "evidence": ["Specific supporting signal from persona or dimensions"]
    }},
    {{
      "key": "informationControl",
      "label": "정보탐색 및 통제 욕구",
      "score": 3,
      "maxScore": 5,
      "rationale": "Why this persona scores this way",
      "evidence": ["Specific supporting signal from persona or dimensions"]
    }},
    {{
      "key": "digitalAiOpenness",
      "label": "디지털 및 AI 개방성",
      "score": 5,
      "maxScore": 5,
      "rationale": "Why this persona scores this way",
      "evidence": ["Specific supporting signal from persona or dimensions"]
    }}
  ]
}}

## Fixed Persona
{json.dumps({
        "name": persona.get("name"),
        "age": persona.get("age"),
        "gender": persona.get("gender"),
        "title": persona.get("title"),
        "income": persona.get("income"),
        "current_city": persona.get("currentCity"),
        "current_country": persona.get("currentCountry"),
        "biography": persona.get("biography"),
        "attitudes": persona.get("attitudes"),
        "behaviours": persona.get("behaviours"),
        "motivation": persona.get("motivation"),
        "personality": persona.get("personality"),
        "preferences": persona.get("preferences"),
        "social_context": persona.get("socialContext"),
        "cultural_background": persona.get("culturalBackground"),
    }, ensure_ascii=False, indent=2)}

## Selected Segment
- Segment: {segment.get("name")}
- Description: {segment.get("description")}
- Key Traits: {", ".join((segment.get("characteristics") or {}).get("keyTraits") or [])}

## Service Context
{_service_context(payload)}

## Dataset Seed Context
{json.dumps(seed, ensure_ascii=False, indent=2)}

Generate the 11 telecom behavior variables and the 4 telecom behavior scores. Keep the fixed persona unchanged.
The telecomServiceUsageContext field should be experience-based and written in 4-5 sentences.
""".strip()


def _normalize_telecom_dimensions(value) -> dict:
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for group, fields in TELECOM_DIMENSION_GROUPS.items():
        source = value.get(group) if isinstance(value.get(group), dict) else {}
        normalized[group] = {field: source.get(field) for field in fields if source.get(field)}
    return normalized


def _validate_telecom_dimensions(value: dict):
    missing = []
    for group, fields in TELECOM_DIMENSION_GROUPS.items():
        source = value.get(group) if isinstance(value.get(group), dict) else {}
        for field in fields:
            if not _has_text(source.get(field), min_length=4):
                missing.append(f"{group}.{field}")
    if missing:
        raise PersonaGenerationQualityError(f"telecom dimensions missing: {', '.join(missing)}")


def _normalize_telecom_scores(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    by_key = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key not in TELECOM_SCORE_GROUPS:
            continue
        try:
            score = int(item.get("score"))
        except Exception:
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            evidence = item.get("evidenceSnippets") or item.get("evidence_snippets") or []
        by_key[key] = {
            "key": key,
            "label": item.get("label") or TELECOM_SCORE_GROUPS[key],
            "score": max(1, min(5, score)),
            "maxScore": 5,
            "rationale": str(item.get("rationale") or item.get("basis") or "").strip(),
            "evidence": [str(entry).strip() for entry in evidence if str(entry).strip()][:3],
        }
    return [by_key[key] for key in TELECOM_SCORE_GROUPS if key in by_key]


def _validate_telecom_scores(value: list[dict]):
    by_key = {item.get("key"): item for item in value if isinstance(item, dict)}
    missing = []
    invalid = []
    for key in TELECOM_SCORE_GROUPS:
        item = by_key.get(key)
        if not item:
            missing.append(key)
            continue
        if not isinstance(item.get("score"), int) or not 1 <= item["score"] <= 5:
            invalid.append(f"{key}.score")
        if not _has_text(item.get("rationale"), min_length=8):
            invalid.append(f"{key}.rationale")
        if not item.get("evidence"):
            invalid.append(f"{key}.evidence")
    if missing or invalid:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if invalid:
            details.append(f"invalid: {', '.join(invalid)}")
        raise PersonaGenerationQualityError(f"telecom scores invalid: {'; '.join(details)}")


def _telecom_signal_score(context: str, positive_terms: Iterable[str], negative_terms: Iterable[str] = ()) -> int:
    positive = sum(1 for term in positive_terms if term and term.lower() in context)
    negative = sum(1 for term in negative_terms if term and term.lower() in context)
    return max(1, min(5, 3 + min(2, positive) - min(2, negative)))


def _fallback_telecom_dimensions_and_scores(persona: dict, payload: dict, segment: dict, seed: dict) -> tuple[dict, list[dict]]:
    context = " ".join(
        str(value or "")
        for value in [
            persona.get("name"),
            persona.get("title"),
            persona.get("biography"),
            persona.get("attitudes"),
            persona.get("behaviours"),
            persona.get("motivation"),
            persona.get("personality"),
            persona.get("preferences"),
            persona.get("socialContext"),
            persona.get("culturalBackground"),
            segment.get("name"),
            segment.get("description"),
            _service_context(payload),
            json.dumps(seed, ensure_ascii=False),
        ]
    ).lower()
    name = persona.get("name") or "이 퍼소나"
    segment_name = segment.get("name") or "해당 세그먼트"
    brand_score = _telecom_signal_score(context, ["장기", "결합", "가족", "멤버십", "프리미엄", "안정", "신뢰"], ["알뜰", "번호이동", "변경", "절약"])
    optimization_score = _telecom_signal_score(context, ["비교", "절약", "최적화", "알뜰", "요금", "커뮤니티", "후기"], ["귀찮", "시간 부족", "복잡"])
    information_score = _telecom_signal_score(context, ["검색", "비교", "리뷰", "후기", "커뮤니티", "앱", "직접"], ["대리점", "상담원에게 맡"])
    ai_score = _telecom_signal_score(context, ["ai", "앱", "디지털", "추천", "자동", "개인화", "데이터"], ["불신", "개인정보", "꺼림"])
    dimensions = {
        "brandRetention": {
            "brandRetentionTendency": f"{name}은 {segment_name} 맥락에서 현재 통신사의 안정성과 변경 비용을 함께 고려한다.",
            "premiumInfraBenefitOrientation": "네트워크 품질, 멤버십, 결합 혜택이 실제 생활비나 편의로 연결될 때 프리미엄 혜택을 긍정적으로 본다.",
        },
        "optimizationResource": {
            "optimizationResourceInvestment": "요금제와 혜택을 비교하되, 지나치게 복잡한 조건에는 많은 시간을 쓰지 않으려는 실용적 태도를 보인다.",
            "paymentResistanceLine": "월 납부액이 체감 혜택보다 높거나 가족 및 생활비 관리 기준을 넘어서면 추가 지출에 저항한다.",
        },
        "informationControl": {
            "informationExplorationStyle": "공식 앱, 요금제 안내, 주변 경험, 온라인 후기를 조합해 필요한 정보만 선별하려는 경향이 있다.",
            "problemSolvingAutonomy": "기본적인 요금제 확인과 혜택 점검은 직접 처리하지만, 약정이나 결합처럼 복잡한 문제는 상담을 병행한다.",
        },
        "digitalAiOpenness": {
            "aiProviderTrust": "개인 상황을 정확히 반영하고 근거를 설명하는 추천에는 개방적이지만, 불투명한 상향 판매성 추천은 경계한다.",
            "personalizationDataSharingScope": "요금, 데이터 사용량, 결합 여부처럼 추천에 직접 필요한 범위의 데이터 공유를 선호한다.",
        },
        "telecomLifeCharacteristics": {
            "householdDecisionLeadership": "본인의 회선뿐 아니라 생활 패턴과 가구 상황을 기준으로 통신 결정을 조율한다.",
            "productServiceUnderstanding": "핵심 요금, 데이터 제공량, 약정 조건은 이해하지만 세부 할인 구조는 필요할 때 확인하는 수준이다.",
            "telecomServiceUsageContext": f"{name}은 일상적인 모바일 데이터 사용과 통신비 관리를 생활 관리의 일부로 다룬다. 요금제 변경은 즉흥적으로 하기보다 현재 혜택, 데이터 사용량, 약정 조건을 확인한 뒤 판단한다. 불편이 생기면 먼저 앱이나 안내 페이지를 확인하고, 조건이 복잡하면 상담 채널을 활용한다. {segment_name}의 특성상 가격, 안정성, 편의성 사이의 균형을 중요하게 본다.",
        },
    }
    scores = [
        {
            "key": "brandRetention",
            "label": TELECOM_SCORE_GROUPS["brandRetention"],
            "score": brand_score,
            "maxScore": 5,
            "rationale": "현재 이용 안정성과 변경 비용을 함께 고려하는 성향을 기준으로 산정했다.",
            "evidence": [dimensions["brandRetention"]["brandRetentionTendency"]],
        },
        {
            "key": "optimizationResource",
            "label": TELECOM_SCORE_GROUPS["optimizationResource"],
            "score": optimization_score,
            "maxScore": 5,
            "rationale": "요금, 혜택, 비교 행동에 투자하는 시간과 노력을 기준으로 산정했다.",
            "evidence": [dimensions["optimizationResource"]["optimizationResourceInvestment"]],
        },
        {
            "key": "informationControl",
            "label": TELECOM_SCORE_GROUPS["informationControl"],
            "score": information_score,
            "maxScore": 5,
            "rationale": "직접 탐색과 문제 해결 자율성의 강도를 기준으로 산정했다.",
            "evidence": [dimensions["informationControl"]["informationExplorationStyle"]],
        },
        {
            "key": "digitalAiOpenness",
            "label": TELECOM_SCORE_GROUPS["digitalAiOpenness"],
            "score": ai_score,
            "maxScore": 5,
            "rationale": "앱, AI 추천, 개인화 데이터 공유에 대한 개방성을 기준으로 산정했다.",
            "evidence": [dimensions["digitalAiOpenness"]["aiProviderTrust"]],
        },
    ]
    return dimensions, scores


def stage_nemotron_telecom_dimensions(persona: dict, payload: dict, segment: dict, seed: dict, text_generator: Callable[[str], tuple[str, dict]]) -> tuple[dict, dict]:
    def validate(parsed: dict):
        dimensions = _normalize_telecom_dimensions(parsed.get("telecomBehaviorDimensions") or parsed.get("telecom_behavior_dimensions"))
        scores = _normalize_telecom_scores(parsed.get("telecomBehaviorScores") or parsed.get("telecom_behavior_scores"))
        _validate_telecom_dimensions(dimensions)
        _validate_telecom_scores(scores)

    try:
        parsed, usage = _execute_json_stage(text_generator, _telecom_prompt(persona, payload, segment, seed), stage_name="telecom_dimensions", validator=validate)
        dimensions = _normalize_telecom_dimensions(parsed.get("telecomBehaviorDimensions") or parsed.get("telecom_behavior_dimensions"))
        scores = _normalize_telecom_scores(parsed.get("telecomBehaviorScores") or parsed.get("telecom_behavior_scores"))
    except PersonaGenerationQualityError as exc:
        _log_persona_generation_event("telecom_dimensions_fallback", reason=str(exc))
        dimensions, scores = _fallback_telecom_dimensions_and_scores(persona, payload, segment, seed)
        usage = _usage_from({"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "fallback": True})
    return {
        **persona,
        "telecomBehaviorDimensions": dimensions,
        "telecomBehaviorScores": scores,
    }, usage


def _interview_prompt(persona: dict, payload: dict) -> str:
    return f"""
STAGE: interview_reference
Choose the closest telecom interview reference type and rewrite only telecomServiceUsageContext.
Return JSON with selected_type, source_user_ids, reference_strength, rationale, telecom_service_usage_context.

References:
{json.dumps(TELECOM_INTERVIEW_REFERENCES, ensure_ascii=False)}

Service context:
{_service_context(payload)}

Persona:
{json.dumps(persona, ensure_ascii=False)}
""".strip()


def regenerate_telecom_service_usage_context_from_interview_reference(persona: dict, payload: dict, text_generator: Callable[[str], tuple[str, dict]]) -> tuple[dict, dict | None, dict]:
    def validate(parsed: dict):
        if not _has_text(parsed.get("telecom_service_usage_context") or parsed.get("telecomServiceUsageContext"), min_length=20):
            raise PersonaGenerationQualityError("telecom_service_usage_context missing")

    parsed, usage = _execute_json_stage(text_generator, _interview_prompt(persona, payload), stage_name="interview_reference", validator=validate)
    context = parsed.get("telecom_service_usage_context") or parsed.get("telecomServiceUsageContext")
    dimensions = dict(persona.get("telecomBehaviorDimensions") or {})
    life = dict(dimensions.get("telecomLifeCharacteristics") or {})
    life["telecomServiceUsageContext"] = context
    dimensions["telecomLifeCharacteristics"] = life
    metadata = {
        "personaName": persona.get("name"),
        "selectedType": parsed.get("selected_type") or parsed.get("selectedType"),
        "sourceUserIds": parsed.get("source_user_ids") or parsed.get("sourceUserIds") or [],
        "referenceStrength": parsed.get("reference_strength") or parsed.get("referenceStrength") or 0,
        "rationale": parsed.get("rationale") or "",
    }
    return {**persona, "telecomBehaviorDimensions": dimensions}, metadata, usage


def generate_seed_based_personas(payload: dict, existing_personas: Iterable[dict] | None = None, *, seed_path: Path | None = None) -> dict:
    started_at = time.monotonic()
    existing = list(existing_personas or [])
    segments = _segments_for_payload(payload)
    profiles = _fallback_profiles(payload, segments)
    selected_seeds = select_nemotron_korea_seeds(
        payload=payload,
        segments=segments,
        profiles=profiles,
        existing_personas=existing,
        seed_path=seed_path,
    )
    selected_names = []
    personas = []
    for selected in selected_seeds:
        persona = _with_persona_tag(
            map_nemotron_seed_to_persona(selected, existing, selected_names),
            selected["profile"],
            selected["segment"],
            payload,
        )
        selected_names.append(persona["name"])
        personas.append(persona)
    timings_ms = {"seedSelection": int((time.monotonic() - started_at) * 1000)}
    timings_ms["total"] = timings_ms["seedSelection"]
    return {
        "personas": personas,
        "segments": _align_segment_counts(segments, profiles),
        "generation_mode": "nemotron_seed_telecom_polished",
        "generation_metadata": {
            "nemotronSeedReferences": [_seed_metadata(selected, persona["name"]) for selected, persona in zip(selected_seeds, personas)],
            "timingsMs": timings_ms,
        },
        "token_usage": _empty_usage(),
        "seed_count": len(load_seed_personas(seed_path, limit=max(len(profiles), 1))),
    }


def generate_personas_pipeline(
    payload: dict,
    *,
    existing_personas: Iterable[dict] | None = None,
    text_generator: Callable[[str], tuple[str, dict]],
    seed_path: Path | None = None,
) -> dict:
    started_total = time.monotonic()
    total_usage = _empty_usage()
    timings_ms = {}
    existing = list(existing_personas or [])

    started = time.monotonic()
    segments, profiles, usage = stage_segmentation_and_identity(payload, existing, text_generator)
    timings_ms["segmentation"] = int((time.monotonic() - started) * 1000)
    _add_usage(total_usage, usage)

    started = time.monotonic()
    selected_seeds = select_nemotron_korea_seeds(
        payload=payload,
        segments=segments,
        profiles=profiles,
        existing_personas=existing,
        seed_path=seed_path,
    )
    timings_ms["seedSelection"] = int((time.monotonic() - started) * 1000)

    selected_names = []
    service_context = _service_context(payload)
    seeded_drafts = []
    for selected in selected_seeds:
        persona = _with_persona_tag(
            map_nemotron_seed_to_persona(selected, existing, selected_names),
            selected["profile"],
            selected["segment"],
            payload,
        )
        selected_names.append(persona["name"])
        seeded_drafts.append({"selected": selected, "persona": persona})

    started = time.monotonic()
    max_workers = resolve_persona_generation_max_concurrency(len(seeded_drafts))
    _log_persona_generation_event("batch_start", personas=len(seeded_drafts), max_workers=max_workers)

    def generate_one(item: dict):
        selected = item["selected"]
        persona = item["persona"]
        _log_persona_generation_event("worker_start", persona=persona.get("name"))
        try:
            persona, usage = stage_nemotron_seed_narrative_polish(persona, payload, selected["segment"], selected["seed"], text_generator)
            narrative_usage = usage
            persona, usage = stage_nemotron_telecom_dimensions(persona, payload, selected["segment"], selected["seed"], text_generator)
            telecom_usage = usage
            metadata = None
            interview_usage = None
            if _is_likely_telecom_context(service_context, selected["segment"].get("name"), selected["segment"].get("description")):
                persona, metadata, usage = regenerate_telecom_service_usage_context_from_interview_reference(persona, payload, text_generator)
                interview_usage = usage
            return {
                "persona": persona,
                "narrative_usage": narrative_usage,
                "telecom_usage": telecom_usage,
                "interview_usage": interview_usage,
                "interview_reference": metadata,
                "seed_reference": _seed_metadata(selected, persona["name"]),
            }
        finally:
            _log_persona_generation_event("worker_end", persona=persona.get("name"))

    persona_results = _map_with_concurrency(seeded_drafts, max_workers, generate_one)
    personas = []
    seed_references = []
    interview_references = []
    for result in persona_results:
        _add_usage(total_usage, result.get("narrative_usage"))
        _add_usage(total_usage, result.get("telecom_usage"))
        _add_usage(total_usage, result.get("interview_usage"))
        if result.get("interview_reference"):
            interview_references.append(result["interview_reference"])
        personas.append(result["persona"])
        seed_references.append(result["seed_reference"])
    timings_ms["narrativeTelecomAndPostprocess"] = int((time.monotonic() - started) * 1000)
    _log_persona_generation_event("batch_end", personas=len(personas), max_workers=max_workers)
    timings_ms["total"] = int((time.monotonic() - started_total) * 1000)

    return {
        "personas": personas,
        "segments": segments,
        "generation_mode": "nemotron_seed_telecom_polished",
        "generation_metadata": {
            "nemotronSeedReferences": seed_references,
            "timingsMs": timings_ms,
        },
        "telecom_service_usage_context_references": interview_references,
        "token_usage": total_usage,
        "seed_count": len(selected_seeds),
    }
