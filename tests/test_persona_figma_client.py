from urllib.parse import parse_qs, urlparse
import sys
from types import SimpleNamespace

from reopsai.infrastructure.persona_figma_client import FIGMA_OAUTH_SCOPE, PersonaFigmaClient


class _Config:
    PERSONA_FIGMA_CLIENT_ID = "figma-client-id"
    PERSONA_FIGMA_CLIENT_SECRET = "figma-client-secret"


class _Response:
    def __init__(self, payload):
        self.payload = payload

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
