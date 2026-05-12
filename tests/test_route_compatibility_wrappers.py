from importlib import import_module


def test_legacy_route_wrappers_export_current_blueprints():
    import reopsai_backend.api.admin as admin
    import reopsai_backend.api.ai_persona as ai_persona
    import reopsai_backend.api.artifact_ai as artifact_ai
    import reopsai_backend.api.auth as auth
    import reopsai_backend.api.b2b as b2b
    import reopsai_backend.api.demo as demo
    import reopsai_backend.api.dev_evaluator as dev_evaluator
    import reopsai_backend.api.generator as generator
    import reopsai_backend.api.guideline as guideline
    import reopsai_backend.api.plan as plan
    import reopsai_backend.api.screener as screener
    import reopsai_backend.api.study as study
    import reopsai_backend.api.survey as survey
    import reopsai_backend.api.workspace as workspace
    legacy_admin = import_module("routes" + ".admin")
    legacy_ai_persona = import_module("routes" + ".ai_persona")
    legacy_artifact_ai = import_module("routes" + ".artifact_ai")
    legacy_auth = import_module("routes" + ".auth")
    legacy_b2b = import_module("routes" + ".b2b")
    legacy_demo = import_module("routes" + ".demo")
    legacy_dev_evaluator = import_module("routes" + ".dev_evaluator")
    legacy_generator = import_module("routes" + ".generator")
    legacy_guideline = import_module("routes" + ".guideline_routes")
    legacy_plan = import_module("routes" + ".plan_routes")
    legacy_screener = import_module("routes" + ".screener")
    legacy_study = import_module("routes" + ".study")
    legacy_survey = import_module("routes" + ".survey_routes")
    legacy_workspace = import_module("routes" + ".workspace")

    assert legacy_admin.admin_bp is admin.admin_bp
    assert legacy_ai_persona.ai_persona_bp is ai_persona.ai_persona_bp
    assert legacy_artifact_ai.artifact_ai_bp is artifact_ai.artifact_ai_bp
    assert legacy_auth.auth_bp is auth.auth_bp
    assert legacy_b2b.b2b_bp is b2b.b2b_bp
    assert legacy_demo.demo_bp is demo.demo_bp
    assert legacy_dev_evaluator.dev_evaluator_bp is dev_evaluator.dev_evaluator_bp
    assert legacy_generator.generator_bp is generator.generator_bp
    assert legacy_guideline.guideline_bp is guideline.guideline_bp
    assert legacy_plan.plan_bp is plan.plan_bp
    assert legacy_screener.screener_bp is screener.screener_bp
    assert legacy_study.study_bp is study.study_bp
    assert legacy_survey.survey_bp is survey.survey_bp
    assert legacy_workspace.workspace_bp is workspace.workspace_bp
