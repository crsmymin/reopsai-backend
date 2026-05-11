"""Blueprint registration for the public Flask API."""

from __future__ import annotations

import os


def register_blueprints(app) -> None:
    from reopsai_backend.api.ai_persona import ai_persona_bp
    from reopsai_backend.api.artifact_ai import artifact_ai_bp
    from reopsai_backend.api.auth import auth_bp
    from reopsai_backend.api.b2b import b2b_bp
    from reopsai_backend.api.demo import demo_bp
    from reopsai_backend.api.generator import generator_bp
    from reopsai_backend.api.screener import screener_bp
    from reopsai_backend.api.study import study_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(screener_bp)
    app.register_blueprint(study_bp)
    app.register_blueprint(generator_bp)
    app.register_blueprint(demo_bp)
    app.register_blueprint(artifact_ai_bp)
    app.register_blueprint(b2b_bp)
    app.register_blueprint(ai_persona_bp)

    if os.getenv("FLASK_ENV") == "development":
        from reopsai_backend.api.dev_evaluator import dev_evaluator_bp

        app.register_blueprint(dev_evaluator_bp)
        print("Dev Evaluator Blueprint registered for development")

    from reopsai_backend.api.admin import admin_bp

    app.register_blueprint(admin_bp)
    print("Admin Blueprint registered")

    from reopsai_backend.api.guideline import guideline_bp
    from reopsai_backend.api.plan import plan_bp
    from reopsai_backend.api.survey import survey_bp
    from reopsai_backend.api.workspace import workspace_bp

    app.register_blueprint(workspace_bp)
    app.register_blueprint(survey_bp)
    app.register_blueprint(guideline_bp)
    app.register_blueprint(plan_bp)
    print("Blueprints registered: workspace, survey, guideline, plan")
