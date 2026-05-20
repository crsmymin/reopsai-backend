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
FIGMA_OAUTH_SCOPE = "file_content:read,current_user:read"


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


persona_figma_client = PersonaFigmaClient()
