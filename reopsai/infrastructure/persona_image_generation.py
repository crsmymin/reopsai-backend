from __future__ import annotations

import base64
import os


DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
GENERATED_IMAGE_MIME_TYPE = "image/png"


def build_image_prompt(persona: dict) -> str:
    if persona.get("imagePrompt"):
        return str(persona["imagePrompt"])
    hints = "\n".join(
        str(value)
        for value in [
            f"Name: {persona.get('name')}",
            f"Gender vibe: {persona.get('gender')}" if persona.get("gender") else None,
            f"Approximate age: {persona.get('age')}" if persona.get("age") else None,
            f"Profession: {persona.get('title')}" if persona.get("title") else None,
            f"Location: {persona.get('currentCountry')}" if persona.get("currentCountry") else None,
            f"Personality vibe: {persona.get('personality')}" if persona.get("personality") else None,
        ]
        if value
    )
    return f"""Create a photorealistic professional portrait for a user persona profile.

{hints}

Requirements:
- realistic headshot photo
- natural facial details
- non-cartoon, non-illustration
- clean modern background
- unique individual, not a stock-photo stereotype"""


def data_url_from_bytes(image_bytes: bytes, mime_type: str = GENERATED_IMAGE_MIME_TYPE) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def resolve_google_api_key(api_key: str | None = None) -> str | None:
    if api_key:
        return api_key
    single_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if single_key:
        return single_key
    keys = os.getenv("GOOGLE_API_KEYS") or os.getenv("GEMINI_API_KEYS")
    if not keys:
        return None
    return next((key.strip() for key in keys.split(",") if key.strip()), None)


def generate_persona_image_data_url(persona: dict, *, api_key: str | None = None, model: str | None = None, timeout: int = 45) -> str | None:
    import requests

    api_key = resolve_google_api_key(api_key)
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY, GEMINI_API_KEY, GOOGLE_API_KEYS, or GEMINI_API_KEYS is required for persona image generation")
    model = model or os.getenv("PERSONA_GEMINI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(
        url,
        params={"key": api_key},
        json={
            "contents": [{"role": "user", "parts": [{"text": build_image_prompt(persona)}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            mime_type = inline.get("mimeType") or inline.get("mime_type") or GENERATED_IMAGE_MIME_TYPE
            return f"data:{mime_type};base64,{inline['data']}"
    return None
