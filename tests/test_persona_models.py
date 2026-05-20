from reopsai.infrastructure.persistence.base import Base
from reopsai.infrastructure.persistence import models  # noqa: F401
from reopsai.infrastructure.persistence.models.persona import PersonaAsset


def test_persona_models_register_in_metadata():
    expected = {
        "persona_folders",
        "personas",
        "persona_memory_settings",
        "persona_activities",
        "persona_learned_traits",
        "persona_assets",
        "persona_figma_accounts",
        "persona_figma_files",
        "persona_figma_flows",
        "persona_ui_tests",
        "persona_ui_test_results",
        "persona_ab_tests",
        "persona_ab_test_results",
        "persona_interviews",
        "persona_interview_results",
    }

    assert expected.issubset(Base.metadata.tables.keys())
    assert "company_id" in Base.metadata.tables["personas"].columns
    assert "biography" in Base.metadata.tables["personas"].columns
    assert "current_city" in Base.metadata.tables["personas"].columns
    assert "telecom_behavior_dimensions" in Base.metadata.tables["personas"].columns
    assert "image_data" in Base.metadata.tables["personas"].columns
    assert "updated_by_user_id" in Base.metadata.tables["persona_assets"].columns
    assert "interview_pack" in Base.metadata.tables["personas"].columns
    assert "persona_goal_fit" in Base.metadata.tables["persona_ui_test_results"].columns
    assert "pin_comments" in Base.metadata.tables["persona_ui_test_results"].columns
    assert "persona_snapshot" in Base.metadata.tables["persona_ui_test_results"].columns
    assert "persona_snapshot" in Base.metadata.tables["persona_ab_test_results"].columns
    assert "raw_response" in Base.metadata.tables["persona_ab_test_results"].columns
    assert "turns" in Base.metadata.tables["persona_interview_results"].columns


def test_persona_asset_accepts_updated_by_user_id():
    asset = PersonaAsset(
        company_id=1,
        team_id=None,
        created_by_user_id=10,
        updated_by_user_id=10,
        asset_type="generated_image",
        storage_backend="local",
        storage_key="persona/generated.png",
    )

    assert asset.updated_by_user_id == 10
