"""Flask application factory."""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from flask_cors import CORS

from config import Config
from reopsai.api.blueprints import register_blueprints
from reopsai.shared.extensions import jwt
from reopsai.shared.security import (
    register_jwt_error_handlers,
    register_request_guards,
    register_security_headers,
)


BASE_DIR = Path(__file__).resolve().parents[2]


def _init_database(config_object) -> bool:
    try:
        from db.engine import init_engine as init_sqlalchemy_engine
    except Exception:
        print("SQLAlchemy package not ready; engine initialization skipped")
        return False

    try:
        if not getattr(config_object, "DATABASE_URL", None):
            print("DATABASE_URL not set; SQLAlchemy engine initialization skipped")
            return False
        init_sqlalchemy_engine(validate_connection=True)
        print("SQLAlchemy engine initialized")
        return True
    except Exception as exc:
        print(f"SQLAlchemy engine initialization failed: {exc}")
        return False


def _init_cors(app: Flask, config_object) -> None:
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": config_object.ALLOWED_ORIGINS,
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization", "Accept", "X-User-ID", "x-user-id"],
                "supports_credentials": True,
                "max_age": 86400,
            }
        },
        automatic_options=True,
        intercept_exceptions=False,
    )


def create_app(config_object=Config) -> Flask:
    app = Flask("app", root_path=str(BASE_DIR))
    app.config.from_object(config_object)

    jwt.init_app(app)
    register_jwt_error_handlers(jwt)
    _init_cors(app, config_object)
    register_request_guards(app)
    register_security_headers(app)
    app.config["SQLA_ENABLED"] = _init_database(config_object)

    @app.route("/health")
    def health():
        try:
            return {"status": "ok"}, 200
        except Exception:
            return {"status": "error"}, 500

    register_blueprints(app)
    return app
