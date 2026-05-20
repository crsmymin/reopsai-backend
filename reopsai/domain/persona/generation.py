from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "nemotron-personas-korea-sample.jsonl"
DEFAULT_TEXT_MODEL = os.getenv("PERSONA_GEMINI_TEXT_MODEL") or "gemini-2.5-pro"

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
LANGUAGE_LABELS = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "de": "German",
    "zh": "Chinese",
}


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
            for segment in segment_inputs:
                if not isinstance(segment, dict):
                    errors.append("segmentInputs must be an array of valid segment objects")
                    break
                try:
                    target_count = int(round(float(segment["targetCount"])))
                    if target_count < 1:
                        raise ValueError("targetCount must be positive")
                    parsed_segments.append(
                        {
                            "id": str(segment["id"]).strip(),
                            "name": str(segment["name"]).strip(),
                            "description": str(segment["description"]).strip(),
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
    if not parsed_segments and not service_description:
        errors.append("serviceDescription is required when segmentInputs is not provided")

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


def _json_extract(text: str) -> dict:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


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
                "name": str(segment.get("name") or f"Segment {index + 1}"),
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
        content, usage = text_generator(f"{prompt}{retry}")
        try:
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
    required = [
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
    missing = [field for field in required if not _has_text(persona.get(field))]
    if missing:
        raise PersonaGenerationQualityError(f"narrative fields missing: {', '.join(missing)}")


def stage_nemotron_seed_narrative_polish(persona: dict, payload: dict, segment: dict, seed: dict, text_generator: Callable[[str], tuple[str, dict]]) -> tuple[dict, dict]:
    def validate(parsed: dict):
        candidate = parsed.get("persona") if isinstance(parsed.get("persona"), dict) else parsed
        _validate_narrative({**persona, **candidate})

    parsed, usage = _execute_json_stage(text_generator, _narrative_prompt(persona, payload, segment, seed), stage_name="narrative", validator=validate)
    updates = parsed.get("persona") if isinstance(parsed.get("persona"), dict) else parsed
    merged = {**persona, **updates, "schemaVersion": 3}
    _validate_narrative(merged)
    return merged, usage


def _telecom_prompt(persona: dict, payload: dict, segment: dict, seed: dict) -> str:
    target_language = _language_label(payload)
    return f"""
STAGE: telecom_dimensions
You are an expert telecom UX researcher.

## Task
Generate only telecom behavior dimensions for a fixed persona.

## Critical Rules
1. Do not rewrite the persona identity or narrative.
2. Infer telecom behavior from the persona's life context, household situation, job habits, and service context.
3. Make each field concrete and behavior-based.
4. Avoid generic labels. Write 1-3 natural-language sentences per field.
5. Generate all text in {target_language}.
6. Respond with JSON only.

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
  }}
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

Generate the 11 telecom behavior variables only. Keep the fixed persona unchanged.
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


def stage_nemotron_telecom_dimensions(persona: dict, payload: dict, segment: dict, seed: dict, text_generator: Callable[[str], tuple[str, dict]]) -> tuple[dict, dict]:
    def validate(parsed: dict):
        dimensions = _normalize_telecom_dimensions(parsed.get("telecomBehaviorDimensions") or parsed.get("telecom_behavior_dimensions"))
        _validate_telecom_dimensions(dimensions)

    parsed, usage = _execute_json_stage(text_generator, _telecom_prompt(persona, payload, segment, seed), stage_name="telecom_dimensions", validator=validate)
    dimensions = _normalize_telecom_dimensions(parsed.get("telecomBehaviorDimensions") or parsed.get("telecom_behavior_dimensions"))
    return {
        **persona,
        "telecomBehaviorDimensions": dimensions,
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
        persona = map_nemotron_seed_to_persona(selected, existing, selected_names)
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

    personas = []
    seed_references = []
    interview_references = []
    selected_names = []
    service_context = _service_context(payload)

    started = time.monotonic()
    for selected in selected_seeds:
        persona = map_nemotron_seed_to_persona(selected, existing, selected_names)
        selected_names.append(persona["name"])
        persona, usage = stage_nemotron_seed_narrative_polish(persona, payload, selected["segment"], selected["seed"], text_generator)
        _add_usage(total_usage, usage)
        persona, usage = stage_nemotron_telecom_dimensions(persona, payload, selected["segment"], selected["seed"], text_generator)
        _add_usage(total_usage, usage)
        if _is_likely_telecom_context(service_context, selected["segment"].get("name"), selected["segment"].get("description")):
            persona, metadata, usage = regenerate_telecom_service_usage_context_from_interview_reference(persona, payload, text_generator)
            _add_usage(total_usage, usage)
            if metadata:
                interview_references.append(metadata)
        personas.append(persona)
        seed_references.append(_seed_metadata(selected, persona["name"]))
    timings_ms["narrativeTelecomAndPostprocess"] = int((time.monotonic() - started) * 1000)
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
