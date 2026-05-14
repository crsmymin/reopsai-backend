import config


def test_parse_allowed_origins_includes_stage_frontend(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://reopsai.com")
    monkeypatch.delenv("FRONTEND_URL", raising=False)

    origins = config._parse_allowed_origins()

    assert "https://stage.reopsai.com" in origins
    assert "https://reopsai.com" in origins
