from urllib.parse import parse_qs, urlparse
import sys
from types import SimpleNamespace

from reopsai.infrastructure.persona_figma_client import FIGMA_OAUTH_SCOPE, PersonaFigmaClient, build_flow_preview, extract_prototype_flows


class _Config:
    PERSONA_FIGMA_CLIENT_ID = "figma-client-id"
    PERSONA_FIGMA_CLIENT_SECRET = "figma-client-secret"


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_authorization_url_uses_granular_figma_scopes():
    url = PersonaFigmaClient(config=_Config).authorization_url(
        state="company:user:nonce",
        redirect_uri="https://api.example.com/api/persona/figma/callback",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert query["scope"] == [FIGMA_OAUTH_SCOPE]
    assert "files%3Aread" not in url
    assert "file_content%3Aread" in url
    assert "current_user%3Aread" in url
    assert query["redirect_uri"] == ["https://api.example.com/api/persona/figma/callback"]


def test_exchange_code_uses_basic_auth_for_token_request(monkeypatch):
    calls = {}

    def fake_post(url, *, data, auth, timeout):
        calls["post"] = {"url": url, "data": data, "auth": auth, "timeout": timeout}
        return _Response({"access_token": "access-token", "refresh_token": "refresh-token", "expires_in": 3600})

    def fake_get(url, *, headers, timeout):
        calls["get"] = {"url": url, "headers": headers, "timeout": timeout}
        return _Response({"id": "figma-user-id", "email": "figma@example.com", "handle": "figma-user", "img_url": "https://example.com/avatar.png"})

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=fake_post, get=fake_get))

    payload = PersonaFigmaClient(config=_Config).exchange_code(
        code="oauth-code",
        redirect_uri="https://api.example.com/api/persona/figma/callback",
    )

    assert calls["post"]["auth"] == ("figma-client-id", "figma-client-secret")
    assert "client_id" not in calls["post"]["data"]
    assert "client_secret" not in calls["post"]["data"]
    assert calls["post"]["data"]["redirect_uri"] == "https://api.example.com/api/persona/figma/callback"
    assert calls["get"]["headers"] == {"Authorization": "Bearer access-token"}
    assert payload["figma_user_id"] == "figma-user-id"


def test_extract_prototype_flows_uses_flow_starting_points():
    flows = extract_prototype_flows(
        {
            "document": {
                "children": [
                    {
                        "id": "1:1",
                        "name": "Home",
                        "flowStartingPoints": [{"nodeId": "2:1", "name": "Signup"}],
                        "children": [{"id": "2:1", "name": "Start frame"}],
                    }
                ]
            }
        }
    )

    assert flows == [
        {
            "figma_page_id": "1:1",
            "figma_page_name": "Home",
            "figma_start_node_id": "2:1",
            "figma_flow_name": "Signup",
            "metadata": {"source": "figma_api"},
        }
    ]


def test_extract_prototype_flows_falls_back_to_prototype_start_node_id():
    flows = extract_prototype_flows(
        {
            "document": {
                "children": [
                    {
                        "id": "1:1",
                        "name": "Checkout",
                        "prototypeStartNodeID": "2:3",
                        "children": [{"id": "2:3", "name": "Payment screen"}],
                    }
                ]
            }
        }
    )

    assert flows[0]["figma_start_node_id"] == "2:3"
    assert flows[0]["figma_flow_name"] == "Payment screen"


def test_build_flow_preview_follows_prototype_node_transitions():
    preview = build_flow_preview(
        {
            "document": {
                "id": "0:0",
                "type": "DOCUMENT",
                "children": [
                    {
                        "id": "1:1",
                        "type": "CANVAS",
                        "children": [
                            {
                                "id": "2:1",
                                "name": "Start",
                                "type": "FRAME",
                                "absoluteBoundingBox": {"x": 0, "y": 0, "width": 390, "height": 844},
                                "interactions": [
                                    {
                                        "trigger": {"type": "ON_CLICK"},
                                        "actions": [{"type": "NODE", "destinationId": "2:2", "navigation": "NAVIGATE"}],
                                    }
                                ],
                            },
                            {
                                "id": "2:2",
                                "name": "Done",
                                "type": "FRAME",
                                "absoluteBoundingBox": {"x": 420, "y": 0, "width": 390, "height": 844},
                            },
                        ],
                    }
                ],
            }
        },
        start_node_id="2:1",
    )

    assert [screen["figmaNodeId"] for screen in preview["screens"]] == ["2:1", "2:2"]
    assert preview["screens"][0]["viewport"] == "mobile"
    assert preview["transitions"][0]["toScreenId"] == "screen_2:2"
