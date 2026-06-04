from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from config import Config


FIGMA_AUTH_URL = "https://www.figma.com/oauth"
FIGMA_TOKEN_URL = "https://api.figma.com/v1/oauth/token"
FIGMA_ME_URL = "https://api.figma.com/v1/me"
FIGMA_FILE_URL = "https://api.figma.com/v1/files"
FIGMA_IMAGES_URL = "https://api.figma.com/v1/images"
FIGMA_OAUTH_SCOPE = "file_content:read,current_user:read"


class PersonaFigmaClientError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _fernet_key(raw_key: str | None) -> bytes:
    if not raw_key:
        raise RuntimeError("PERSONA_FIGMA_ENCRYPTION_KEY is not configured")
    key = raw_key.strip()
    if len(key) == 44:
        return key.encode("utf-8")
    if len(key) == 64:
        digest = bytes.fromhex(key)
    else:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class PersonaFigmaClient:
    def __init__(self, *, config=Config):
        self.config = config

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        if not self.config.PERSONA_FIGMA_CLIENT_ID:
            raise RuntimeError("PERSONA_FIGMA_CLIENT_ID is not configured")
        query = urlencode(
            {
                "client_id": self.config.PERSONA_FIGMA_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "scope": FIGMA_OAUTH_SCOPE,
                "state": state,
                "response_type": "code",
            }
        )
        return f"{FIGMA_AUTH_URL}?{query}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict:
        if not self.config.PERSONA_FIGMA_CLIENT_ID or not self.config.PERSONA_FIGMA_CLIENT_SECRET:
            raise RuntimeError("Figma OAuth client credentials are not configured")
        import requests

        response = requests.post(
            FIGMA_TOKEN_URL,
            data={
                "redirect_uri": redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
            },
            auth=(self.config.PERSONA_FIGMA_CLIENT_ID, self.config.PERSONA_FIGMA_CLIENT_SECRET),
            timeout=15,
        )
        response.raise_for_status()
        token_payload = response.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("Figma token response did not include access_token")
        me_response = requests.get(FIGMA_ME_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        me_response.raise_for_status()
        me = me_response.json()
        expires_in = token_payload.get("expires_in")
        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return {
            "figma_user_id": str(me.get("id") or me.get("handle") or ""),
            "figma_email": me.get("email"),
            "figma_handle": me.get("handle"),
            "figma_avatar_url": me.get("img_url"),
            "access_token": access_token,
            "refresh_token": token_payload.get("refresh_token"),
            "scope": token_payload.get("scope"),
            "expires_at": expires_at,
        }

    def fetch_file_with_flows(self, *, file_key: str, access_token: str) -> dict:
        payload = self.fetch_file(file_key=file_key, access_token=access_token)
        flows = extract_prototype_flows(payload)
        return {
            "figma_file_key": file_key,
            "figma_file_name": payload.get("name") or file_key,
            "thumbnail_url": payload.get("thumbnailUrl"),
            "flows": flows,
        }

    def fetch_file(self, *, file_key: str, access_token: str) -> dict:
        import requests

        response = requests.get(
            f"{FIGMA_FILE_URL}/{file_key}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if response.status_code == 403:
            raise PersonaFigmaClientError("figma_permission", "해당 링크의 파일 권한을 확인해주세요", 403)
        if response.status_code == 404:
            raise PersonaFigmaClientError("figma_sharing", "해당 링크의 파일 공유 설정을 확인해주세요", 404)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise PersonaFigmaClientError("figma_error", str(exc), 502) from exc

        payload = response.json()
        role = str(payload.get("role") or "").strip().lower()
        if role and role != "owner":
            raise PersonaFigmaClientError("figma_permission", "해당 링크의 파일 권한을 확인해주세요", 403)
        return payload

    def fetch_node_images(self, *, file_key: str, node_ids: list[str], access_token: str, scale: int = 2, image_format: str = "png") -> dict:
        if not node_ids:
            return {}
        import requests

        response = requests.get(
            f"{FIGMA_IMAGES_URL}/{file_key}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "ids": ",".join(node_ids),
                "scale": scale,
                "format": image_format,
                "use_absolute_bounds": "true",
            },
            timeout=30,
        )
        if response.status_code == 403:
            raise PersonaFigmaClientError("figma_permission", "해당 링크의 파일 권한을 확인해주세요", 403)
        if response.status_code == 404:
            raise PersonaFigmaClientError("figma_sharing", "해당 링크의 파일 공유 설정을 확인해주세요", 404)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise PersonaFigmaClientError("figma_error", str(exc), 502) from exc
        payload = response.json()
        return payload.get("images") or {}

    def download_image(self, image_url: str) -> tuple[bytes, str]:
        import requests

        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        return response.content, response.headers.get("Content-Type") or "image/png"

    def fetch_flow_preview(self, *, file_key: str, start_node_id: str, access_token: str, max_screens: int = 50, max_depth: int = 20) -> dict:
        file_payload = self.fetch_file(file_key=file_key, access_token=access_token)
        preview = build_flow_preview(file_payload, start_node_id=start_node_id, max_screens=max_screens, max_depth=max_depth)
        renderable_ids = {
            screen["figmaNodeId"]: _find_renderable_node_id(preview["node_index"].get(screen["figmaNodeId"]) or {})
            for screen in preview["screens"]
        }
        image_map = self.fetch_node_images(
            file_key=file_key,
            node_ids=list({node_id for node_id in renderable_ids.values() if node_id}),
            access_token=access_token,
        )
        for screen in preview["screens"]:
            renderable_id = renderable_ids.get(screen["figmaNodeId"])
            screen["imageUrl"] = image_map.get(renderable_id) or ""
        preview.pop("node_index", None)
        return preview

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key(self.config.PERSONA_FIGMA_ENCRYPTION_KEY)).encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key(self.config.PERSONA_FIGMA_ENCRYPTION_KEY)).decrypt(value.encode("utf-8")).decode("utf-8")


def make_oauth_state(*, company_id: int, user_id: int) -> str:
    nonce = os.urandom(8).hex()
    return f"{int(company_id)}:{int(user_id)}:{nonce}"


def extract_prototype_flows(file_payload: dict) -> list[dict]:
    document = file_payload.get("document") or {}
    flows: list[dict] = []
    seen: set[str] = set()

    def find_node_name(node: dict | None, target_id: str) -> str | None:
        if not isinstance(node, dict):
            return None
        if node.get("id") == target_id:
            return node.get("name")
        for child in node.get("children") or []:
            found = find_node_name(child, target_id)
            if found:
                return found
        return None

    def add_flow(*, page: dict, node_id: str | None, name: str | None = None):
        if not node_id or node_id in seen:
            return
        seen.add(node_id)
        flows.append(
            {
                "figma_page_id": page.get("id"),
                "figma_page_name": page.get("name"),
                "figma_start_node_id": node_id,
                "figma_flow_name": name or find_node_name(page, node_id) or "Flow",
                "metadata": {"source": "figma_api"},
            }
        )

    for page in document.get("children") or []:
        if not isinstance(page, dict):
            continue
        for point in page.get("flowStartingPoints") or []:
            if isinstance(point, dict):
                add_flow(page=page, node_id=point.get("nodeId"), name=point.get("name"))
        add_flow(page=page, node_id=page.get("prototypeStartNodeID"))

    return flows


def infer_viewport(width=None, height=None) -> str:
    if not width or not height:
        return "desktop"
    if width < 600:
        return "mobile"
    if width < 1024:
        return "tablet"
    return "desktop"


def build_flow_preview(file_payload: dict, *, start_node_id: str, max_screens: int = 50, max_depth: int = 20) -> dict:
    node_index = _build_node_index(file_payload)
    visited: set[str] = set()
    queue = [{"node_id": start_node_id, "depth": 0}]
    screens = []
    transitions = []

    while queue and len(screens) < max_screens:
        current = queue.pop(0)
        node_id = current["node_id"]
        depth = current["depth"]
        if node_id in visited or depth > max_depth:
            continue
        visited.add(node_id)
        node = node_index.get(node_id)
        if not _is_screen_node(node):
            continue

        bounds = node.get("absoluteBoundingBox") or {}
        screen_id = f"screen_{node_id}"
        screens.append(
            {
                "id": screen_id,
                "name": node.get("name") or f"화면 {len(screens) + 1}",
                "figmaNodeId": node_id,
                "figma_node_id": node_id,
                "width": bounds.get("width") or 0,
                "height": bounds.get("height") or 0,
                "viewport": infer_viewport(bounds.get("width"), bounds.get("height")),
                "order": len(screens),
                "imageUrl": "",
            }
        )

        direct_interactions = [{"interaction": interaction, "source_node": node} for interaction in node.get("interactions") or []]
        child_interactions = _collect_child_interactions(node)
        for entry in [*direct_interactions, *child_interactions]:
            interaction = entry["interaction"]
            source_node = entry["source_node"]
            for action in interaction.get("actions") or []:
                transition = _process_action(
                    from_screen_id=screen_id,
                    from_node_id=node_id,
                    interaction=interaction,
                    action=action,
                    source_node=source_node,
                    frame_bounds=bounds,
                )
                if not transition:
                    continue
                transitions.append(transition)
                destination_id = action.get("destinationId")
                if destination_id and action.get("type") not in {"BACK", "CLOSE"} and destination_id not in visited:
                    queue.append({"node_id": destination_id, "depth": depth + 1})

    valid_screen_ids = {screen["id"] for screen in screens}
    valid_transitions = [
        transition
        for transition in transitions
        if transition["fromScreenId"] in valid_screen_ids and (transition["toScreenId"] is None or transition["toScreenId"] in valid_screen_ids)
    ]
    return {
        "startScreenId": screens[0]["id"] if screens else "",
        "screens": screens,
        "transitions": valid_transitions,
        "node_index": node_index,
    }


def _build_node_index(file_payload: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}

    def traverse(node):
        if not isinstance(node, dict) or not node.get("id"):
            return
        index[node["id"]] = node
        for child in node.get("children") or []:
            traverse(child)

    traverse(file_payload.get("document"))
    return index


def _is_screen_node(node) -> bool:
    return isinstance(node, dict) and node.get("type") in {"FRAME", "COMPONENT", "INSTANCE"}


def _find_renderable_node_id(node: dict) -> str | None:
    if not isinstance(node, dict):
        return None
    if not node.get("clipsContent"):
        return node.get("id")
    if node.get("overflowDirection") and node.get("children"):
        first_child = node["children"][0]
        if isinstance(first_child, dict) and first_child.get("type") in {"FRAME", "GROUP"}:
            return first_child.get("id")
    return node.get("id")


def _collect_child_interactions(frame_node: dict) -> list[dict]:
    results = []

    def traverse(node):
        if not isinstance(node, dict):
            return
        for interaction in node.get("interactions") or []:
            results.append({"interaction": interaction, "source_node": node})
        for child in node.get("children") or []:
            traverse(child)

    for child in frame_node.get("children") or []:
        traverse(child)
    return results


def _relative_bounds(control_bounds, frame_bounds):
    if not isinstance(control_bounds, dict) or not isinstance(frame_bounds, dict):
        return None
    return {
        "x": (control_bounds.get("x") or 0) - (frame_bounds.get("x") or 0),
        "y": (control_bounds.get("y") or 0) - (frame_bounds.get("y") or 0),
        "width": control_bounds.get("width") or 0,
        "height": control_bounds.get("height") or 0,
    }


def _extract_text_from_node(node):
    if not isinstance(node, dict):
        return None
    if node.get("characters"):
        return node.get("characters")
    for child in node.get("children") or []:
        if isinstance(child, dict) and child.get("characters"):
            return child.get("characters")
    return None


def _process_action(*, from_screen_id: str, from_node_id: str, interaction: dict, action: dict, source_node: dict, frame_bounds: dict):
    action_type = action.get("type") if isinstance(action, dict) else None
    trigger = interaction.get("trigger") if isinstance(interaction, dict) else None
    trigger_type = trigger.get("type") if isinstance(trigger, dict) else None
    if not action_type or not trigger_type or action_type in {"URL", "UPDATE_MEDIA_RUNTIME"}:
        return None
    control_bounds = _relative_bounds(source_node.get("absoluteBoundingBox"), frame_bounds)
    base = {
        "fromScreenId": from_screen_id,
        "triggerType": trigger_type,
        "controlNodeId": source_node.get("id"),
        "controlNodeName": source_node.get("name"),
        "controlBounds": control_bounds,
    }
    if action_type == "BACK":
        return {**base, "id": f"trans_{from_node_id}_back", "toScreenId": None, "navigationType": "BACK"}
    if action_type == "CLOSE":
        return {**base, "id": f"trans_{from_node_id}_close", "toScreenId": None, "navigationType": "CLOSE"}
    if action_type == "NODE" and action.get("destinationId"):
        destination_id = action["destinationId"]
        return {
            **base,
            "id": f"trans_{from_node_id}_{destination_id}",
            "toScreenId": f"screen_{destination_id}",
            "navigationType": action.get("navigation") or "NAVIGATE",
            "controlText": _extract_text_from_node(source_node),
        }
    return None


persona_figma_client = PersonaFigmaClient()
