from reopsai.infrastructure.persona_image_generation import resolve_google_api_key


def test_resolve_google_api_key_supports_comma_separated_keys(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEYS", " first-key , second-key ")

    assert resolve_google_api_key() == "first-key"


def test_resolve_google_api_key_prefers_explicit_value(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEYS", "first-key,second-key")

    assert resolve_google_api_key("explicit-key") == "explicit-key"
