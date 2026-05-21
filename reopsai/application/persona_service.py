from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import mimetypes
import re
import time
import os
from typing import Any, Mapping, Optional

from reopsai.domain.persona.generation import (
    PersonaGenerationQualityError,
    generate_segment_suggestions_pipeline,
    generate_personas_pipeline,
    infer_persona_source_type,
    validate_generation_payload,
    validate_segment_suggestion_payload,
)
from reopsai.infrastructure.persistence.engine import session_scope
from reopsai.infrastructure.persistence.repositories.persona_repository import PersonaRepository
from reopsai.infrastructure.persona_capture import persona_capture
from reopsai.infrastructure.persona_figma_client import make_oauth_state, persona_figma_client
from reopsai.infrastructure.persona_image_generation import generate_persona_image_data_url
from reopsai.infrastructure.persona_storage import persona_storage
from reopsai.shared.usage_metering import build_llm_usage_context, run_with_llm_usage_context


@dataclass(frozen=True)
class PersonaServiceResult:
    status: str
    data: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None
    status_code: int = 200


class PersonaUrlCaptureError(RuntimeError):
    pass


def _dt(value):
    return value.isoformat() if value else None


def _clean_mapping(value):
    return value if isinstance(value, (dict, list)) else None


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _compact_json(value, *, max_chars: int = 4000):
    if not isinstance(value, (dict, list)):
        return None
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n... [truncated]"


def _clamp_percent(value, fallback: int = 50):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return fallback
    if not isinstance(value, (int, float)):
        return fallback
    return max(0, min(100, round(value)))


def _storage_asset_id_from_url(value):
    if not isinstance(value, str):
        return None
    match = re.search(r"/api/persona/storage/(\d+)", value)
    if not match:
        return None
    return int(match.group(1))


def _camelize_result_aliases(payload: dict) -> dict:
    aliases = {
        "test_id": "testId",
        "ab_test_id": "testId",
        "interview_id": "interviewId",
        "persona_id": "personaId",
        "persona_goal_fit": "personaGoalFit",
        "pin_comments": "pinComments",
        "flow_analysis": "flowAnalysis",
        "persona_snapshot": "personaSnapshot",
        "evidence_ids": "evidenceIds",
        "screen_insights": "screenInsights",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
    }
    for snake, camel in aliases.items():
        if snake in payload and camel not in payload:
            payload[camel] = payload[snake]
    snapshot = payload.get("persona_snapshot") or {}
    if isinstance(snapshot, dict):
        payload.setdefault("personaName", snapshot.get("name") or "알 수 없는 퍼소나")
        payload.setdefault("personaImageUrl", snapshot.get("imageUrl"))
        payload.setdefault("personaTitle", snapshot.get("title") or snapshot.get("roleArea"))
        if "persona" not in payload:
            payload["persona"] = snapshot
    if "error_message" in payload and "error" not in payload:
        payload["error"] = payload["error_message"]
    return payload


def _camelize_record_aliases(payload: dict) -> dict:
    aliases = {
        "company_id": "companyId",
        "created_by_user_id": "createdByUserId",
        "updated_by_user_id": "updatedByUserId",
        "device_type": "deviceType",
        "validation_type": "validationType",
        "scope_type": "scopeType",
        "source_type": "sourceType",
        "persona_count": "personaCount",
        "screen_count": "screenCount",
        "source_data": "sourceData",
        "service_context": "serviceContext",
        "context_data": "contextData",
        "enable_consistency_validation": "enableConsistencyValidation",
        "consistency_run_count": "consistencyRunCount",
        "product_description": "productDescription",
        "question_set": "questionSet",
        "pack_model": "packModel",
        "persona_ids": "personaIds",
        "error_message": "errorMessage",
        "started_at": "startedAt",
        "completed_at": "completedAt",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
    }
    for snake, camel in aliases.items():
        if snake in payload and camel not in payload:
            payload[camel] = payload[snake]
    return payload


def _parse_image_data_url(value: str | None):
    if not value:
        return None
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", value.strip(), re.DOTALL)
    if not match:
        return None
    return match.group(1), base64.b64decode(match.group(2))


class PersonaService:
    def __init__(
        self,
        *,
        repository=PersonaRepository,
        session_factory=session_scope,
        storage=persona_storage,
        figma_client=persona_figma_client,
        capture=persona_capture,
        llm_adapter=None,
        image_generator=generate_persona_image_data_url,
    ):
        self.repository = repository
        self.session_factory = session_factory
        self.storage = storage
        self.figma_client = figma_client
        self.capture = capture
        self.llm_adapter = llm_adapter
        self.image_generator = image_generator

    def _ok(self, data=None, status_code=200):
        return PersonaServiceResult(status="ok", data=data or {}, status_code=status_code)

    def _error(self, status: str, error: str, status_code: int):
        return PersonaServiceResult(status=status, error=error, status_code=status_code)

    def _require_name(self, data: dict):
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        data["name"] = name
        return name

    def _can_modify(self, db_session, record, *, company_id: int, user_id: int):
        return self.repository.can_modify_record(db_session, record, company_id=company_id, user_id=user_id)

    def _get_llm_adapter(self):
        if self.llm_adapter is None:
            from reopsai.infrastructure.gemini_service import GeminiService

            self.llm_adapter = GeminiService()
        return self.llm_adapter

    def _generate_text(self, prompt: str, *, media_parts: Optional[list[dict]] = None) -> tuple[str, dict]:
        model_name = os.getenv("PERSONA_GEMINI_TEXT_MODEL") or "gemini-2.5-pro"
        generation_config = {"temperature": 0.7, "max_output_tokens": 8192}
        if "STAGE: telecom_dimensions" in prompt:
            generation_config = {"temperature": 0.35, "max_output_tokens": 4500}
        elif "STAGE: segment_suggestion" in prompt:
            generation_config = {"temperature": 0.4, "max_output_tokens": 4096}
        adapter = self._get_llm_adapter()
        if media_parts and hasattr(adapter, "generate_multimodal_response"):
            result = adapter.generate_multimodal_response(
                prompt,
                media_parts=media_parts,
                generation_config=generation_config,
                model_name=model_name,
            )
        else:
            result = adapter.generate_response(
                prompt,
                generation_config=generation_config,
                model_name=model_name,
            )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Persona Gemini generation failed")
        usage = dict(result.get("usage") or {})
        usage["model"] = model_name
        if media_parts:
            usage["media_parts"] = len(media_parts)
        return result.get("content") or "", usage

    def _generate_json(self, prompt: str, *, feature_key: str, company_id: int, user_id: int, media_parts: Optional[list[dict]] = None) -> tuple[dict, dict]:
        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key=feature_key)

        def generate():
            text, usage = self._generate_text(prompt, media_parts=media_parts)
            try:
                return json.loads(text), usage
            except Exception:
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if not match:
                    raise ValueError("LLM response did not contain JSON")
                return json.loads(match.group(0)), usage

        return run_with_llm_usage_context(usage_context, generate)

    def _persona_snapshot_payload(self, persona):
        payload = self.persona_payload(persona)
        return {
            "id": payload["id"],
            "name": payload["name"],
            "imageUrl": payload.get("imageUrl"),
            "age": payload.get("age"),
            "generation": payload.get("generation"),
            "gender": payload.get("gender"),
            "title": payload.get("title") or payload.get("roleArea"),
            "sector": payload.get("sector"),
            "organisation": payload.get("organisation"),
            "roleArea": payload.get("roleArea"),
            "roleLevel": payload.get("roleLevel"),
            "language": payload.get("language"),
        }

    def _persona_context(self, persona):
        payload = self.persona_payload(persona)
        field_labels = [
            ("Name", payload.get("name")),
            ("Age", f"{payload.get('age')}세" if payload.get("age") else None),
            ("Generation", payload.get("generation")),
            ("Gender", payload.get("gender")),
            ("Title/Role", payload.get("title") or payload.get("roleArea")),
            ("Sector", payload.get("sector")),
            ("Organisation", payload.get("organisation")),
            ("Role Level", payload.get("roleLevel")),
            ("City/Country", " / ".join(part for part in [payload.get("currentCity"), payload.get("currentCountry")] if part)),
            ("Personality", payload.get("personality")),
            ("Biography", payload.get("biography")),
            ("Attitudes", payload.get("attitudes")),
            ("Behaviours", payload.get("behaviours")),
            ("Motivation", payload.get("motivation")),
            ("Preferences", payload.get("preferences")),
            ("Interests", payload.get("interests")),
            ("Social Context", payload.get("socialContext")),
            ("Cultural Background", payload.get("culturalBackground")),
            ("Quote", payload.get("quote")),
        ]
        parts = [f"{label}: {value}" for label, value in field_labels if value]
        for label, key in (
            ("Profile JSON", "profile"),
            ("Telecom Profile JSON", "telecom_profile"),
            ("Telecom Usage JSON", "telecomUsage"),
            ("Telecom Values JSON", "telecomValues"),
            ("UX Interaction JSON", "uxInteraction"),
            ("Telecom Behavior Dimensions JSON", "telecomBehaviorDimensions"),
            ("Source Data JSON", "sourceData"),
        ):
            text = _compact_json(payload.get(key), max_chars=1600)
            if text:
                parts.append(f"{label}:\n{text}")
        interview_pack = _compact_json(getattr(persona, "interview_pack", None), max_chars=2600)
        if interview_pack:
            parts.append(f"Persona Interview Pack:\n{interview_pack}")
        return "\n".join(part for part in parts if part)

    def _resolve_run_personas(self, db_session, *, company_id: int, explicit_ids=None, source_data=None):
        persona_ids = explicit_ids or []
        if not persona_ids and isinstance(source_data, dict):
            selection = source_data.get("personaSelection") or source_data.get("persona_selection") or {}
            if not selection.get("useAllPersonas") and selection.get("selectedPersonaIds"):
                persona_ids = selection.get("selectedPersonaIds") or []
        if persona_ids:
            return self.repository.list_personas_by_ids(db_session, company_id=company_id, persona_ids=persona_ids)
        return self.repository.list_all_personas(db_session, company_id=company_id)

    def _screen_manifest(self, source_data):
        if not isinstance(source_data, dict):
            source_data = {}
        screens = []
        for entry in _as_list(source_data.get("imageEntries") or source_data.get("image_entries")):
            screens.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name") or entry.get("fileName"),
                    "source": entry.get("imageUrl") or entry.get("image_url"),
                    "sourceType": "image",
                    "fileName": entry.get("fileName") or entry.get("file_name"),
                }
            )
        for entry in _as_list(source_data.get("urlEntries") or source_data.get("url_entries")):
            screens.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name") or entry.get("pageTitle") or entry.get("url"),
                    "source": entry.get("capturedImageUrl") or entry.get("captured_image_url") or entry.get("url"),
                    "sourceType": "url",
                    "url": entry.get("url"),
                    "pageTitle": entry.get("pageTitle") or entry.get("page_title"),
                    "capturedImageUrl": entry.get("capturedImageUrl") or entry.get("captured_image_url"),
                }
            )
        for entry in _as_list(source_data.get("figmaScreens") or source_data.get("figma_screens")):
            screens.append(
                {
                    "id": entry.get("id") or entry.get("figmaNodeId") or entry.get("figma_node_id"),
                    "name": entry.get("name"),
                    "source": entry.get("imageUrl") or entry.get("image_url"),
                    "sourceType": "figma",
                    "figmaNodeId": entry.get("figmaNodeId") or entry.get("figma_node_id"),
                }
            )
        if not screens:
            for entry in _as_list(source_data.get("screens")):
                if not isinstance(entry, dict):
                    continue
                screens.append(
                    {
                        "id": entry.get("id") or entry.get("screenId") or f"screen-{len(screens) + 1}",
                        "name": entry.get("name") or entry.get("label") or entry.get("filename") or f"화면 {len(screens) + 1}",
                        "source": entry.get("imageUrl") or entry.get("image_url") or entry.get("source") or entry.get("url"),
                        "sourceType": source_data.get("sourceType") or source_data.get("source_type"),
                        "url": entry.get("url"),
                    }
                )
        if not screens and isinstance(source_data.get("figma_flow") or source_data.get("figmaFlow"), dict):
            flow = source_data.get("figma_flow") or source_data.get("figmaFlow")
            figma_file = _as_dict(source_data.get("figma_file") or source_data.get("figmaFile"))
            screens.append(
                {
                    "id": flow.get("id") or flow.get("figma_start_node_id") or flow.get("figmaStartNodeId") or "figma-flow",
                    "name": flow.get("figma_flow_name") or flow.get("flowName") or flow.get("figmaFlowName") or "Figma flow",
                    "source": flow.get("imageUrl") or flow.get("thumbnailUrl") or figma_file.get("thumbnail_url") or figma_file.get("thumbnailUrl"),
                    "sourceType": "figma",
                    "figmaNodeId": flow.get("figma_start_node_id") or flow.get("figmaStartNodeId"),
                    "figmaFileName": figma_file.get("figma_file_name") or figma_file.get("fileName") or figma_file.get("figmaFileName"),
                }
            )
        if not screens:
            screens.append({"id": "screen-1", "name": "Provided test source", "source": None})
        return screens

    def _screen_image_source(self, screen: dict):
        return screen.get("capturedImageUrl") or screen.get("source") or screen.get("imageUrl") or screen.get("image_url")

    def _read_screen_media_parts(self, db_session, *, company_id: int, screens):
        media_parts = []
        for screen_index, screen in enumerate(screens):
            source = self._screen_image_source(screen)
            if not source:
                continue
            screen_label = f"screenIndex {screen_index} / 화면 {screen_index + 1} / id={screen.get('id')} / name={screen.get('name') or f'화면 {screen_index + 1}'}"
            if isinstance(source, str) and source.startswith("data:") and ";base64," in source:
                header, data_base64 = source.split(";base64,", 1)
                media_parts.append({"type": "text", "text": screen_label})
                media_parts.append(
                    {
                        "type": "image",
                        "screenIndex": screen_index,
                        "mime_type": header.removeprefix("data:") or "image/png",
                        "data_base64": data_base64,
                    }
                )
                continue

            asset_id = _storage_asset_id_from_url(source)
            if not asset_id:
                continue
            try:
                asset = self.repository.get_asset(db_session, company_id=company_id, asset_id=asset_id)
                if not asset:
                    continue
                path = self.storage.resolve_local_path(asset.storage_key)
                if not path.exists() or not path.is_file():
                    continue
                mime_type = asset.mime_type or mimetypes.guess_type(str(path))[0] or "image/png"
                media_parts.append({"type": "text", "text": screen_label})
                media_parts.append(
                    {
                        "type": "image",
                        "screenIndex": screen_index,
                        "mime_type": mime_type,
                        "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    }
                )
            except Exception:
                continue
        return media_parts

    def _resolve_ui_source_data_for_run(self, *, company_id: int, user_id: int, source_data: dict):
        if not isinstance(source_data, dict):
            return {}
        entries = _as_list(source_data.get("urlEntries") or source_data.get("url_entries"))
        if not entries:
            return source_data
        updated_entries = []
        changed = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("capturedImageUrl") or entry.get("captured_image_url") or not entry.get("url"):
                updated_entries.append(entry)
                continue
            captured = self.capture_url(company_id=company_id, user_id=user_id, url=entry.get("url"))
            if captured.status != "ok":
                raise PersonaUrlCaptureError(captured.error or "URL capture failed")
            captured_data = _as_dict(captured.data).get("data") or {}
            captured_image_url = captured_data.get("capturedImageUrl")
            if not captured_image_url:
                raise PersonaUrlCaptureError("URL capture did not produce a screenshot image")
            entry = {
                **entry,
                "url": captured_data.get("url") or entry.get("url"),
                "pageTitle": captured_data.get("title") or entry.get("pageTitle"),
                "capturedImageUrl": captured_image_url,
                "captureBackend": captured_data.get("capture_backend"),
                "captureStatusCode": captured_data.get("status_code"),
            }
            changed = True
            updated_entries.append(entry)
        if not changed:
            return source_data
        return {
            **source_data,
            "urlEntries": updated_entries,
            "url_entries": updated_entries,
        }

    def _build_ui_prompt(self, *, test, persona, screens):
        is_flow = test.scope_type == "flow" and len(screens) > 1
        source_data = _as_dict(getattr(test, "source_data", None))
        flow_goal = _first_text(source_data.get("flow_goal"), source_data.get("flowGoal"), test.description)
        parts = [
            "You are evaluating a UI from the perspective of the given persona, not as a generic UX reviewer.",
            "Return only valid JSON with keys: summary, personaGoalFit, scores, feedback, pinComments, flowAnalysis, strengths, risks, recommendations, screenInsights.",
            "Every comment must be grounded in this persona's profile, needs, worries, habits, decision rules, or past context. Do not write generic comments that any user could say.",
            "Use natural Korean. Write screenFeedbacks as persona reactions, and write summary/personaGoalFit as a researcher-style synthesis.",
            "Do not invent hard facts outside the persona. Reason from the persona context when the profile is incomplete.",
            "scores must include clarity, usability, appeal, overall as 0-100 integers.",
            "feedback must include overallFeedback and screenFeedbacks. screenFeedbacks must include at least one item for every screenIndex.",
            "pinComments must be an array of concrete image markers with screenIndex, x, y, type, content. type must be one of praise, problem, improvement.",
            "Use praise for positive comments and problem/improvement for negative comments. screenInsights positives/issues must align with the same evidence used in pinComments.",
            "For each screen, provide at least one positive evidence point and one risk/improvement point when possible.",
            "Attached images follow the same order as [Screens]. x and y must point to the actual UI element in the attached image as 0-100 percentage coordinates, where x is left-to-right and y is top-to-bottom.",
            "Do not use generic center coordinates unless the target element is genuinely centered. If exact location is uncertain, choose the most plausible visible element area and name that element in content.",
            is_flow
            and "This is a flow test. Evaluate whether the persona can keep moving toward the task goal across screens; include flowAnalysis for every screenIndex with confusionScore, dropoffRisk, frictionPoints, suggestions, transitionFromPrevious, expectedNextAction, bottleneckRisk.",
            not is_flow and "This is a single-screen test. Do not include flowAnalysis unless it is directly useful.",
            f"Test: {test.name}",
            f"Description: {test.description or ''}",
            f"Scope: {test.scope_type}",
            f"Task/Flow Goal: {flow_goal or ''}",
            "[Persona]",
            self._persona_context(persona),
            "[Screens]",
            json.dumps(screens, ensure_ascii=False),
        ]
        return "\n".join(part for part in parts if part)

    def _fallback_ui_feedback(self, *, test, persona, screens):
        persona_name = getattr(persona, "name", "Persona")
        is_flow = test.scope_type == "flow" and len(screens) > 1
        screen_feedbacks = [
            {"screenIndex": index, "feedback": f"{persona_name} 관점에서 {screen.get('name') or index + 1} 화면의 다음 행동과 정보 구조를 확인했습니다."}
            for index, screen in enumerate(screens)
        ]
        flow_analysis = [
            {
                "screenIndex": index,
                "confusionScore": 35,
                "dropoffRisk": 30,
                "frictionPoints": [],
                "suggestions": ["다음 행동을 더 명확히 표시합니다."],
                "expectedNextAction": "다음 단계로 이동",
                "bottleneckRisk": "low",
            }
            for index, _screen in enumerate(screens)
        ] if is_flow else []
        return {
            "summary": f"{persona_name}님은 주요 정보와 다음 행동을 기준으로 화면을 평가했습니다.",
            "personaGoalFit": "목표 수행에 필요한 핵심 정보를 확인할 수 있습니다.",
            "scores": {"clarity": 70, "usability": 70, "appeal": 65, "overall": 68, "overallFlowScore": 68 if is_flow else None},
            "feedback": {"overallFeedback": f"{persona_name}님은 전반적으로 이해 가능한 흐름으로 평가했습니다.", "screenFeedbacks": screen_feedbacks},
            "pinComments": [
                {"screenIndex": 0, "x": 42, "y": 44, "type": "praise", "content": "핵심 정보 접근이 가능해 화면의 목적을 빠르게 파악할 수 있습니다."},
                {"screenIndex": 0, "x": 58, "y": 52, "type": "improvement", "content": "핵심 CTA와 근거 정보를 더 가깝게 배치하면 다음 행동 판단이 쉬워집니다."},
            ],
            "flowAnalysis": flow_analysis,
            "strengths": ["핵심 정보 접근이 가능합니다."],
            "risks": ["일부 사용자는 다음 행동을 다시 확인할 수 있습니다."],
            "recommendations": ["주요 CTA와 신뢰 근거를 강화합니다."],
            "screenInsights": [{"screenId": str(screens[0].get("id") or "screen-1"), "name": screens[0].get("name") or "화면 1", "positives": ["핵심 정보 접근이 가능합니다."], "issues": ["핵심 CTA와 근거 정보의 근접성이 약합니다."], "recommendation": "핵심 행동을 강조합니다."}],
        }

    def _normalize_ui_pin_type(self, raw_type):
        value = str(raw_type or "").strip().lower()
        if value in {"praise", "positive", "strength"}:
            return "praise"
        if value in {"problem", "negative", "risk", "issue"}:
            return "problem"
        if value == "improvement":
            return "improvement"
        return "improvement"

    def _normalize_ui_pin_comments(self, *, feedback: dict, screens):
        pins = []
        max_screen_index = max(len(screens) - 1, 0)
        for index, item in enumerate(_as_list(feedback.get("pinComments") or feedback.get("pin_comments"))):
            if not isinstance(item, dict):
                continue
            screen_index = item.get("screenIndex", item.get("screen_index", 0))
            try:
                screen_index = int(screen_index)
            except (TypeError, ValueError):
                screen_index = 0
            screen_index = max(0, min(screen_index, max_screen_index))
            pins.append(
                {
                    **item,
                    "screenIndex": screen_index,
                    "x": _clamp_percent(item.get("x"), 42 + (index % 3) * 8),
                    "y": _clamp_percent(item.get("y"), 38 + (index % 3) * 10),
                    "type": self._normalize_ui_pin_type(item.get("type")),
                    "content": str(item.get("content") or item.get("comment") or "").strip(),
                }
            )

        screen_insights = _as_list(feedback.get("screenInsights") or feedback.get("screen_insights"))
        strengths = [str(item).strip() for item in _as_list(feedback.get("strengths")) if str(item).strip()]
        risks = [str(item).strip() for item in _as_list(feedback.get("risks")) if str(item).strip()]

        for screen_index, screen in enumerate(screens or [{"id": "screen-1", "name": "화면 1"}]):
            screen_id = str(screen.get("id") or f"screen-{screen_index + 1}")
            insight = next(
                (
                    item
                    for item in screen_insights
                    if isinstance(item, dict) and str(item.get("screenId") or item.get("screen_id") or "") == screen_id
                ),
                screen_insights[screen_index] if screen_index < len(screen_insights) and isinstance(screen_insights[screen_index], dict) else None,
            )
            has_positive = any(pin.get("screenIndex") == screen_index and pin.get("type") == "praise" for pin in pins)
            has_negative = any(pin.get("screenIndex") == screen_index and pin.get("type") != "praise" for pin in pins)
            positive_text = None
            negative_text = None
            if insight:
                positive_text = next((str(item).strip() for item in _as_list(insight.get("positives")) if str(item).strip()), None)
                negative_text = next((str(item).strip() for item in _as_list(insight.get("issues")) if str(item).strip()), None)
            if not positive_text and screen_index == 0 and strengths:
                positive_text = strengths[0]
            if not negative_text and screen_index == 0 and risks:
                negative_text = risks[0]
            if not has_positive and positive_text:
                pins.append({"screenIndex": screen_index, "x": 36, "y": 34, "type": "praise", "content": positive_text})
            if not has_negative and negative_text:
                pins.append({"screenIndex": screen_index, "x": 62, "y": 54, "type": "improvement", "content": negative_text})

        return [pin for pin in pins if pin.get("content")]

    def _normalize_ui_screen_feedbacks(self, *, feedback: dict, persona, screens):
        feedback_payload = feedback.get("feedback") if isinstance(feedback.get("feedback"), dict) else {}
        existing = []
        for item in _as_list(feedback_payload.get("screenFeedbacks") or feedback_payload.get("screen_feedbacks")):
            if not isinstance(item, dict):
                continue
            try:
                screen_index = int(item.get("screenIndex", item.get("screen_index", 0)))
            except (TypeError, ValueError):
                screen_index = 0
            if screen_index < 0 or screen_index >= max(1, len(screens)):
                continue
            text = _first_text(item.get("feedback"), item.get("content"), item.get("comment"))
            if not text:
                continue
            existing.append({**item, "screenIndex": screen_index, "feedback": text})

        covered = {item["screenIndex"] for item in existing}
        persona_name = getattr(persona, "name", "이 퍼소나")
        for screen_index, screen in enumerate(screens):
            if screen_index in covered:
                continue
            screen_name = screen.get("name") or f"화면 {screen_index + 1}"
            existing.append(
                {
                    "screenIndex": screen_index,
                    "feedback": f"{persona_name}님은 {screen_name}에서 핵심 정보와 다음 행동이 자신의 판단 기준에 맞는지 먼저 확인했을 가능성이 큽니다.",
                }
            )
        return sorted(existing, key=lambda item: item["screenIndex"])

    def _normalize_ui_flow_analysis(self, *, feedback: dict, screens, is_flow: bool):
        if not is_flow:
            return []
        normalized = []
        for item in _as_list(feedback.get("flowAnalysis") or feedback.get("flow_analysis")):
            if not isinstance(item, dict):
                continue
            try:
                screen_index = int(item.get("screenIndex", item.get("screen_index", 0)))
            except (TypeError, ValueError):
                screen_index = 0
            if screen_index < 0 or screen_index >= max(1, len(screens)):
                continue
            normalized.append(
                {
                    **item,
                    "screenIndex": screen_index,
                    "confusionScore": int(item.get("confusionScore", item.get("confusion_score", 35)) or 35),
                    "dropoffRisk": int(item.get("dropoffRisk", item.get("dropoff_risk", 30)) or 30),
                    "frictionPoints": _as_list(item.get("frictionPoints") or item.get("friction_points")),
                    "suggestions": _as_list(item.get("suggestions")),
                    "transitionFromPrevious": item.get("transitionFromPrevious") or item.get("transition_from_previous"),
                    "expectedNextAction": item.get("expectedNextAction") or item.get("expected_next_action"),
                    "bottleneckRisk": item.get("bottleneckRisk") or item.get("bottleneck_risk") or "low",
                }
            )
        covered = {item["screenIndex"] for item in normalized}
        for screen_index, screen in enumerate(screens):
            if screen_index in covered:
                continue
            screen_name = screen.get("name") or f"화면 {screen_index + 1}"
            normalized.append(
                {
                    "screenIndex": screen_index,
                    "confusionScore": 35,
                    "dropoffRisk": 30,
                    "frictionPoints": [f"{screen_name}에서 다음 행동을 확신할 근거가 부족할 수 있습니다."],
                    "suggestions": [f"{screen_name}에서 목표 수행에 필요한 다음 행동과 상태 변화를 더 분명히 보여줍니다."],
                    "transitionFromPrevious": None if screen_index == 0 else f"이전 단계의 선택 결과가 {screen_name}에 이어지는지 확인해야 합니다.",
                    "expectedNextAction": f"{screen_name}의 핵심 CTA 또는 탐색 경로를 확인합니다.",
                    "bottleneckRisk": "low",
                }
            )
        return sorted(normalized, key=lambda item: item["screenIndex"])

    def _normalize_ui_screen_insights(self, *, feedback: dict, screens, pin_comments, screen_feedbacks):
        existing = _as_list(feedback.get("screenInsights") or feedback.get("screen_insights"))
        insights = []
        for screen_index, screen in enumerate(screens):
            screen_id = str(screen.get("id") or f"screen-{screen_index + 1}")
            source = next(
                (
                    item
                    for item in existing
                    if isinstance(item, dict) and str(item.get("screenId") or item.get("screen_id") or "") == screen_id
                ),
                existing[screen_index] if screen_index < len(existing) and isinstance(existing[screen_index], dict) else {},
            )
            positives = [str(item).strip() for item in _as_list(_as_dict(source).get("positives")) if str(item).strip()]
            issues = [str(item).strip() for item in _as_list(_as_dict(source).get("issues")) if str(item).strip()]
            positives.extend(
                pin["content"]
                for pin in pin_comments
                if pin.get("screenIndex") == screen_index and pin.get("type") == "praise" and pin.get("content")
            )
            issues.extend(
                pin["content"]
                for pin in pin_comments
                if pin.get("screenIndex") == screen_index and pin.get("type") != "praise" and pin.get("content")
            )
            if not positives and screen_feedbacks:
                text = next((item["feedback"] for item in screen_feedbacks if item.get("screenIndex") == screen_index), None)
                if text:
                    positives.append(text)
            insights.append(
                {
                    "screenId": screen_id,
                    "name": screen.get("name") or f"화면 {screen_index + 1}",
                    "positives": list(dict.fromkeys(positives))[:3],
                    "issues": list(dict.fromkeys(issues))[:3],
                    "recommendation": _as_dict(source).get("recommendation")
                    or (issues[0] if issues else "퍼소나의 판단 기준에 맞는 핵심 근거를 더 명확히 보여줍니다."),
                }
            )
        return insights

    def _run_ui_persona_evaluation(self, *, company_id: int, user_id: int, test, persona, screens, media_parts: Optional[list[dict]] = None):
        try:
            parsed, usage = self._generate_json(
                self._build_ui_prompt(test=test, persona=persona, screens=screens),
                feature_key="persona_ui_test",
                company_id=company_id,
                user_id=user_id,
                media_parts=media_parts,
            )
        except ValueError:
            parsed, usage = self._fallback_ui_feedback(test=test, persona=persona, screens=screens), {"model": "fallback"}
        feedback = parsed if isinstance(parsed, dict) else self._fallback_ui_feedback(test=test, persona=persona, screens=screens)
        scores = feedback.get("scores") if isinstance(feedback.get("scores"), dict) else {}
        summary = feedback.get("summary") or feedback.get("overallFeedback") or "UI test run completed"
        is_flow = test.scope_type == "flow" and len(screens) > 1
        screen_feedbacks = self._normalize_ui_screen_feedbacks(feedback=feedback, persona=persona, screens=screens)
        pin_comments = self._normalize_ui_pin_comments(feedback=feedback, screens=screens)
        flow_analysis = self._normalize_ui_flow_analysis(feedback=feedback, screens=screens, is_flow=is_flow)
        screen_insights = self._normalize_ui_screen_insights(
            feedback=feedback,
            screens=screens,
            pin_comments=pin_comments,
            screen_feedbacks=screen_feedbacks,
        )
        feedback_payload = feedback.get("feedback") if isinstance(feedback.get("feedback"), dict) else {}
        feedback_payload = {
            **feedback_payload,
            "overallFeedback": feedback_payload.get("overallFeedback") or summary,
            "screenFeedbacks": screen_feedbacks,
        }
        return {
            "summary": summary,
            "persona_goal_fit": feedback.get("personaGoalFit") or feedback.get("persona_goal_fit"),
            "scores": {
                "clarity": int(scores.get("clarity", 70)),
                "usability": int(scores.get("usability", 70)),
                "appeal": int(scores.get("appeal", 65)),
                "overall": int(scores.get("overall", scores.get("overallFlowScore", 68))),
                "overallFlowScore": scores.get("overallFlowScore"),
                "flowSummary": scores.get("flowSummary"),
            },
            "feedback": feedback_payload,
            "pin_comments": pin_comments,
            "flow_analysis": flow_analysis,
            "persona_snapshot": self._persona_snapshot_payload(persona),
            "confidence": {
                "model": usage.get("model"),
                "promptVersion": "persona_test_v2",
                "screenCoverage": {
                    "screens": len(screens),
                    "screenFeedbacks": len({item.get("screenIndex") for item in screen_feedbacks}),
                    "pinComments": len(pin_comments),
                    "flowAnalysis": len({item.get("screenIndex") for item in flow_analysis}),
                    "imageEvidenceScreens": len({part.get("screenIndex") for part in _as_list(media_parts) if isinstance(part, dict) and part.get("type") == "image"}),
                },
            },
            "evidence_ids": ["promptVersion:persona_test_v2"],
            "strengths": _as_list(feedback.get("strengths")),
            "risks": _as_list(feedback.get("risks")),
            "recommendations": _as_list(feedback.get("recommendations")),
            "screen_insights": screen_insights,
            "raw_response": {"parsed": feedback, "usage": usage},
        }

    def _build_ab_prompt(self, *, test, persona):
        return "\n".join(
            [
                "You are comparing A/B UX variants from the perspective of the given persona.",
                "Return only JSON with keys: scores, feedback. scores must include winner(A/B/tie) and reasonForChoice.",
                "For flow tests include journeyComparison and stepAnalysis inside scores.",
                f"Test: {test.name}",
                f"Purpose: {test.purpose or ''}",
                f"Mode: {test.mode}",
                f"Screens: {json.dumps(test.screens or {}, ensure_ascii=False)}",
                f"Context: {json.dumps(test.context_data or {}, ensure_ascii=False)}",
                "[Persona]",
                self._persona_context(persona),
            ]
        )

    def _fallback_ab_feedback(self, *, test, persona):
        persona_name = getattr(persona, "name", "Persona")
        is_flow = test.mode == "flow"
        scores = {
            "winner": "tie",
            "reasonForChoice": f"{persona_name}님 관점에서 두 안 모두 장단점이 있어 명확한 우위를 판단하기 어렵습니다.",
        }
        if is_flow:
            scores.update(
                {
                    "journeyComparison": {
                        "flowARating": 65,
                        "flowBRating": 65,
                        "goalAchievementEase": {"flowA": 65, "flowB": 65},
                        "navigationConfidence": {"flowA": 65, "flowB": 65},
                        "estimatedCompletionSpeed": "same",
                        "criticalDropoffStep": {"flowA": None, "flowB": None},
                    },
                    "stepAnalysis": [],
                    "overallFeedback": "두 플로우 모두 목표 수행은 가능하지만 확신을 높일 근거가 더 필요합니다.",
                }
            )
        return {"scores": scores, "feedback": ["A안과 B안 모두 핵심 정보를 비교할 수 있지만 결정적 차이는 약합니다."]}

    def _run_ab_persona_evaluation(self, *, company_id: int, user_id: int, test, persona):
        try:
            parsed, usage = self._generate_json(
                self._build_ab_prompt(test=test, persona=persona),
                feature_key="persona_ab_test",
                company_id=company_id,
                user_id=user_id,
            )
        except ValueError:
            parsed, usage = self._fallback_ab_feedback(test=test, persona=persona), {"model": "fallback"}
        feedback = parsed if isinstance(parsed, dict) else self._fallback_ab_feedback(test=test, persona=persona)
        scores = feedback.get("scores") if isinstance(feedback.get("scores"), dict) else self._fallback_ab_feedback(test=test, persona=persona)["scores"]
        return {
            "persona_snapshot": self._persona_snapshot_payload(persona),
            "scores": scores,
            "feedback": _as_list(feedback.get("feedback")) or [scores.get("reasonForChoice", "비교 평가가 완료되었습니다.")],
            "confidence": {"model": usage.get("model"), "promptVersion": "persona_test_v2"},
            "evidence_ids": ["promptVersion:persona_test_v2"],
            "raw_response": {"parsed": feedback, "usage": usage},
        }

    def _ab_summary(self, results: list[dict], mode: str):
        total = len(results)
        vote_a = sum(1 for result in results if (result.get("scores") or {}).get("winner") == "A")
        vote_b = sum(1 for result in results if (result.get("scores") or {}).get("winner") == "B")
        winner = "tie"
        if vote_a > vote_b:
            winner = "A"
        elif vote_b > vote_a:
            winner = "B"
        summary = {
            "voteA": vote_a,
            "voteB": vote_b,
            "percentA": round((vote_a / total) * 100) if total else 0,
            "percentB": round((vote_b / total) * 100) if total else 0,
            "totalVotes": total,
            "winner": winner,
        }
        if mode == "flow":
            flow_a = []
            flow_b = []
            step_votes = {}
            for result in results:
                scores = result.get("scores") or {}
                journey = scores.get("journeyComparison") or {}
                if isinstance(journey.get("flowARating"), (int, float)):
                    flow_a.append(journey["flowARating"])
                if isinstance(journey.get("flowBRating"), (int, float)):
                    flow_b.append(journey["flowBRating"])
                for step in _as_list(scores.get("stepAnalysis")):
                    index = int(step.get("stepIndex") or 0)
                    step_votes.setdefault(index, {"stepIndex": index, "voteA": 0, "voteB": 0, "voteTie": 0})
                    preferred = step.get("preferredVersion")
                    if preferred == "A":
                        step_votes[index]["voteA"] += 1
                    elif preferred == "B":
                        step_votes[index]["voteB"] += 1
                    else:
                        step_votes[index]["voteTie"] += 1
            summary["flowMetrics"] = {
                "avgFlowARating": round(sum(flow_a) / len(flow_a)) if flow_a else 0,
                "avgFlowBRating": round(sum(flow_b) / len(flow_b)) if flow_b else 0,
                "stepPreferences": list(step_votes.values()),
            }
        return summary

    def _interview_question_set(self, *, goal: str, product_description: str | None, length: str):
        count = 4 if length == "quick" else 7
        base_questions = [
            "이 제품이나 서비스에서 가장 먼저 확인하고 싶은 점은 무엇인가요?",
            "사용 과정에서 불안하거나 망설일 만한 부분은 무엇인가요?",
            "현재 생활 맥락에서 가장 유용하게 느껴질 조건은 무엇인가요?",
            "개선된다면 더 신뢰하거나 자주 사용할 부분은 무엇인가요?",
            "비슷한 대안을 고를 때 비교하는 기준은 무엇인가요?",
            "주변 사람에게 추천하거나 말릴 상황은 언제인가요?",
            "마지막으로 꼭 전달하고 싶은 기대나 우려는 무엇인가요?",
        ]
        return {
            "goal": goal,
            "productDescription": product_description,
            "length": length,
            "questions": [{"id": f"q{index + 1}", "text": text} for index, text in enumerate(base_questions[:count])],
        }

    def _run_interview_for_persona(self, *, company_id: int, user_id: int, interview, persona):
        question_set = interview.question_set or self._interview_question_set(
            goal=interview.goal,
            product_description=interview.product_description,
            length=interview.length,
        )
        prompt = "\n".join(
            [
                "You are conducting a 1:1 AI interview with the given persona.",
                "Return only JSON with keys: summary, turns. turns is an array of question, answer objects.",
                f"Goal: {interview.goal}",
                f"Product: {interview.product_description or ''}",
                f"Questions: {json.dumps(question_set.get('questions') or [], ensure_ascii=False)}",
                "[Persona]",
                self._persona_context(persona),
            ]
        )
        try:
            parsed, usage = self._generate_json(prompt, feature_key="persona_interview", company_id=company_id, user_id=user_id)
        except ValueError:
            parsed, usage = {
                "summary": {"insights": [f"{persona.name}님은 목표와 신뢰 근거를 중심으로 답변했습니다."]},
                "turns": [{"question": item["text"], "answer": "제 상황에서는 근거와 다음 행동이 명확해야 신뢰할 수 있습니다."} for item in question_set.get("questions", [])],
            }, {"model": "fallback"}
        return {
            "persona_snapshot": self._persona_snapshot_payload(persona),
            "summary": parsed.get("summary") if isinstance(parsed, dict) else None,
            "turns": _as_list(parsed.get("turns")) if isinstance(parsed, dict) else [],
            "pack": {"persona": self._persona_snapshot_payload(persona), "questionSet": question_set},
            "raw_response": {"parsed": parsed, "usage": usage},
        }

    def _preview_image_timeout(self) -> int:
        try:
            return max(1, int(os.getenv("PERSONA_IMAGE_GENERATION_TIMEOUT_SECONDS", "45")))
        except Exception:
            return 45

    def _generate_preview_image(self, persona: dict):
        try:
            return self.image_generator(persona, timeout=self._preview_image_timeout())
        except TypeError:
            return self.image_generator(persona)

    def _merge_persona_summaries(self, *summary_groups):
        merged = []
        seen = set()
        for group in summary_groups:
            for item in group or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                merged.append(
                    {
                        "name": name,
                        "age": item.get("age"),
                        "generation": item.get("generation"),
                        "title": item.get("title"),
                        "roleArea": item.get("roleArea"),
                        "personality": item.get("personality"),
                    }
                )
        return merged

    def folder_payload(self, folder):
        return {
            "id": folder.id,
            "company_id": folder.company_id,
            "team_id": folder.team_id,
            "name": folder.name,
            "description": folder.description,
            "color": folder.color,
            "is_default": bool(folder.is_default),
            "created_by_user_id": folder.created_by_user_id,
            "created_at": _dt(folder.created_at),
            "updated_at": _dt(folder.updated_at),
        }

    def persona_payload(self, persona):
        source_data = _clean_mapping(persona.source_data)
        locale = None
        if isinstance(source_data, dict) and isinstance(source_data.get("locale"), dict):
            locale = source_data["locale"]
        elif persona.locale:
            locale = {"country": persona.locale, "language": persona.language}
        return {
            "id": persona.id,
            "schemaVersion": persona.schema_version,
            "company_id": persona.company_id,
            "team_id": persona.team_id,
            "folder_id": persona.folder_id,
            "folderId": str(persona.folder_id) if persona.folder_id is not None else None,
            "created_by_user_id": persona.created_by_user_id,
            "name": persona.name,
            "gender": persona.gender,
            "title": persona.title,
            "personality": persona.personality,
            "language": persona.language,
            "source_type": persona.source_type,
            "sourceType": persona.source_type,
            "source_data": source_data,
            "sourceData": source_data,
            "image_asset_id": persona.image_asset_id,
            "image_url": persona.image_url,
            "imageUrl": persona.image_url,
            "imageData": None,
            "imageMimeType": persona.image_mime_type,
            "image_prompt": persona.image_prompt,
            "imagePrompt": persona.image_prompt,
            "schema_version": persona.schema_version,
            "locale": locale,
            "age": persona.age,
            "profile": _clean_mapping(persona.profile),
            "telecom_profile": _clean_mapping(persona.telecom_profile),
            "income": persona.income,
            "sector": persona.sector,
            "generation": persona.generation,
            "ethnicity": persona.ethnicity,
            "currentCity": persona.current_city,
            "currentCountry": persona.current_country,
            "locations": _clean_mapping(persona.locations),
            "organisation": persona.organisation,
            "roleArea": persona.role_area,
            "roleLevel": persona.role_level,
            "attitudes": persona.attitudes,
            "biography": persona.biography,
            "demeanour": persona.demeanour,
            "interests": persona.interests,
            "behaviours": persona.behaviours,
            "motivation": persona.motivation,
            "upbringing": persona.upbringing,
            "preferences": persona.preferences,
            "socialContext": persona.social_context,
            "culturalBackground": persona.cultural_background,
            "quote": persona.quote,
            "additionalInfo": persona.additional_info,
            "telecomUsage": _clean_mapping(persona.telecom_usage),
            "telecomValues": _clean_mapping(persona.telecom_values),
            "uxInteraction": _clean_mapping(persona.ux_interaction),
            "telecomBehaviorDimensions": _clean_mapping(persona.telecom_behavior_dimensions),
            "generation_metadata": _clean_mapping(persona.generation_metadata),
            "created_at": _dt(persona.created_at),
            "createdAt": _dt(persona.created_at),
            "updated_at": _dt(persona.updated_at),
            "updatedAt": _dt(persona.updated_at),
        }

    def memory_settings_payload(self, settings):
        if not settings:
            return None
        return {
            "id": settings.id,
            "personaId": settings.persona_id,
            "enableMemory": bool(settings.enable_memory),
            "memoryStrength": settings.memory_strength,
            "applyToChat": bool(settings.apply_to_chat),
            "applyToTests": bool(settings.apply_to_tests),
            "createdAt": _dt(settings.created_at),
            "updatedAt": _dt(settings.updated_at),
        }

    def activity_record_payload(self, row):
        return {
            "id": row.id,
            "personaId": row.persona_id,
            "activityType": row.activity_type,
            "activityId": row.activity_id,
            "summary": row.summary,
            "wasValidated": bool(row.was_validated),
            "wasCorrect": row.was_correct,
            "createdAt": _dt(row.created_at),
        }

    def trait_record_payload(self, row):
        return {
            "id": row.id,
            "personaId": row.persona_id,
            "trait": row.trait,
            "category": row.category,
            "confidence": row.confidence,
            "sourceCount": row.source_count,
            "sources": _clean_mapping(row.sources) or [],
            "isActive": bool(row.is_active),
            "createdAt": _dt(row.created_at),
            "updatedAt": _dt(row.updated_at),
        }

    def activity_stats_payload(self, activities):
        validated = sum(1 for row in activities if row.was_validated)
        correct = sum(1 for row in activities if row.was_correct is True)
        incorrect = sum(1 for row in activities if row.was_correct is False)
        return {
            "total": len(activities),
            "byType": {
                "ui_test": sum(1 for row in activities if row.activity_type == "ui_test"),
            },
            "validated": validated,
            "correct": correct,
            "incorrect": incorrect,
        }

    def _persona_create_data_from_generated(self, persona: dict, *, source_type: str = "manual", source_data=None, locale=None, folder_id=None):
        return {
            "folder_id": int(folder_id) if folder_id else None,
            "name": persona.get("name"),
            "gender": persona.get("gender"),
            "title": persona.get("title"),
            "personality": persona.get("personality"),
            "language": (locale or {}).get("language") or persona.get("language") or "ko",
            "source_type": source_type,
            "source_data": source_data,
            "image_url": persona.get("imageUrl"),
            "image_prompt": persona.get("imagePrompt"),
            "schema_version": persona.get("schemaVersion") or 3,
            "locale": (locale or {}).get("country") if isinstance(locale, dict) else None,
            "age": persona.get("age"),
            "income": persona.get("income"),
            "sector": persona.get("sector"),
            "generation": persona.get("generation"),
            "ethnicity": persona.get("ethnicity"),
            "current_city": persona.get("currentCity"),
            "current_country": persona.get("currentCountry"),
            "locations": persona.get("locations"),
            "organisation": persona.get("organisation"),
            "role_area": persona.get("roleArea"),
            "role_level": persona.get("roleLevel"),
            "attitudes": persona.get("attitudes"),
            "biography": persona.get("biography"),
            "demeanour": persona.get("demeanour"),
            "interests": persona.get("interests"),
            "behaviours": persona.get("behaviours"),
            "motivation": persona.get("motivation"),
            "upbringing": persona.get("upbringing"),
            "preferences": persona.get("preferences"),
            "social_context": persona.get("socialContext"),
            "cultural_background": persona.get("culturalBackground"),
            "quote": persona.get("quote"),
            "additional_info": persona.get("additionalInfo") or persona.get("additional_info"),
            "telecom_usage": persona.get("telecomUsage"),
            "telecom_values": persona.get("telecomValues"),
            "ux_interaction": persona.get("uxInteraction"),
            "telecom_behavior_dimensions": persona.get("telecomBehaviorDimensions"),
            "profile": None,
            "telecom_profile": None,
            "generation_metadata": persona.get("generationMetadata") or persona.get("generation_metadata"),
        }

    def _persist_persona_image_if_needed(self, db_session, *, company_id: int, user_id: int, persona_data: dict):
        parsed = _parse_image_data_url(persona_data.get("image_url"))
        if not parsed:
            return persona_data
        mime_type, image_bytes = parsed
        extension = mime_type.split("/")[-1].split("+")[0] or "png"
        storage_data = self.storage.save_bytes(
            image_bytes,
            company_id=company_id,
            filename=f"{persona_data.get('name') or 'persona'}.{extension}",
            mime_type=mime_type,
            asset_type="persona_image",
        )
        asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
        return {
            **persona_data,
            "image_asset_id": asset.id,
            "image_url": f"/api/persona/storage/{asset.id}",
            "image_data": image_bytes,
            "image_mime_type": mime_type,
        }

    def memory_payload(self, settings, activities, traits):
        return {
            "settings": {
                "id": settings.id,
                "persona_id": settings.persona_id,
                "enable_memory": bool(settings.enable_memory),
                "memory_strength": settings.memory_strength,
                "apply_to_chat": bool(settings.apply_to_chat),
                "apply_to_tests": bool(settings.apply_to_tests),
                "created_at": _dt(settings.created_at),
                "updated_at": _dt(settings.updated_at),
            }
            if settings
            else None,
            "activities": [
                {
                    "id": row.id,
                    "activity_type": row.activity_type,
                    "activity_id": row.activity_id,
                    "summary": row.summary,
                    "was_validated": bool(row.was_validated),
                    "was_correct": row.was_correct,
                    "metadata": _clean_mapping(row.metadata_),
                    "created_at": _dt(row.created_at),
                }
                for row in activities
            ],
            "learned_traits": [
                {
                    "id": row.id,
                    "trait": row.trait,
                    "category": row.category,
                    "confidence": row.confidence,
                    "source_count": row.source_count,
                    "sources": _clean_mapping(row.sources),
                    "created_at": _dt(row.created_at),
                    "updated_at": _dt(row.updated_at),
                }
                for row in traits
            ],
        }

    def asset_payload(self, asset):
        return {
            "id": asset.id,
            "asset_type": asset.asset_type,
            "mime_type": asset.mime_type,
            "byte_size": asset.byte_size,
            "original_filename": asset.original_filename,
            "url": f"/api/persona/storage/{asset.id}",
            "created_at": _dt(asset.created_at),
        }

    def ui_test_payload(self, test):
        return _camelize_record_aliases({
            "id": test.id,
            "company_id": test.company_id,
            "name": test.name,
            "description": test.description,
            "device_type": test.device_type,
            "validation_type": test.validation_type,
            "scope_type": test.scope_type,
            "source_type": test.source_type,
            "status": test.status,
            "progress": test.progress,
            "error_message": test.error_message,
            "persona_count": test.persona_count,
            "screen_count": test.screen_count,
            "summary": _clean_mapping(test.summary),
            "source_data": _clean_mapping(test.source_data),
            "created_at": _dt(test.created_at),
            "updated_at": _dt(test.updated_at),
            "started_at": _dt(getattr(test, "started_at", None)),
            "completed_at": _dt(getattr(test, "completed_at", None)),
        })

    def ui_result_payload(self, result):
        return _camelize_result_aliases({
            "id": result.id,
            "test_id": result.test_id,
            "persona_id": result.persona_id,
            "status": result.status,
            "summary": result.summary,
            "persona_goal_fit": result.persona_goal_fit,
            "scores": _clean_mapping(result.scores),
            "feedback": _clean_mapping(result.feedback),
            "pin_comments": _clean_mapping(result.pin_comments) or [],
            "flow_analysis": _clean_mapping(result.flow_analysis) or [],
            "persona_snapshot": _clean_mapping(result.persona_snapshot),
            "confidence": _clean_mapping(result.confidence),
            "evidence_ids": _clean_mapping(result.evidence_ids) or [],
            "strengths": _clean_mapping(result.strengths) or [],
            "risks": _clean_mapping(result.risks) or [],
            "recommendations": _clean_mapping(result.recommendations) or [],
            "screen_insights": _clean_mapping(result.screen_insights) or [],
            "evidence": _clean_mapping(result.evidence),
            "raw_response": _clean_mapping(result.raw_response),
            "error_message": result.error_message,
            "created_at": _dt(result.created_at),
            "updated_at": _dt(result.updated_at),
        })

    def ab_test_payload(self, test):
        return _camelize_record_aliases({
            "id": test.id,
            "company_id": test.company_id,
            "name": test.name,
            "purpose": test.purpose,
            "service_context": test.service_context,
            "mode": test.mode,
            "screens": _clean_mapping(test.screens),
            "transitions": _clean_mapping(test.transitions),
            "context_data": _clean_mapping(test.context_data),
            "summary": _clean_mapping(test.summary),
            "status": test.status,
            "progress": test.progress,
            "error_message": test.error_message,
            "enable_consistency_validation": bool(test.enable_consistency_validation),
            "consistency_run_count": test.consistency_run_count,
            "created_at": _dt(test.created_at),
            "updated_at": _dt(test.updated_at),
        })

    def ab_result_payload(self, result):
        return _camelize_result_aliases({
            "id": result.id,
            "ab_test_id": result.ab_test_id,
            "persona_id": result.persona_id,
            "status": result.status,
            "persona_snapshot": _clean_mapping(result.persona_snapshot),
            "scores": _clean_mapping(result.scores),
            "feedback": _clean_mapping(result.feedback),
            "confidence": _clean_mapping(result.confidence),
            "evidence_ids": _clean_mapping(result.evidence_ids),
            "raw_response": _clean_mapping(result.raw_response),
            "error_message": result.error_message,
            "created_at": _dt(result.created_at),
            "updated_at": _dt(result.updated_at),
        })

    def interview_payload(self, interview):
        return _camelize_record_aliases({
            "id": interview.id,
            "company_id": interview.company_id,
            "name": interview.name,
            "goal": interview.goal,
            "product_description": interview.product_description,
            "length": interview.length,
            "question_set": _clean_mapping(interview.question_set),
            "model": interview.model,
            "pack_model": interview.pack_model,
            "status": interview.status,
            "progress": interview.progress,
            "persona_ids": _clean_mapping(interview.persona_ids) or [],
            "summary": _clean_mapping(interview.summary),
            "error_message": interview.error_message,
            "started_at": _dt(interview.started_at),
            "completed_at": _dt(interview.completed_at),
            "created_at": _dt(interview.created_at),
            "updated_at": _dt(interview.updated_at),
        })

    def interview_result_payload(self, result):
        return _camelize_result_aliases({
            "id": result.id,
            "interview_id": result.interview_id,
            "persona_id": result.persona_id,
            "status": result.status,
            "persona_snapshot": _clean_mapping(result.persona_snapshot),
            "summary": _clean_mapping(result.summary),
            "turns": _clean_mapping(result.turns) or [],
            "pack": _clean_mapping(result.pack),
            "raw_response": _clean_mapping(result.raw_response),
            "error_message": result.error_message,
            "created_at": _dt(result.created_at),
            "updated_at": _dt(result.updated_at),
        })

    def figma_account_payload(self, account):
        return {
            "connected": bool(account),
            "account": {
                "id": account.id,
                "figma_user_id": account.figma_user_id,
                "figma_email": account.figma_email,
                "figma_handle": account.figma_handle,
                "figma_avatar_url": account.figma_avatar_url,
                "expires_at": _dt(account.expires_at),
                "updated_at": _dt(account.updated_at),
            }
            if account
            else None,
        }

    def figma_file_payload(self, row):
        return {
            "id": row.id,
            "figma_file_key": row.figma_file_key,
            "figma_file_name": row.figma_file_name,
            "figma_file_link": row.figma_file_link,
            "thumbnail_url": row.thumbnail_url,
            "last_synced_at": _dt(row.last_synced_at),
            "sync_status": row.sync_status,
            "sync_error": row.sync_error,
        }

    def figma_flow_payload(self, row):
        return {
            "id": row.id,
            "figma_file_id": row.figma_file_id,
            "figma_page_id": row.figma_page_id,
            "figma_page_name": row.figma_page_name,
            "figma_start_node_id": row.figma_start_node_id,
            "figma_flow_name": row.figma_flow_name,
            "metadata": _clean_mapping(row.metadata_),
        }

    def list_folders(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.folder_payload(row) for row in self.repository.list_folders(db_session, company_id=company_id)]})

    def create_folder(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        with self.session_factory() as db_session:
            folder = self.repository.create_folder(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.folder_payload(folder)}, 201)

    def update_folder(self, *, company_id: int, user_id: int, folder_id: int, data: dict):
        with self.session_factory() as db_session:
            folder = self.repository.get_folder(db_session, company_id=company_id, folder_id=folder_id)
            if not folder:
                return self._error("not_found", "folder not found", 404)
            if not self._can_modify(db_session, folder, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            updated = self.repository.update_folder(db_session, folder, user_id=user_id, data=data)
            return self._ok({"data": self.folder_payload(updated)})

    def delete_folder(self, *, company_id: int, user_id: int, folder_id: int):
        with self.session_factory() as db_session:
            folder = self.repository.get_folder(db_session, company_id=company_id, folder_id=folder_id)
            if not folder:
                return self._error("not_found", "folder not found", 404)
            if not self._can_modify(db_session, folder, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_folder(db_session, folder, user_id=user_id)
            return self._ok()

    def list_personas(self, *, company_id: int, page: int, limit: int, search=None, folder_id=None, no_folder=False):
        with self.session_factory() as db_session:
            items, total = self.repository.list_personas(
                db_session,
                company_id=company_id,
                page=page,
                limit=limit,
                search=search,
                folder_id=folder_id,
                no_folder=no_folder,
            )
            return self._ok({"data": [self.persona_payload(row) for row in items], "pagination": {"page": page, "limit": limit, "total": total}})

    def create_persona(self, *, company_id: int, user_id: int, data: dict):
        if "persona" in data and isinstance(data["persona"], dict):
            data = self._persona_create_data_from_generated(
                data["persona"],
                source_type="manual",
                source_data={"createdManually": True},
                locale={"country": data["persona"].get("currentCountry") or "KR", "language": "ko"},
                folder_id=data.get("folderId") or data.get("folder_id"),
            )
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        with self.session_factory() as db_session:
            if data.get("folder_id") and not self.repository.get_folder(db_session, company_id=company_id, folder_id=data["folder_id"]):
                return self._error("invalid", "folder not found", 400)
            data = self._persist_persona_image_if_needed(db_session, company_id=company_id, user_id=user_id, persona_data=data)
            persona = self.repository.create_persona(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.persona_payload(persona)}, 201)

    def generate_personas(self, *, company_id: int, user_id: int, data: dict):
        started_at = time.monotonic()
        validated, errors = validate_generation_payload(data)
        if errors or validated is None:
            return PersonaServiceResult(
                status="invalid",
                error="Invalid persona generation request",
                data={"details": errors},
                status_code=400,
            )
        payload_existing_personas = validated.get("existingPersonas") or []
        if validated.get("skipExistingPersonas"):
            existing_personas = self._merge_persona_summaries(payload_existing_personas)
        else:
            with self.session_factory() as db_session:
                db_existing_personas = self.repository.list_existing_persona_summaries(
                    db_session,
                    company_id=company_id,
                )
            existing_personas = self._merge_persona_summaries(db_existing_personas, payload_existing_personas)
        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key="persona_generation")

        def generate():
            return generate_personas_pipeline(
                validated,
                existing_personas=existing_personas,
                text_generator=self._generate_text,
            )

        try:
            generated = run_with_llm_usage_context(usage_context, generate)
        except FileNotFoundError as exc:
            return self._error("seed_missing", str(exc), 500)
        except PersonaGenerationQualityError as exc:
            return self._error("generation_incomplete", str(exc), 502)
        except ValueError as exc:
            return self._error("seed_invalid", str(exc), 500)
        except RuntimeError as exc:
            return self._error("generation_failed", str(exc), 502)
        personas = []
        for persona in generated["personas"]:
            clean = dict(persona)
            clean.pop("_sourceSeed", None)
            if not validated.get("includeImages", True):
                clean["imageUrl"] = None
            else:
                try:
                    clean["imageUrl"] = self._generate_preview_image(clean)
                except Exception:
                    clean["imageUrl"] = None
            personas.append(clean)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        generation_metadata = dict(generated["generation_metadata"])
        timings = dict(generation_metadata.get("timingsMs") or {})
        timings["total"] = duration_ms
        generation_metadata["timingsMs"] = timings
        return self._ok(
            {
                "sourceType": infer_persona_source_type(validated),
                "generationMode": generated["generation_mode"],
                "durationMs": duration_ms,
                "personas": personas,
                "segments": generated["segments"],
                "telecomServiceUsageContextReferences": generated.get("telecom_service_usage_context_references") or [],
                "generationMetadata": generation_metadata,
                "tokenUsage": generated["token_usage"],
            }
        )

    def suggest_segments(self, *, company_id: int, user_id: int, data: dict):
        validated, errors = validate_segment_suggestion_payload(data)
        if errors or validated is None:
            return PersonaServiceResult(
                status="invalid",
                error="Invalid segment suggestion payload",
                data={"details": errors},
                status_code=400,
            )

        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key="persona_segment_suggestion")

        def generate():
            return generate_segment_suggestions_pipeline(
                validated,
                text_generator=self._generate_text,
            )

        try:
            segments, usage = run_with_llm_usage_context(usage_context, generate)
        except PersonaGenerationQualityError as exc:
            return self._error("generation_incomplete", str(exc), 502)
        except RuntimeError as exc:
            return self._error("generation_failed", str(exc), 502)
        return self._ok({"segments": segments, "tokenUsage": usage})

    def save_generated_personas(self, *, company_id: int, user_id: int, data: dict):
        personas_input = data.get("personas")
        if not isinstance(personas_input, list) or not personas_input:
            return self._error("invalid", "Invalid generated persona save payload", 400)
        source_type = data.get("sourceType") or data.get("source_type") or "service_based"
        source_data = data.get("sourceData") if "sourceData" in data else data.get("source_data")
        locale = data.get("locale")
        folder_id = data.get("folderId") or data.get("folder_id")
        created = []
        with self.session_factory() as db_session:
            if folder_id and not self.repository.get_folder(db_session, company_id=company_id, folder_id=folder_id):
                return self._error("invalid", "folder not found", 400)
            for item in personas_input:
                persona_data = self._persona_create_data_from_generated(
                    item,
                    source_type=source_type,
                    source_data=source_data,
                    locale=locale,
                    folder_id=folder_id,
                )
                if not persona_data.get("name"):
                    return self._error("invalid", "persona.name is required", 400)
                persona_data = self._persist_persona_image_if_needed(db_session, company_id=company_id, user_id=user_id, persona_data=persona_data)
                persona = self.repository.create_persona(db_session, company_id=company_id, user_id=user_id, data=persona_data)
                created.append(self.persona_payload(persona))
        return self._ok({"personas": created}, 201)

    def get_persona(self, *, company_id: int, persona_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            settings = self.repository.get_memory_settings(db_session, company_id=company_id, persona_id=persona_id)
            activities = self.repository.list_activities(db_session, company_id=company_id, persona_id=persona_id)
            traits = self.repository.list_traits(db_session, company_id=company_id, persona_id=persona_id)
            return self._ok(
                {
                    "persona": self.persona_payload(persona),
                    "memorySettings": self.memory_settings_payload(settings),
                    "activityStats": self.activity_stats_payload(activities),
                    "recentActivities": [self.activity_record_payload(row) for row in activities[:10]],
                    "recentTraits": [self.trait_record_payload(row) for row in traits[:10]],
                }
            )

    def update_persona(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            if data.get("folder_id") and not self.repository.get_folder(db_session, company_id=company_id, folder_id=data["folder_id"]):
                return self._error("invalid", "folder not found", 400)
            updated = self.repository.update_persona(db_session, persona, user_id=user_id, data=data)
            return self._ok({"data": self.persona_payload(updated)})

    def delete_persona(self, *, company_id: int, user_id: int, persona_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_persona(db_session, persona, user_id=user_id)
            return self._ok()

    def get_memory(self, *, company_id: int, persona_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            settings = self.repository.get_memory_settings(db_session, company_id=company_id, persona_id=persona_id)
            activities = self.repository.list_activities(db_session, company_id=company_id, persona_id=persona_id)
            traits = self.repository.list_traits(db_session, company_id=company_id, persona_id=persona_id)
            return self._ok({"data": self.memory_payload(settings, activities, traits)})

    def update_memory_settings(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            settings = self.repository.upsert_memory_settings(db_session, company_id=company_id, persona_id=persona_id, data=data)
            return self._ok({"data": self.memory_payload(settings, [], [])["settings"]})

    def add_activity(self, *, company_id: int, persona_id: int, data: dict):
        if not data.get("activity_type"):
            return self._error("invalid", "activity_type is required", 400)
        with self.session_factory() as db_session:
            if not self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id):
                return self._error("not_found", "persona not found", 404)
            activity = self.repository.create_activity(db_session, company_id=company_id, persona_id=persona_id, data=data)
            return self._ok({"data": {"id": activity.id}}, 201)

    def add_trait(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        if not data.get("trait"):
            return self._error("invalid", "trait is required", 400)
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            trait = self.repository.create_trait(db_session, company_id=company_id, persona_id=persona_id, data=data)
            return self._ok({"data": {"id": trait.id}}, 201)

    def delete_trait(self, *, company_id: int, user_id: int, persona_id: int, trait_id: int):
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            trait = self.repository.get_trait(db_session, company_id=company_id, persona_id=persona_id, trait_id=trait_id)
            if not persona or not trait:
                return self._error("not_found", "trait not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.deactivate_trait(db_session, trait)
            return self._ok()

    def save_upload(self, *, company_id: int, user_id: int, file, asset_type: str = "upload"):
        if not file:
            return self._error("invalid", "file is required", 400)
        storage_data = self.storage.save_upload(file, company_id=company_id, asset_type=asset_type)
        with self.session_factory() as db_session:
            asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
            return self._ok({"data": self.asset_payload(asset)}, 201)

    def get_asset(self, *, company_id: int, asset_id: int):
        with self.session_factory() as db_session:
            asset = self.repository.get_asset(db_session, company_id=company_id, asset_id=asset_id)
            if not asset:
                return self._error("not_found", "asset not found", 404)
            return self._ok({"asset": asset, "path": self.storage.resolve_local_path(asset.storage_key)})

    def attach_persona_image(self, *, company_id: int, user_id: int, persona_id: int, data: dict):
        if not data.get("asset_id") and not data.get("image_url"):
            if data.get("image_prompt"):
                return self._error("image_generation_not_configured", "persona image generation adapter is not configured", 503)
            return self._error("invalid", "asset_id or image_url is required", 400)
        with self.session_factory() as db_session:
            persona = self.repository.get_persona(db_session, company_id=company_id, persona_id=persona_id)
            if not persona:
                return self._error("not_found", "persona not found", 404)
            if not self._can_modify(db_session, persona, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            asset_id = data.get("asset_id")
            if asset_id and not self.repository.get_asset(db_session, company_id=company_id, asset_id=asset_id):
                return self._error("invalid", "asset not found", 400)
            image_url = f"/api/persona/storage/{asset_id}" if asset_id else data.get("image_url")
            updated = self.repository.attach_persona_image(
                db_session,
                persona,
                user_id=user_id,
                asset_id=asset_id,
                image_url=image_url,
                image_prompt=data.get("image_prompt"),
            )
            return self._ok({"data": self.persona_payload(updated)})

    def create_ui_test(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        if not data.get("source_type"):
            return self._error("invalid", "source_type is required", 400)
        with self.session_factory() as db_session:
            test = self.repository.create_ui_test(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.ui_test_payload(test)}, 201)

    def list_ui_tests(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.ui_test_payload(row) for row in self.repository.list_ui_tests(db_session, company_id=company_id)]})

    def get_ui_test(self, *, company_id: int, test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            rows = self.repository.list_ui_test_results(db_session, company_id=company_id, test_id=test_id)
            payload = self.ui_test_payload(test)
            payload["results"] = [self.ui_result_payload(row) for row in rows]
            return self._ok({"data": payload})

    def update_ui_test(self, *, company_id: int, user_id: int, test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            updated = self.repository.update_ui_test(db_session, test, user_id=user_id, data=data)
            return self._ok({"data": self.ui_test_payload(updated)})

    def delete_ui_test(self, *, company_id: int, user_id: int, test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_ui_test(db_session, test, user_id=user_id)
            return self._ok()

    def run_ui_test(self, *, company_id: int, user_id: int, test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id)
            if not test:
                return self._error("not_found", "test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "running", "progress": 10, "started_at": datetime.now(timezone.utc), "error_message": None})
            try:
                source_data = self._resolve_ui_source_data_for_run(
                    company_id=company_id,
                    user_id=user_id,
                    source_data=_clean_mapping(test.source_data) or {},
                )
            except PersonaUrlCaptureError as exc:
                self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": str(exc)})
                return self._error("capture_failed", str(exc), 502)
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=data.get("persona_ids") or data.get("personaIds"), source_data=source_data)
            if not personas:
                self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": "No personas available for UI test"})
                return self._error("invalid", "No personas available for UI test", 400)
            screens = self._screen_manifest(source_data)
            media_parts = self._read_screen_media_parts(db_session, company_id=company_id, screens=screens)
            try:
                self.repository.delete_ui_test_results(db_session, company_id=company_id, test_id=test.id)
                results = []
                for persona in personas:
                    result_data = self._run_ui_persona_evaluation(company_id=company_id, user_id=user_id, test=test, persona=persona, screens=screens, media_parts=media_parts)
                    result = self.repository.create_ui_test_result(db_session, company_id=company_id, test_id=test.id, persona_id=persona.id, data=result_data)
                    self.repository.create_activity(
                        db_session,
                        company_id=company_id,
                        persona_id=persona.id,
                        data={
                            "activity_type": "ui_test",
                            "activity_id": str(test.id),
                            "summary": result_data["summary"],
                            "metadata": {"testName": test.name, "scores": result_data.get("scores")},
                        },
                    )
                    results.append(self.ui_result_payload(result))
                summary = {
                    "averageScores": {
                        "clarity": round(sum((row["scores"] or {}).get("clarity", 0) for row in results) / len(results)),
                        "usability": round(sum((row["scores"] or {}).get("usability", 0) for row in results) / len(results)),
                        "appeal": round(sum((row["scores"] or {}).get("appeal", 0) for row in results) / len(results)),
                    },
                    "totalResponses": len(results),
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                }
                self.repository.update_ui_test(
                    db_session,
                    test,
                    user_id=user_id,
                    data={"status": "completed", "progress": 100, "completed_at": datetime.now(timezone.utc), "summary": summary, "persona_count": len(personas), "screen_count": len(screens), "source_data": source_data},
                )
            except Exception as exc:
                self.repository.update_ui_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": str(exc)})
                return self._error("failed", str(exc), 500)
            return self._ok({"data": self.ui_test_payload(test), "results": results})

    def list_ui_results(self, *, company_id: int, test_id: int):
        with self.session_factory() as db_session:
            if not self.repository.get_ui_test(db_session, company_id=company_id, test_id=test_id):
                return self._error("not_found", "test not found", 404)
            rows = self.repository.list_ui_test_results(db_session, company_id=company_id, test_id=test_id)
            return self._ok({"data": [self.ui_result_payload(row) for row in rows]})

    def capture_url(self, *, company_id: int, user_id: int, url: str):
        try:
            captured = self.capture.capture_url(url)
            screenshot_base64 = captured.pop("screenshot_base64", None)
            if not screenshot_base64:
                raise PersonaUrlCaptureError("URL capture did not produce a screenshot image")
            image_bytes = base64.b64decode(screenshot_base64)
            storage_data = self.storage.save_bytes(
                image_bytes,
                company_id=company_id,
                filename="captured-url.png",
                mime_type="image/png",
                asset_type="ui_test_capture",
            )
            with self.session_factory() as db_session:
                asset = self.repository.create_asset(db_session, company_id=company_id, user_id=user_id, data=storage_data)
                captured["capturedImageUrl"] = f"/api/persona/storage/{asset.id}"
                captured["asset_id"] = asset.id
            return self._ok({"data": captured})
        except ValueError as exc:
            return self._error("invalid", str(exc), 400)
        except PersonaUrlCaptureError as exc:
            return self._error("capture_failed", str(exc), 502)
        except Exception as exc:
            return self._error("capture_failed", str(exc), 502)

    def create_ab_test(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        with self.session_factory() as db_session:
            test = self.repository.create_ab_test(db_session, company_id=company_id, user_id=user_id, data=data)
            return self._ok({"data": self.ab_test_payload(test)}, 201)

    def list_ab_tests(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.ab_test_payload(row) for row in self.repository.list_ab_tests(db_session, company_id=company_id)]})

    def get_ab_test(self, *, company_id: int, ab_test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            rows = self.repository.list_ab_test_results(db_session, company_id=company_id, ab_test_id=ab_test_id)
            payload = self.ab_test_payload(test)
            payload["results"] = [self.ab_result_payload(row) for row in rows]
            return self._ok({"data": payload})

    def update_ab_test(self, *, company_id: int, user_id: int, ab_test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            updated = self.repository.update_ab_test(db_session, test, user_id=user_id, data=data)
            return self._ok({"data": self.ab_test_payload(updated)})

    def delete_ab_test(self, *, company_id: int, user_id: int, ab_test_id: int):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_ab_test(db_session, test, user_id=user_id)
            return self._ok()

    def run_ab_test(self, *, company_id: int, user_id: int, ab_test_id: int, data: dict):
        with self.session_factory() as db_session:
            test = self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id)
            if not test:
                return self._error("not_found", "ab test not found", 404)
            if not self._can_modify(db_session, test, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "running", "progress": 10, "error_message": None})
            source_data = _clean_mapping(test.context_data) or {}
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=data.get("persona_ids") or data.get("personaIds"), source_data=source_data)
            if not personas:
                self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "error_message": "No personas available for A/B test"})
                return self._error("invalid", "No personas available for A/B test", 400)
            try:
                self.repository.delete_ab_test_results(db_session, company_id=company_id, ab_test_id=test.id)
                results = []
                result_inputs = []
                for persona in personas:
                    result_data = self._run_ab_persona_evaluation(company_id=company_id, user_id=user_id, test=test, persona=persona)
                    result = self.repository.create_ab_test_result(db_session, company_id=company_id, ab_test_id=test.id, persona_id=persona.id, data=result_data)
                    result_inputs.append(result_data)
                    results.append(self.ab_result_payload(result))
                summary = self._ab_summary(result_inputs, test.mode)
                self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "completed", "progress": 100, "summary": summary})
            except Exception as exc:
                self.repository.update_ab_test(db_session, test, user_id=user_id, data={"status": "failed", "progress": 100, "error_message": str(exc)})
                return self._error("failed", str(exc), 500)
            return self._ok({"data": self.ab_test_payload(test), "results": results})

    def list_ab_results(self, *, company_id: int, ab_test_id: int):
        with self.session_factory() as db_session:
            if not self.repository.get_ab_test(db_session, company_id=company_id, ab_test_id=ab_test_id):
                return self._error("not_found", "ab test not found", 404)
            rows = self.repository.list_ab_test_results(db_session, company_id=company_id, ab_test_id=ab_test_id)
            return self._ok({"data": [self.ab_result_payload(row) for row in rows]})

    def list_combined_tests(self, *, company_id: int):
        with self.session_factory() as db_session:
            ui_items = [
                {
                    "id": row.id,
                    "kind": "ui-test",
                    "name": row.name,
                    "status": row.status,
                    "created_at": _dt(row.created_at),
                    "createdAt": _dt(row.created_at),
                    "personaCount": row.persona_count,
                    "href": f"/tests/{row.id}",
                    "typeLabel": "UX테스트 > 단일검증",
                }
                for row in self.repository.list_ui_tests(db_session, company_id=company_id)
            ]
            ab_items = [
                {
                    "id": row.id,
                    "kind": "ab-test",
                    "name": row.name,
                    "status": row.status,
                    "created_at": _dt(row.created_at),
                    "createdAt": _dt(row.created_at),
                    "personaCount": len(((row.context_data or {}).get("personaSelection") or {}).get("selectedPersonaIds") or []),
                    "href": f"/tests/{row.id}",
                    "typeLabel": "UX테스트 > A/B테스트",
                }
                for row in self.repository.list_ab_tests(db_session, company_id=company_id)
            ]
            interview_items = [
                {
                    "id": row.id,
                    "kind": "interview",
                    "name": row.name,
                    "status": row.status,
                    "created_at": _dt(row.created_at),
                    "createdAt": _dt(row.created_at),
                    "personaCount": len(row.persona_ids or []),
                    "href": f"/interviews/{row.id}",
                    "typeLabel": "UX테스트 > 1:1 AI 인터뷰",
                }
                for row in self.repository.list_interviews(db_session, company_id=company_id)
            ]
            items = sorted(ui_items + ab_items + interview_items, key=lambda item: item["created_at"] or "", reverse=True)
            return self._ok({"data": items})

    def generate_interview_questions(self, *, company_id: int, user_id: int, data: dict):
        goal = str(data.get("goal") or "").strip()
        if not goal:
            return self._error("invalid", "goal is required", 400)
        length = data.get("length") or "quick"
        usage_context = build_llm_usage_context(company_id=company_id, user_id=user_id, feature_key="persona_interview_question_generation")

        def generate():
            return self._interview_question_set(goal=goal, product_description=data.get("productDescription") or data.get("product_description"), length=length)

        question_set = run_with_llm_usage_context(usage_context, generate)
        return self._ok({"data": question_set})

    def list_interview_personas(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.persona_payload(row) for row in self.repository.list_all_personas(db_session, company_id=company_id)]})

    def create_interview(self, *, company_id: int, user_id: int, data: dict):
        if not self._require_name(data):
            return self._error("invalid", "name is required", 400)
        goal = str(data.get("goal") or "").strip()
        if not goal:
            return self._error("invalid", "goal is required", 400)
        question_set = data.get("question_set") or data.get("questionSet") or self._interview_question_set(
            goal=goal,
            product_description=data.get("productDescription") or data.get("product_description"),
            length=data.get("length") or "quick",
        )
        payload = {
            **data,
            "goal": goal,
            "product_description": data.get("productDescription") or data.get("product_description"),
            "question_set": question_set,
            "persona_ids": data.get("persona_ids") or data.get("personaIds") or [],
        }
        with self.session_factory() as db_session:
            interview = self.repository.create_interview(db_session, company_id=company_id, user_id=user_id, data=payload)
            return self._ok({"data": self.interview_payload(interview)}, 201)

    def list_interviews(self, *, company_id: int):
        with self.session_factory() as db_session:
            return self._ok({"data": [self.interview_payload(row) for row in self.repository.list_interviews(db_session, company_id=company_id)]})

    def get_interview(self, *, company_id: int, interview_id: int):
        with self.session_factory() as db_session:
            interview = self.repository.get_interview(db_session, company_id=company_id, interview_id=interview_id)
            if not interview:
                return self._error("not_found", "interview not found", 404)
            results = self.repository.list_interview_results(db_session, company_id=company_id, interview_id=interview_id)
            result_payloads = [self.interview_result_payload(row) for row in results]
            payload = self.interview_payload(interview)
            payload["results"] = result_payloads
            return self._ok({"data": payload, "results": result_payloads})

    def delete_interview(self, *, company_id: int, user_id: int, interview_id: int):
        with self.session_factory() as db_session:
            interview = self.repository.get_interview(db_session, company_id=company_id, interview_id=interview_id)
            if not interview:
                return self._error("not_found", "interview not found", 404)
            if not self._can_modify(db_session, interview, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            self.repository.soft_delete_interview(db_session, interview, user_id=user_id)
            return self._ok()

    def run_interview(self, *, company_id: int, user_id: int, interview_id: int, data: dict):
        with self.session_factory() as db_session:
            interview = self.repository.get_interview(db_session, company_id=company_id, interview_id=interview_id)
            if not interview:
                return self._error("not_found", "interview not found", 404)
            if not self._can_modify(db_session, interview, company_id=company_id, user_id=user_id):
                return self._error("forbidden", "insufficient permissions", 403)
            persona_ids = data.get("persona_ids") or data.get("personaIds") or interview.persona_ids or []
            personas = self._resolve_run_personas(db_session, company_id=company_id, explicit_ids=persona_ids)
            if not personas:
                self.repository.update_interview(db_session, interview, user_id=user_id, data={"status": "failed", "progress": 100, "error_message": "No personas available for interview"})
                return self._error("invalid", "No personas available for interview", 400)
            self.repository.update_interview(db_session, interview, user_id=user_id, data={"status": "running", "progress": 10, "started_at": datetime.now(timezone.utc), "error_message": None})
            try:
                self.repository.delete_interview_results(db_session, company_id=company_id, interview_id=interview.id)
                results = []
                for persona in personas:
                    result_data = self._run_interview_for_persona(company_id=company_id, user_id=user_id, interview=interview, persona=persona)
                    result = self.repository.create_interview_result(db_session, company_id=company_id, interview_id=interview.id, persona_id=persona.id, data=result_data)
                    results.append(self.interview_result_payload(result))
                summary = {"totalResponses": len(results), "completedAt": datetime.now(timezone.utc).isoformat()}
                self.repository.update_interview(
                    db_session,
                    interview,
                    user_id=user_id,
                    data={"status": "completed", "progress": 100, "completed_at": datetime.now(timezone.utc), "summary": summary, "persona_ids": [row.id for row in personas]},
                )
            except Exception as exc:
                self.repository.update_interview(db_session, interview, user_id=user_id, data={"status": "failed", "progress": 100, "completed_at": datetime.now(timezone.utc), "error_message": str(exc)})
                return self._error("failed", str(exc), 500)
            return self._ok({"data": self.interview_payload(interview), "results": results})

    def figma_status(self, *, company_id: int, user_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            return self._ok(self.figma_account_payload(account))

    def figma_connect_url(self, *, company_id: int, user_id: int, redirect_uri: str):
        try:
            url = self.figma_client.authorization_url(state=make_oauth_state(company_id=company_id, user_id=user_id), redirect_uri=redirect_uri)
            return self._ok({"url": url})
        except Exception as exc:
            return self._error("figma_not_configured", str(exc), 503)

    def figma_callback(self, *, company_id: int, user_id: int, code: str, redirect_uri: str):
        try:
            payload = self.figma_client.exchange_code(code=code, redirect_uri=redirect_uri)
            if not payload.get("figma_user_id"):
                return self._error("figma_error", "Figma user id is missing", 502)
            data = {
                "figma_user_id": payload["figma_user_id"],
                "figma_email": payload.get("figma_email"),
                "figma_handle": payload.get("figma_handle"),
                "figma_avatar_url": payload.get("figma_avatar_url"),
                "access_token_encrypted": self.figma_client.encrypt(payload.get("access_token")),
                "refresh_token_encrypted": self.figma_client.encrypt(payload.get("refresh_token")),
                "scope": payload.get("scope"),
                "expires_at": payload.get("expires_at"),
            }
            with self.session_factory() as db_session:
                account = self.repository.upsert_figma_account(db_session, company_id=company_id, user_id=user_id, data=data)
                return self._ok(self.figma_account_payload(account))
        except Exception as exc:
            return self._error("figma_error", str(exc), 502)

    def figma_disconnect(self, *, company_id: int, user_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            if account:
                self.repository.disconnect_figma_account(db_session, account)
            return self._ok()

    def list_figma_files(self, *, company_id: int, user_id: int):
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            rows = self.repository.list_figma_files(db_session, company_id=company_id, account_id=account.id if account else None)
            return self._ok({"data": [self.figma_file_payload(row) for row in rows]})

    def sync_figma_file(self, *, company_id: int, user_id: int, data: dict):
        if not data.get("figma_file_key"):
            return self._error("invalid", "figma_file_key is required", 400)
        with self.session_factory() as db_session:
            account = self.repository.get_figma_account(db_session, company_id=company_id, user_id=user_id)
            if not account:
                return self._error("not_connected", "Figma account is not connected", 409)
            figma_file = self.repository.upsert_figma_file(
                db_session,
                company_id=company_id,
                account_id=account.id,
                data={
                    "figma_account_id": account.id,
                    "figma_file_key": data["figma_file_key"],
                    "figma_file_name": data.get("figma_file_name") or data["figma_file_key"],
                    "figma_file_link": data.get("figma_file_link"),
                    "thumbnail_url": data.get("thumbnail_url"),
                    "last_synced_at": datetime.now(timezone.utc),
                    "sync_status": "completed",
                    "sync_error": None,
                },
            )
            return self._ok({"data": self.figma_file_payload(figma_file)}, 201)

    def list_figma_flows(self, *, company_id: int, file_id: int):
        with self.session_factory() as db_session:
            if not self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id):
                return self._error("not_found", "figma file not found", 404)
            rows = self.repository.list_figma_flows(db_session, company_id=company_id, file_id=file_id)
            return self._ok({"data": [self.figma_flow_payload(row) for row in rows]})

    def sync_figma_flows(self, *, company_id: int, file_id: int, data: dict):
        flows = data.get("flows")
        if not isinstance(flows, list):
            return self._error("invalid", "flows must be a list", 400)
        with self.session_factory() as db_session:
            if not self.repository.get_figma_file(db_session, company_id=company_id, file_id=file_id):
                return self._error("not_found", "figma file not found", 404)
            created = self.repository.replace_figma_flows(db_session, company_id=company_id, file_id=file_id, flows=flows)
            return self._ok({"data": [self.figma_flow_payload(row) for row in created]}, 201)


persona_service = PersonaService()
