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
TELECOM_SCORE_DIMENSION_FIELDS = {
    "brandRetention": (
        "brandRetention.brandRetentionTendency",
        "brandRetention.premiumInfraBenefitOrientation",
    ),
    "optimizationResource": (
        "optimizationResource.optimizationResourceInvestment",
        "optimizationResource.paymentResistanceLine",
    ),
    "informationControl": (
        "informationControl.informationExplorationStyle",
        "informationControl.problemSolvingAutonomy",
    ),
    "digitalAiOpenness": (
        "digitalAiOpenness.aiProviderTrust",
        "digitalAiOpenness.personalizationDataSharingScope",
    ),
}

TELECOM_DIMENSIONS_JSON_CONTRACT = """
## OUTPUT CONTRACT (mandatory — parse errors if violated)
Return exactly ONE JSON object. No markdown fences, no commentary, no extra top-level keys.

Required top-level key: `telecom_behavior_dimensions` (snake_case). Do NOT use `telecomBehaviorDimensions`.

Inside `telecom_behavior_dimensions`, use exactly these 5 group keys (camelCase) and 11 field keys — copy verbatim:
- brandRetention.brandRetentionTendency
- brandRetention.premiumInfraBenefitOrientation
- optimizationResource.optimizationResourceInvestment
- optimizationResource.paymentResistanceLine
- informationControl.informationExplorationStyle
- informationControl.problemSolvingAutonomy
- digitalAiOpenness.aiProviderTrust
- digitalAiOpenness.personalizationDataSharingScope
- telecomLifeCharacteristics.householdDecisionLeadership
- telecomLifeCharacteristics.productServiceUnderstanding
- telecomLifeCharacteristics.telecomServiceUsageContext

Rules:
- Do NOT rename keys (no snake_case field names, no Korean keys, no flat root-level fields).
- Do NOT omit any of the 11 fields.
- Each field value must be a non-empty string (2-3 sentences).
- Do NOT include scores or any keys outside this schema.

Template:
{
  "telecom_behavior_dimensions": {
    "brandRetention": {
      "brandRetentionTendency": "<string>",
      "premiumInfraBenefitOrientation": "<string>"
    },
    "optimizationResource": {
      "optimizationResourceInvestment": "<string>",
      "paymentResistanceLine": "<string>"
    },
    "informationControl": {
      "informationExplorationStyle": "<string>",
      "problemSolvingAutonomy": "<string>"
    },
    "digitalAiOpenness": {
      "aiProviderTrust": "<string>",
      "personalizationDataSharingScope": "<string>"
    },
    "telecomLifeCharacteristics": {
      "householdDecisionLeadership": "<string>",
      "productServiceUnderstanding": "<string>",
      "telecomServiceUsageContext": "<string>"
    }
  }
}
""".strip()

TELECOM_SCORES_JSON_CONTRACT = f"""
## OUTPUT CONTRACT (mandatory — parse errors if violated)
Return exactly ONE JSON object. No markdown fences, no commentary, no extra top-level keys.

Required top-level key: `telecom_behavior_scores` (snake_case). Do NOT use `telecomBehaviorScores`.

`telecom_behavior_scores` MUST be a JSON array of exactly 4 objects, in this fixed order:
1. brandRetention
2. optimizationResource
3. informationControl
4. digitalAiOpenness

Each array item MUST include exactly these keys:
- key (string, one of the four English keys above)
- label (string, fixed Korean label from template below)
- score (integer 1-5, NOT a string)
- maxScore (integer 5)
- rationale (string, at least 2 sentences in Korean)
- evidence (array of 1-3 strings — NOT a single string)

Fixed key / label pairs:
- brandRetention → {TELECOM_SCORE_GROUPS["brandRetention"]}
- optimizationResource → {TELECOM_SCORE_GROUPS["optimizationResource"]}
- informationControl → {TELECOM_SCORE_GROUPS["informationControl"]}
- digitalAiOpenness → {TELECOM_SCORE_GROUPS["digitalAiOpenness"]}

Rules:
- Do NOT add, remove, or reorder array items.
- Do NOT use Korean or alternate values for `key`.
- Do NOT wrap evidence as a string; use a JSON string array.
- Output ONLY the JSON object below — no rubric text outside JSON.

Template:
{{
  "telecom_behavior_scores": [
    {{
      "key": "brandRetention",
      "label": "{TELECOM_SCORE_GROUPS["brandRetention"]}",
      "score": 4,
      "maxScore": 5,
      "rationale": "<string>",
      "evidence": ["<string>", "<string>"]
    }},
    {{
      "key": "optimizationResource",
      "label": "{TELECOM_SCORE_GROUPS["optimizationResource"]}",
      "score": 2,
      "maxScore": 5,
      "rationale": "<string>",
      "evidence": ["<string>"]
    }},
    {{
      "key": "informationControl",
      "label": "{TELECOM_SCORE_GROUPS["informationControl"]}",
      "score": 3,
      "maxScore": 5,
      "rationale": "<string>",
      "evidence": ["<string>"]
    }},
    {{
      "key": "digitalAiOpenness",
      "label": "{TELECOM_SCORE_GROUPS["digitalAiOpenness"]}",
      "score": 1,
      "maxScore": 5,
      "rationale": "<string>",
      "evidence": ["<string>"]
    }}
  ]
}}
""".strip()
TELECOM_SCORE_RUBRIC_TEXT = """
## Scale meaning (read before scoring)

- **1–5 is a behavioral tendency on this axis only — NOT a quality grade, NOT "bad vs good".**
- **Low scores (1–2) are normal, valid user profiles** (cautious, minimal, delegate, offline-first). Do NOT round up to sound positive or sympathetic.
- **High scores (4–5) are also normal** when dimension text shows strong engagement on that axis.
- Score from **observed behavior in dimension text**, not from brand prestige, polite tone, or "AI/innovation sounds positive".
- Avoid **positivity bias**: marketing-friendly phrases ("혁신", "긍정적 이미지", "신뢰") without matching **usage behavior** do NOT justify 4–5.

## How to score (Generated Telecom Behavior Dimensions = ONLY source for the integer)

For each of the 4 groups:
1. Read ONLY the two dimension fields listed for that group.
2. Identify the **dominant behavior** on that axis (not one optimistic sentence — overall pattern).
3. Match **one** anchor 1–5. If texts conflict, pick the **lower** score (e.g. "긍정적 이미지" + "몇 번만 쓰고 안 씀" → digitalAiOpenness ≤2).
4. rationale: neutral researcher tone — describe the tendency at this level; **never** apologize for low scores or praise high scores as "better".
5. evidence: 1–3 strings from those dimension fields only.
6. Use the **full scale** including 1 and 2 whenever they fit — **do not cluster at 3–4** out of courtesy.

---

### brandRetention — 브랜드 유지 성향
Fields: brandRetentionTendency, premiumInfraBenefitOrientation
(Low = 이동·비교 중심 성향, High = 유지·결합 중심 성향 — neither is "wrong".)

| Score | When to assign (dominant pattern in dimension text) |
|-------|------------------------------------------------------|
| **1** | Active switching mindset: 번호이동·이탈·6개월·보상·상품권·유지보다 이동·이동이 유리·자주 변경. Little reason to stay; premium/quality benefits ignored. |
| **2** | Change-ready: 가격·혜택이 좋으면 변경, 약정 끝나면 검토, 장기 관성 약함. Some brand mention but **behavior favors moving**. |
| **3** | Balanced: 유지하나 이득 있으면 검토, 불편 없으면 유지. Neither loyalist nor frequent switcher. |
| **4** | Stay-oriented: 결합·가족·번호 유지 부담·장기 이용·변경 꺼림·안정·신뢰. Switching mentioned only as rare exception. |
| **5** | Strong loyalty: 충성·생태계 이탈 비용·프리미엄/품질/멤버십을 핵심 가치. Switching almost absent. |

**Not 4-5 if:** dimension says 이동 적극, 주기적 변경, 유지보다 보상, 알뜰/이동 최적화가 주 전략.

---

### optimizationResource — 최적화 리소스 투입
Fields: optimizationResourceInvestment, paymentResistanceLine
(Low = 시간·노력 절약·위임·편의 구매 성향 — valid. High = 직접 비교·능동 관리 성향.)

| Score | When to assign |
|-------|----------------|
| **1** | Almost no self-comparison: 위임·맡김·귀찮·시간 아깝·최소 확인·결론만·상담·대리점 의존. OR: switches often for gifts with **no** ongoing self-optimization narrative. |
| **2** | Low effort: 가끔 확인, 복잡한 재계산 회피, 편의·프리미엄에 돈으로 해결. Compares only when forced. |
| **3** | Mixed: 핵심만 비교, 필요 시에만. Sometimes saves money, sometimes saves time — equally plausible. |
| **4** | Active comparer: 정기적 앱/요금표/커뮤니티 비교, 결합·할인 직접 관리, 재검토 습관. |
| **5** | Power optimizer: 지속·능동·엑셀/알림/후기/상담 병행, 월 지출·혜택 능동 관리. |

---

### informationControl — 정보탐색 및 통제 욕구
Fields: informationExplorationStyle, problemSolvingAutonomy
(Low = 위임·상담 의존 성향. High = 직접 탐색·통제 성향 — both common.)

| Score | When to assign |
|-------|----------------|
| **1** | Delegates: 탐색 거의 없음, 상담원·대리점·지인 위임, 직접 해결 안 함. |
| **2** | Shallow: 기본 확인만, 조건·근거 비교 얕음, 막히면 바로 상담. |
| **3** | Hybrid: 앱·안내 + 주변 의견, 단순은 직접·복잡은 상담. |
| **4** | Self-driven: 다채널 비교·검증, 스스로 후보 축소, FAQ/챗봇 먼저. |
| **5** | High control: 약관·비교표·예외까지 확인, 자가 해결·추적 선호. |

---

### digitalAiOpenness — 디지털 및 AI 개방성
Fields: aiProviderTrust, personalizationDataSharingScope

**Axis definition:** How much the person **actually uses and trusts** carrier digital/AI channels and data sharing — not how futuristic they sound.

| Score | Tendency label (neutral) | Assign when dimension text shows |
|-------|--------------------------|----------------------------------|
| **1** | Minimal digital/AI reliance | 대면·오프라인·전화/매장 선호; AI·추천·개인화 **거부 또는 미사용**; 또는 통신사 AI를 **잠깐만** 써 보고 **지속 사용 없음**, 일상은 **다른 앱/기본 기능**(제미나이·기본앱 등). **"혁신 기대/긍정 이미지" 문장만 있고 사용 행동이 없으면 1–2.** |
| **2** | Cautious, limited trial | 필수 앱 기능(요금·납부) 위주; AI·맞춤 **불신·꺼림·보조 수준**; 데이터 **최소·비식별**만; 신기해서 시험은 했으나 **루틴에 없음**. |
| **3** | Pragmatic middle | 익숙한 메뉴만; AI는 **가끔 참고**, 최종은 직접 확인; 위치·민감 데이터는 **제한적** 허용. |
| **4** | Regular digital adopter | 앱·AI 추천을 **반복적으로** 활용; 사용량·선호 등 **일부 데이터 제공**에 동의; 통신사 채널이 **실제 선택 경로**. |
| **5** | High openness | 신기능·구독·개인화·데이터 기반 추천을 **적극·지속** 사용; 새 디지털 실험에 **편안함**. |

**digitalAiOpenness anti-bias (mandatory):**
- **Do NOT score 4–5** for: "기대한다", "좋을 것 같다", "긍정적 이미지", "신뢰한다" **without** repeated/ongoing use in dimension text.
- **Score 1–2 is expected** for: 몇 번 사용, 지속 이어지지 않음, 다른 서비스로 옮김, 설정 끔, 불편·부담, 개인정보 최소화 — these are **valid profiles**, not errors.
- Separating **attitude toward innovation** (talk) vs **adoption** (action): when talk > action, score by **action** (usually 1–2).

---

## Cross-group consistency
- Scores may differ across groups (e.g. brandRetention=2, digitalAiOpenness=1, informationControl=4).
- Within one group, both dimension fields must support the chosen score; if they conflict, score lower and note the conflict neutrally in rationale.
""".strip()
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


def _income_formatting_guidance(payload: dict) -> str:
    country = ((payload.get("locale") or {}).get("country") or "").upper()
    if country == "KR":
        return """
## Income Format Requirements
- income means the persona's own annual personal income, not household income
- income must be a single exact annual income amount in Korean won only
- Format it as digits with comma separators and end with 원
- Example: "68,000,000원"
- If the persona is primarily a homemaker, caregiver, student, job-seeker, retiree, or otherwise not in paid employment, leave income empty unless there is a clear personal income source
- Do not write 월 소득, ranges, percentile descriptions, or any explanatory prose inside income"""
    return """
## Income Format Requirements
- income must describe the persona's own annual personal income, not household income
- income must be a single exact annual income amount only
- If the persona is not in paid employment and has no clear personal income source, leave income empty
- Do not write ranges, percentile descriptions, or explanatory prose inside income"""


def _is_likely_telecom_context(*values) -> bool:
    return bool(TELECOM_CONTEXT_PATTERN.search(" ".join(str(value or "") for value in values)))


def _format_segment_inputs(segment_inputs: list[dict]) -> str:
    blocks = []
    for index, segment in enumerate(segment_inputs or []):
        criteria = f"- Criteria: {segment.get('criteria')}" if segment.get("criteria") else ""
        blocks.append(
            "\n".join(
                value
                for value in [
                    f"{index + 1}. {segment.get('name')}",
                    f"- ID: {segment.get('id')}",
                    f"- Description: {segment.get('description')}",
                    criteria,
                    f"- Target Count: {segment.get('targetCount')}",
                ]
                if value
            )
        )
    return "\n\n".join(blocks)


def _telecom_segmentation_guidance(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
## Telecom Differentiation Requirements
- Because this service is telecom-related, make segments diverge across telecom behavior and service-management style
- Vary brand retention and premium infrastructure/benefit orientation
- Vary whether optimization is driven by money saving or by reducing time and effort
- Vary information exploration and problem-solving autonomy
- Vary AI/provider trust and willingness to share data for personalization
- Vary household decision leadership, telecom product understanding, and daily telecom service usage context
- Avoid creating multiple segments that share the same telecom decision logic with only demographic differences"""


def _existing_persona_names(existing_personas: list[dict]) -> str:
    names = [str(persona.get("name") or "").strip() for persona in existing_personas or []]
    names = [name for name in names if name]
    return ", ".join(names) if names else "None"


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
        if attempt == 0:
            retry = ""
        else:
            retry = (
                f"\n\nREGENERATE REQUIRED: The previous {stage_name} response was invalid ({last_error}). "
                "Return one complete valid JSON object only. Include every required field. "
                "Do not truncate, omit groups, or use placeholder text."
            )
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
    locale = payload.get("locale") or {}
    target_language = _language_label(payload)
    country = locale.get("country") or "KR"
    region = locale.get("region") or "Nationwide"
    existing_names = _existing_persona_names(existing_personas)
    segment_inputs = payload.get("segmentInputs") or []
    total_count = _coerce_count(payload.get("totalCount"))
    telecom_guidance = _telecom_segmentation_guidance(
        _is_likely_telecom_context(
            payload.get("serviceDescription"),
            payload.get("targetAudience"),
            *[
                value
                for segment in segment_inputs
                for value in [segment.get("name"), segment.get("description"), segment.get("criteria")]
            ],
        )
    )

    if payload.get("sourceType") == "segment_based" and segment_inputs:
        total_from_segments = sum(_coerce_count(segment.get("targetCount")) for segment in segment_inputs)
        return f"""
STAGE: segmentation_identity
You are an expert in persona creation and market segmentation.

## Task
You are given predefined segments. DO NOT create new segments.
For each provided segment:
1. Keep the id, name, and description EXACTLY as provided
2. Derive segment characteristics based on the description and criteria
3. Generate identity profiles matching the segment's target_count

## Critical Rules
- Do NOT add, remove, rename, or reorder segments
- Segment IDs are immutable foreign keys. Every profile.segment_id MUST exactly equal one of the provided segment IDs, character-for-character.
- Do NOT invent profile segment IDs like "segment_1" unless that exact ID was provided.
- The total profiles array length MUST match the requested Total exactly
- The number of profiles per segment MUST match target_count exactly
- Names must be realistic and culturally appropriate for the country
- Age must be a specific integer and align with the segment's age_range_hint
- title, sector, organisation, role_area, and role_level must be written as human-readable labels in the target language
- Do not output English role level codes like "manager" unless the target language itself is English
- If the persona is not in a conventional paid job, do not force corporate job metadata
- For homemaker, student, job-seeker, retiree, caregiver, or similar personas:
  use title as the main social role,
  leave organisation empty unless there is a real affiliation,
  leave role_level empty unless it is truly meaningful,
  and use role_area for one short responsibility/domain instead of a fake department name
- All profiles must have unique name+age+occupation combinations
- Respond with JSON only

## Output Format
{{
  "segments": [
    {{
      "id": "segment_1",
      "name": "Segment Name",
      "name_en": "Segment Name in English",
      "description": "Brief description of this segment",
      "target_count": 2,
      "characteristics": {{
        "key_traits": ["trait1", "trait2", "trait3"],
        "age_range_hint": "age-range",
        "occupation_hint": ["occupation1", "occupation2"],
        "income_level_hint": "level",
        "urban_rural_hint": "area_type"
      }}
    }}
  ],
  "profiles": [
    {{
      "segment_id": "segment_1",
      "name": "Full Name",
      "title": "Job Title",
      "age": 34,
      "gender": "female",
      "generation": "millennial",
      "current_city": "Seoul",
      "current_country": "KR",
      "sector": "IT",
      "organisation": "Company Name",
      "role_area": "Product",
      "role_level": "Role level in target language"
    }}
  ]
}}

IMPORTANT: Generate persona names that are culturally appropriate for {country}. Write names in the country's common script (or standard romanization if needed), and do not translate names into {target_language}. Keep segment names and descriptions exactly as provided. Generate all other text content in {target_language}. Keep "name_en" in English.

## Provided Segments
{_format_segment_inputs(segment_inputs)}

## Country/Region Context
- Country: {country}
- Region: {region}
- Target Language: {target_language}

## Number of Personas to Generate
Total {total_from_segments or total_count}
The profiles array MUST contain exactly {total_from_segments or total_count} item(s).

## Existing Personas (avoid duplicates)
{existing_names}
{telecom_guidance}

Use the provided segments exactly as listed.
Generate segment characteristics and identity profiles for each segment.
Every profile.segment_id must exactly match a provided segment ID from the list above.
All names must be unique and not duplicate existing personas.
""".strip()

    diversity_instruction = ""
    if total_count > 1:
        diversity_instruction = """
## Diversity Requirements
- Unless the target audience explicitly restricts gender or age, distribute personas across multiple age bands and genders
- Avoid all personas sharing the same gender or within a 5-year age window
- If the target audience is narrow, stay within it but vary ages across early/mid/late range and role levels"""

    return f"""
STAGE: segmentation_identity
You are an expert in market segmentation and persona development.

## Task
Based on the provided service description and target audience:
1. Define 3-5 distinct customer segments
2. Generate basic identity profiles for each segment

## Segment Definition Rules
- Each segment must have distinctly different characteristics
- Segments should represent realistic customer groups who would actually use this service
- Segment names should be concise and descriptive
- Characteristics should include key traits, age range hints, and occupation hints

## Diversity & Coverage Rules
- Unless the target audience explicitly restricts gender or age, ensure demographic variety across segments
- Use multiple age bands and a mix of genders when generating multiple personas
- Do not let all profiles share the same gender or sit within a 5-year age window unless explicitly required

## Identity Profile Rules
- The profiles array length MUST match the requested Total exactly
- If Total is 1, output exactly one profile, even if you define multiple segments
- Names: realistic names appropriate for the country/culture
- Age: REQUIRED integer field - specific age, not a range
- Generation: derived from age (gen_z: 13-28, millennial: 29-44, gen_x: 45-60, baby_boomer: 61-79)
- City: real city names from the specified country
- title, sector, organisation, role_area, and role_level must be written as human-readable labels in the target language
- Do not output English role level codes like "manager" unless the target language itself is English
- If the persona is not in a conventional paid job, do not force corporate job metadata
- For homemaker, student, job-seeker, retiree, caregiver, or similar personas:
  use title as the main social role,
  leave organisation empty unless there is a real affiliation,
  leave role_level empty unless it is truly meaningful,
  and use role_area for one short responsibility/domain instead of a fake department name
- All profiles must have unique name+age+occupation combinations
- Respond with JSON only

## Output Format
{{
  "segments": [
    {{
      "id": "segment_1",
      "name": "Segment Name",
      "name_en": "Segment Name in English",
      "description": "Brief description of this segment",
      "target_count": 3,
      "characteristics": {{
        "key_traits": ["trait1", "trait2", "trait3"],
        "age_range_hint": "age-range",
        "occupation_hint": ["occupation1", "occupation2"],
        "income_level_hint": "level",
        "urban_rural_hint": "area_type"
      }}
    }}
  ],
  "profiles": [
    {{
      "segment_id": "segment_1",
      "name": "Full Name",
      "title": "Job Title",
      "age": 34,
      "gender": "female",
      "generation": "millennial",
      "current_city": "Seoul",
      "current_country": "KR",
      "sector": "IT",
      "organisation": "Company Name",
      "role_area": "Product",
      "role_level": "Role level in target language"
    }}
  ]
}}

IMPORTANT: Generate persona names that are culturally appropriate for {country}. Write names in the country's common script (or standard romanization if needed), and do not translate names into {target_language}. Generate all other text content in {target_language}. Keep "name_en" in English.

## Service Information
{payload.get("serviceDescription") or ""}

## Target Audience
{payload.get("targetAudience") or ""}

## Country/Region Context
- Country: {country}
- Region: {region}
- Target Language: {target_language}

## Number of Personas to Generate
Total {total_count}
The profiles array MUST contain exactly {total_count} item(s). Do not generate one profile per segment unless the requested Total requires it.

## Existing Personas (avoid duplicates)
{existing_names}
{diversity_instruction}
{telecom_guidance}

Generation input:
{json.dumps(payload, ensure_ascii=False)}

Based on the above information, generate segment definitions and basic identity profiles.
All names must be unique and not duplicate existing personas.
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
    target_language = _language_label(payload)
    income_guidance = _income_formatting_guidance(payload)
    return f"""
STAGE: narrative_polish
You are an expert persona editor.

## Task
Lightly polish a dataset-seeded persona into clean app-ready persona fields.

## Critical Rules
1. Preserve all facts from the seed: age, gender, job, city, household, housing, education, interests, and life history.
2. Do not adapt the persona to the telecom service here.
3. Do not invent new life events.
4. Keep the output concise, coherent, and field-oriented.
5. Do not generate telecom behavior dimensions.
6. Required narrative fields: attitudes, biography, demeanour, interests, behaviours, motivation, upbringing, personality, preferences, socialContext, culturalBackground, quote, imagePrompt.
7. Do not omit required fields. If a detail is not explicit, infer a concrete value from the draft persona and Nemotron seed without contradicting the seed.
8. Generate all narrative text in {target_language}. Only imagePrompt should be in English.
9. Respond with JSON only.

## Output Format
{{
  "attitudes": "Polished worldview and values",
  "biography": "Polished biography",
  "demeanour": "Polished communication style",
  "interests": "Polished interests",
  "behaviours": "Polished behavior patterns",
  "motivation": "Polished motivations",
  "upbringing": "Polished upbringing",
  "personality": "Polished personality",
  "preferences": "Polished preferences",
  "socialContext": "Polished social context",
  "culturalBackground": "Polished cultural background",
  "income": "Exact annual income amount only",
  "quote": "Representative quote",
  "imagePrompt": "English profile image prompt"
}}

Generation input:
{json.dumps(payload, ensure_ascii=False)}

Segment:
{json.dumps(segment, ensure_ascii=False)}

Draft persona:
{json.dumps(persona, ensure_ascii=False)}

Nemotron seed:
{json.dumps(seed, ensure_ascii=False)}

{income_guidance}

Lightly rewrite only for clarity and field fit. Preserve the seed's facts and tone.
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


def _telecom_fixed_persona_json(persona: dict) -> str:
    return json.dumps(
        {
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
        },
        ensure_ascii=False,
        indent=2,
    )


def _telecom_context_tail(persona: dict, payload: dict, segment: dict, seed: dict) -> str:
    return f"""
## Fixed Persona
{_telecom_fixed_persona_json(persona)}

## Selected Segment
- Segment: {segment.get("name")}
- Description: {segment.get("description")}
- Key Traits: {", ".join((segment.get("characteristics") or {}).get("keyTraits") or [])}

## Service Context
{_service_context(payload)}

## Dataset Seed Context
{json.dumps(seed, ensure_ascii=False, indent=2)}
""".strip()


def _telecom_dimensions_only_prompt(
    persona: dict,
    payload: dict,
    segment: dict,
    seed: dict,
    *,
    interview_evidence_text: str = "",
) -> str:
    target_language = _language_label(payload)
    has_interview_evidence = bool(interview_evidence_text.strip())
    evidence_section = ""
    evidence_rules = ""
    if has_interview_evidence:
        evidence_section = f"""
## Interview Evidence (curated bundle — PRIMARY source)
The bundle lists real user experiences filtered for ONE coherent behavioral profile.
Ground ALL 11 dimension fields in the same dominant axes. Do not contradict the bundle.
Paraphrase for the fixed persona; never copy participant names from chunks.
{interview_evidence_text.strip()}
"""
        evidence_rules = """
8. Treat the curated bundle as one behavioral story; all 11 fields must align with dominant_axes.
9. Fields not explicitly in the bundle may be inferred conservatively from the same axes only.
10. Never combine conflicting habits (long-term carrier loyalty vs frequent switching for rewards).
11. Do not cite or invent evidence from dropped/conflicting interview chunks.
12. `telecomServiceUsageContext` must match the bundle's daily telecom usage patterns.
"""
    return f"""
STAGE: telecom_dimensions
You are an expert telecom UX researcher.

{TELECOM_DIMENSIONS_JSON_CONTRACT}

## Task
Generate only the 11 telecom behavior dimension fields for a fixed persona. Do not generate scores.
{evidence_section}

## Critical Rules
1. Do not rewrite the persona identity or narrative (name, age, biography, title).
2. {"Prioritize the curated Interview Evidence bundle; use fixed persona only for tone and segment fit." if has_interview_evidence else "Infer telecom behavior from the persona's life context, household situation, job habits, and service context."}
3. Make each field concrete and behavior-based; mention the persona by name where natural.
4. Write 2-3 natural-language sentences per dimension field (not keywords).
5. Make fields meaningfully distinct from each other.
6. Generate all dimension text in {target_language}.
7. Respond with JSON only — follow OUTPUT CONTRACT exactly.{evidence_rules}

{_telecom_context_tail(persona, payload, segment, seed)}

Generate all 11 fields. Keep the fixed persona unchanged. Output ONLY the JSON object from OUTPUT CONTRACT.
""".strip()


def _telecom_scores_prompt(
    persona: dict,
    payload: dict,
    segment: dict,
    dimensions: dict,
) -> str:
    target_language = _language_label(payload)
    field_map_lines = "\n".join(
        f"- {key}: {', '.join(fields)}"
        for key, fields in TELECOM_SCORE_DIMENSION_FIELDS.items()
    )
    return f"""
STAGE: telecom_scores
You are an expert telecom UX researcher.

{TELECOM_SCORES_JSON_CONTRACT}

## Task
Assign an integer score (1-5) to each of the four telecom behavior groups using the rubric and dimension texts below.
Do not rescore or rewrite dimension narratives.

## Dimension fields used per score group
{field_map_lines}

{TELECOM_SCORE_RUBRIC_TEXT}

## Critical Rules
1. Scores are **tendency labels**, not judgments — low is NOT "bad"; high is NOT "good".
2. Score ONLY from dimension field behaviors — ignore flattering adjectives, brand image, or innovation hype without usage proof.
3. If behavior matches 1 or 2, output 1 or 2 — **never inflate** to 3–4 to avoid sounding negative (especially digitalAiOpenness).
4. rationale (2+ sentences in {target_language}): neutral description of this tendency level; cite dimension phrases; no moral wording.
5. evidence: 1-3 short strings from dimension fields for that group.
6. Groups are independent axes — different scores are normal; identical 4–4–4–4 is suspicious unless dimensions truly match.
7. Respond with JSON only — follow OUTPUT CONTRACT exactly.

## Fixed Persona
{_telecom_fixed_persona_json(persona)}

## Generated Telecom Behavior Dimensions
{json.dumps(dimensions, ensure_ascii=False, indent=2)}

## Selected Segment
- Segment: {segment.get("name")}
- Description: {segment.get("description")}

## Service Context
{_service_context(payload)}

Generate exactly four score objects. Output ONLY the JSON object from OUTPUT CONTRACT.
""".strip()


def _telecom_prompt(
    persona: dict,
    payload: dict,
    segment: dict,
    seed: dict,
    *,
    interview_evidence_text: str = "",
) -> str:
    """Backward-compatible alias: dimensions-only prompt (scores are a separate stage)."""
    return _telecom_dimensions_only_prompt(
        persona,
        payload,
        segment,
        seed,
        interview_evidence_text=interview_evidence_text,
    )


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


def _interview_evidence_coherence_prompt(
    persona: dict,
    payload: dict,
    segment: dict,
    *,
    candidates_text: str,
) -> str:
    target_language = _language_label(payload)
    segment_traits = (segment.get("characteristics") or {}).get("keyTraits") or (segment.get("characteristics") or {}).get("key_traits") or []
    return f"""
STAGE: interview_evidence_coherence
You are an expert telecom UX researcher curating interview evidence for persona DNA generation.

## Task
From the candidate chunks below, select a coherent subset for ONE synthetic persona's telecom behavior.
Output dominant behavioral axes and which chunk_ids to KEEP vs DROP.

## Priority (highest first)
1. Alignment with Selected Segment (name, description, traits) and Fixed Persona narrative.
2. Internal consistency: chunks must support the SAME behavioral story (one dominant theme).
3. Drop chunks that conflict with the majority (e.g. "20-year loyalty" vs "switch every 6 months for gifts").
4. Prefer chunks from the same source_id when they reinforce the same theme (not required).
5. Keep 3-7 chunks. If unsure, keep fewer but coherent chunks rather than many conflicting ones.

## Rules
- Do NOT clone a real participant as the persona; extract behavior patterns only.
- keep_chunk_ids must reference ids from the candidate list only.
- Write dominant_axes and persona_fit_notes in {target_language}.
- Respond with JSON only.

## Output Format
{{
  "dominant_axes": ["axis 1", "axis 2"],
  "segment_alignment": "high",
  "persona_fit_notes": "Why these chunks fit the persona and segment",
  "keep_chunk_ids": [1, 4, 7],
  "drop_chunk_ids": [2],
  "drop_reasons": {{"2": "Conflicts with segment and other chunks on carrier switching"}}
}}

## Selected Segment
- name: {segment.get("name")}
- description: {segment.get("description")}
- key_traits: {", ".join(str(value) for value in segment_traits)}

## Service Context
{_service_context(payload)}

## Fixed Persona (narrative — do not replace identity)
{_telecom_fixed_persona_json(persona)}

## Candidate Chunks
{candidates_text}
""".strip()


def _normalize_coherence_curation(parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        return {}
    dominant_axes = parsed.get("dominant_axes") or parsed.get("dominantAxes") or []
    if not isinstance(dominant_axes, list):
        dominant_axes = [dominant_axes]
    dominant_axes = [str(value).strip() for value in dominant_axes if str(value).strip()]

    keep_ids = parsed.get("keep_chunk_ids") or parsed.get("keepChunkIds") or []
    drop_ids = parsed.get("drop_chunk_ids") or parsed.get("dropChunkIds") or []
    if not isinstance(keep_ids, list):
        keep_ids = []
    if not isinstance(drop_ids, list):
        drop_ids = []

    drop_reasons = parsed.get("drop_reasons") or parsed.get("dropReasons") or {}
    if not isinstance(drop_reasons, dict):
        drop_reasons = {}

    return {
        "dominant_axes": dominant_axes,
        "segment_alignment": str(parsed.get("segment_alignment") or parsed.get("segmentAlignment") or "").strip(),
        "persona_fit_notes": str(parsed.get("persona_fit_notes") or parsed.get("personaFitNotes") or "").strip(),
        "keep_chunk_ids": keep_ids,
        "drop_chunk_ids": drop_ids,
        "drop_reasons": drop_reasons,
    }


def build_curated_interview_evidence_bundle(
    *,
    vector_service,
    candidate_chunks: list,
    persona: dict,
    segment: dict,
    payload: dict,
    text_generator: Callable[[str], tuple[str, dict]],
) -> dict:
    from reopsai.domain.persona.interview_evidence import (
        apply_coherence_curation,
        empty_curated_evidence_bundle,
        format_candidates_for_coherence_prompt,
        format_curated_bundle_for_prompt,
        search_global_interview_evidence_chunks,
    )

    candidates = search_global_interview_evidence_chunks(
        vector_service=vector_service,
        candidate_chunks=candidate_chunks,
        persona=persona,
        segment=segment,
        payload=payload,
    )
    if not candidates:
        return empty_curated_evidence_bundle()

    curation = {}
    try:

        def validate_coherence(parsed: dict):
            normalized = _normalize_coherence_curation(parsed)
            if not normalized.get("dominant_axes"):
                raise PersonaGenerationQualityError("coherence missing dominant_axes")

        candidates_text = format_candidates_for_coherence_prompt(candidates)
        parsed, _usage = _execute_json_stage(
            text_generator,
            _interview_evidence_coherence_prompt(
                persona,
                payload,
                segment,
                candidates_text=candidates_text,
            ),
            stage_name="interview_evidence_coherence",
            validator=validate_coherence,
            max_attempts=2,
        )
        curation = _normalize_coherence_curation(parsed)
    except Exception as exc:
        _log_persona_generation_event(
            "interview_evidence_coherence_fallback",
            persona=persona.get("name"),
            error=str(exc),
            candidate_count=len(candidates),
        )

    keep_chunks = apply_coherence_curation(candidates, curation)
    keep_ids = {int(item["id"]) for item in keep_chunks if item.get("id") is not None}
    dropped_count = sum(1 for item in candidates if item.get("id") not in keep_ids)

    prompt_text = format_curated_bundle_for_prompt(
        keep_chunks=keep_chunks,
        dominant_axes=curation.get("dominant_axes"),
        persona_fit_notes=curation.get("persona_fit_notes"),
        segment_alignment=curation.get("segment_alignment"),
    )
    return {
        "mode": "curated_bundle",
        "enabled": bool(keep_chunks),
        "candidateCount": len(candidates),
        "chunkCount": len(keep_chunks),
        "droppedCount": dropped_count,
        "dominantAxes": curation.get("dominant_axes") or [],
        "segmentAlignment": curation.get("segment_alignment"),
        "personaFitNotes": curation.get("persona_fit_notes") or "",
        "keepChunks": keep_chunks,
        "promptText": prompt_text,
        "curation": curation,
    }


def _interview_evidence_prompt_text(bundle: dict | None, evidence_by_variable: dict | None = None) -> tuple[str, int]:
    from reopsai.domain.persona.interview_evidence import count_evidence_chunks, format_evidence_for_prompt

    if isinstance(bundle, dict) and (bundle.get("promptText") or bundle.get("prompt_text")):
        text = str(bundle.get("promptText") or bundle.get("prompt_text") or "").strip()
        keep_chunks = bundle.get("keepChunks") or bundle.get("keep_chunks") or []
        return text, len(keep_chunks)
    if isinstance(bundle, dict) and (bundle.get("keepChunks") or bundle.get("keep_chunks")):
        from reopsai.domain.persona.interview_evidence import format_curated_bundle_for_prompt

        keep_chunks = bundle.get("keepChunks") or bundle.get("keep_chunks") or []
        text = format_curated_bundle_for_prompt(
            keep_chunks=keep_chunks,
            dominant_axes=bundle.get("dominantAxes") or bundle.get("dominant_axes"),
            persona_fit_notes=bundle.get("personaFitNotes") or bundle.get("persona_fit_notes"),
            segment_alignment=bundle.get("segmentAlignment") or bundle.get("segment_alignment"),
        )
        return text, len(keep_chunks)
    legacy = evidence_by_variable if evidence_by_variable is not None else (bundle if isinstance(bundle, dict) else {})
    if legacy and not legacy.get("mode"):
        text = format_evidence_for_prompt(legacy)
        return text, count_evidence_chunks(legacy)
    return "", 0


def stage_nemotron_telecom_behavior_dimensions(
    persona: dict,
    payload: dict,
    segment: dict,
    seed: dict,
    text_generator: Callable[[str], tuple[str, dict]],
    *,
    interview_evidence_bundle: dict | None = None,
    interview_evidence_by_variable: dict | None = None,
) -> tuple[dict, dict]:
    evidence_bundle = interview_evidence_bundle
    if evidence_bundle is None and interview_evidence_by_variable:
        evidence_bundle = interview_evidence_by_variable
    evidence_text, evidence_chunk_count = _interview_evidence_prompt_text(
        evidence_bundle,
        evidence_by_variable=interview_evidence_by_variable,
    )
    prompt = _telecom_dimensions_only_prompt(
        persona,
        payload,
        segment,
        seed,
        interview_evidence_text=evidence_text,
    )

    def validate(parsed: dict):
        dimensions = _normalize_telecom_dimensions(
            parsed.get("telecomBehaviorDimensions") or parsed.get("telecom_behavior_dimensions")
        )
        _validate_telecom_dimensions(dimensions)

    parsed, usage = _execute_json_stage(
        text_generator,
        prompt,
        stage_name="telecom_dimensions",
        validator=validate,
        max_attempts=2,
    )
    dimensions = _normalize_telecom_dimensions(
        parsed.get("telecomBehaviorDimensions") or parsed.get("telecom_behavior_dimensions")
    )
    usage = _usage_from(usage)
    usage["telecomStage"] = "dimensions"
    usage["evidenceChunkCount"] = evidence_chunk_count
    usage["evidenceMode"] = "curated_bundle" if evidence_text else "none"
    return dimensions, usage


def stage_nemotron_telecom_behavior_scores(
    persona: dict,
    payload: dict,
    segment: dict,
    dimensions: dict,
    text_generator: Callable[[str], tuple[str, dict]],
) -> tuple[list[dict], dict]:
    prompt = _telecom_scores_prompt(persona, payload, segment, dimensions)

    def validate(parsed: dict):
        scores = _normalize_telecom_scores(parsed.get("telecomBehaviorScores") or parsed.get("telecom_behavior_scores"))
        _validate_telecom_scores(scores)

    parsed, usage = _execute_json_stage(
        text_generator,
        prompt,
        stage_name="telecom_scores",
        validator=validate,
        max_attempts=2,
    )
    scores = _normalize_telecom_scores(parsed.get("telecomBehaviorScores") or parsed.get("telecom_behavior_scores"))
    usage = _usage_from(usage)
    usage["telecomStage"] = "scores"
    return scores, usage


def stage_nemotron_telecom_dimensions(
    persona: dict,
    payload: dict,
    segment: dict,
    seed: dict,
    text_generator: Callable[[str], tuple[str, dict]],
    *,
    interview_evidence_by_variable: dict | None = None,
) -> tuple[dict, dict]:
    dimensions, dimensions_usage = stage_nemotron_telecom_behavior_dimensions(
        persona,
        payload,
        segment,
        seed,
        text_generator,
        interview_evidence_by_variable=interview_evidence_by_variable,
    )
    persona_with_dimensions = {**persona, "telecomBehaviorDimensions": dimensions}
    scores, scores_usage = stage_nemotron_telecom_behavior_scores(
        persona_with_dimensions,
        payload,
        segment,
        dimensions,
        text_generator,
    )
    telecom_usage = _empty_usage()
    _add_usage(telecom_usage, dimensions_usage)
    _add_usage(telecom_usage, scores_usage)
    telecom_usage["telecomDimensionsSource"] = "llm"
    telecom_usage["telecomPipeline"] = "split_dimensions_then_scores"
    if dimensions_usage.get("evidenceChunkCount") is not None:
        telecom_usage["evidenceChunkCount"] = dimensions_usage["evidenceChunkCount"]
    return {
        **persona_with_dimensions,
        "telecomBehaviorScores": scores,
    }, telecom_usage


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
    interview_evidence_retriever: Callable[[dict, dict, dict], dict] | None = None,
    interview_evidence_by_variable: dict | None = None,
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
    _log_persona_generation_event(
        "batch_start",
        personas=len(seeded_drafts),
        max_workers=max_workers,
        telecom_pipeline="split_parallel",
    )

    def phase_narrative_and_dimensions(item: dict):
        selected = item["selected"]
        persona = item["persona"]
        _log_persona_generation_event("worker_start", persona=persona.get("name"), phase="narrative_dimensions")
        try:
            persona, usage = stage_nemotron_seed_narrative_polish(
                persona, payload, selected["segment"], selected["seed"], text_generator
            )
            narrative_usage = usage
            evidence_bundle = interview_evidence_by_variable
            interview_evidence_summary = None
            if interview_evidence_retriever is not None:
                evidence_bundle = interview_evidence_retriever(persona, selected["segment"], payload)
                from reopsai.domain.persona.interview_evidence import summarize_curated_evidence_bundle

                interview_evidence_summary = summarize_curated_evidence_bundle(evidence_bundle)
                _log_persona_generation_event(
                    "interview_evidence_retrieved",
                    persona=persona.get("name"),
                    mode=interview_evidence_summary.get("mode"),
                    chunk_count=interview_evidence_summary.get("chunkCount"),
                    candidate_count=interview_evidence_summary.get("candidateCount"),
                    dominant_axes=interview_evidence_summary.get("dominantAxes"),
                )
            dimensions, dimensions_usage = stage_nemotron_telecom_behavior_dimensions(
                persona,
                payload,
                selected["segment"],
                selected["seed"],
                text_generator,
                interview_evidence_bundle=evidence_bundle if isinstance(evidence_bundle, dict) else None,
            )
            return {
                "selected": selected,
                "persona": {**persona, "telecomBehaviorDimensions": dimensions},
                "narrative_usage": narrative_usage,
                "dimensions_usage": dimensions_usage,
                "interview_evidence_summary": interview_evidence_summary,
            }
        finally:
            _log_persona_generation_event("worker_end", persona=persona.get("name"), phase="narrative_dimensions")

    dimension_results = _map_with_concurrency(seeded_drafts, max_workers, phase_narrative_and_dimensions)

    def phase_scores_and_postprocess(phase_item: dict):
        selected = phase_item["selected"]
        persona = phase_item["persona"]
        _log_persona_generation_event("worker_start", persona=persona.get("name"), phase="telecom_scores")
        try:
            dimensions = persona.get("telecomBehaviorDimensions") or {}
            scores, scores_usage = stage_nemotron_telecom_behavior_scores(
                persona,
                payload,
                selected["segment"],
                dimensions,
                text_generator,
            )
            persona = {**persona, "telecomBehaviorScores": scores}
            telecom_usage = _empty_usage()
            _add_usage(telecom_usage, phase_item.get("dimensions_usage"))
            _add_usage(telecom_usage, scores_usage)
            telecom_usage["telecomDimensionsSource"] = "llm"
            telecom_usage["telecomPipeline"] = "split_parallel"
            evidence_chunk_count = (phase_item.get("interview_evidence_summary") or {}).get("chunkCount") or 0
            if evidence_chunk_count:
                telecom_usage["evidenceChunkCount"] = evidence_chunk_count
            return {
                "persona": persona,
                "narrative_usage": phase_item.get("narrative_usage"),
                "telecom_usage": telecom_usage,
                "interview_evidence_summary": phase_item.get("interview_evidence_summary"),
                "seed_reference": _seed_metadata(selected, persona["name"]),
            }
        finally:
            _log_persona_generation_event("worker_end", persona=persona.get("name"), phase="telecom_scores")

    persona_results = _map_with_concurrency(dimension_results, max_workers, phase_scores_and_postprocess)
    personas = []
    seed_references = []
    interview_evidence_summaries = []
    for result in persona_results:
        _add_usage(total_usage, result.get("narrative_usage"))
        _add_usage(total_usage, result.get("telecom_usage"))
        if result.get("interview_evidence_summary"):
            interview_evidence_summaries.append(
                {
                    "personaName": result["persona"].get("name"),
                    **result["interview_evidence_summary"],
                }
            )
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
            "interviewEvidenceMode": "curated_bundle" if interview_evidence_retriever else "static",
            "interviewEvidenceRetriever": bool(interview_evidence_retriever),
            "interviewEvidenceSummaries": interview_evidence_summaries,
        },
        "token_usage": total_usage,
        "seed_count": len(selected_seeds),
    }
