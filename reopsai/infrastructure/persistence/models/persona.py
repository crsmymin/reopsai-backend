from __future__ import annotations

from typing import Optional, Union

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from reopsai.infrastructure.persistence.base import Base


JsonValue = Optional[Union[dict, list]]


class PersonaFolder(Base):
    __tablename__ = "persona_folders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        Index(
            "ux_persona_folders_company_name_active",
            "company_id",
            "name",
            unique=True,
            postgresql_where=deleted_at.is_(None),
        ),
    )


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    folder_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("persona_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    source_external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tag: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    gender: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    personality: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(32), nullable=False, server_default="ko")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, server_default="manual", index=True)
    source_data: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    image_asset_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("persona_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    image_mime_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    locale: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attitudes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    biography: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    demeanour: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ethnicity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    interests: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    motivation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upbringing: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    additional_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    behaviours: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cultural_background: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_country: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    income: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    locations: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    organisation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preferences: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role_area: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role_level: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    social_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telecom_usage: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    telecom_values: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    ux_interaction: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    telecom_behavior_dimensions: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    telecom_behavior_scores: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    profile: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    telecom_profile: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    generation_metadata: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    interview_pack: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    interview_pack_source_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    interview_pack_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    interview_pack_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    interview_pack_updated_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaMemorySettings(Base):
    __tablename__ = "persona_memory_settings"
    __table_args__ = (
        UniqueConstraint("persona_id", name="uq_persona_memory_settings_persona"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    enable_memory: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    memory_strength: Mapped[int] = mapped_column(Integer, nullable=False, server_default="70")
    apply_to_chat: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    apply_to_tests: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaActivity(Base):
    __tablename__ = "persona_activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    activity_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    was_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    was_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    metadata_: Mapped[JsonValue] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class PersonaLearnedTrait(Base):
    __tablename__ = "persona_learned_traits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    trait: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    sources: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaAsset(Base):
    __tablename__ = "persona_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, server_default="local")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    byte_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    metadata_: Mapped[JsonValue] = mapped_column("metadata", JSONB, nullable=True)
    data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaFigmaAccount(Base):
    __tablename__ = "persona_figma_accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "created_by_user_id", "figma_user_id", name="uq_persona_figma_accounts_user_figma"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    figma_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    figma_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    figma_handle: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    figma_avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaFigmaFile(Base):
    __tablename__ = "persona_figma_files"
    __table_args__ = (
        UniqueConstraint("company_id", "figma_file_key", name="uq_persona_figma_files_company_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    figma_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persona_figma_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    figma_file_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    figma_file_name: Mapped[str] = mapped_column(Text, nullable=False)
    figma_file_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending", index=True)
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaFigmaFlow(Base):
    __tablename__ = "persona_figma_flows"
    __table_args__ = (
        UniqueConstraint("figma_file_id", "figma_start_node_id", name="uq_persona_figma_flows_file_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    figma_file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persona_figma_files.id", ondelete="CASCADE"), nullable=False, index=True)
    figma_page_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    figma_page_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    figma_start_node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    figma_flow_name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", index=True)
    metadata_: Mapped[JsonValue] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaUITest(Base):
    __tablename__ = "persona_ui_tests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pc")
    validation_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="single")
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="screen")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    persona_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    screen_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    summary: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    source_data: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaUITestResult(Base):
    __tablename__ = "persona_ui_test_results"
    __table_args__ = (
        UniqueConstraint("test_id", "persona_id", name="uq_persona_ui_test_results_test_persona"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persona_ui_tests.id", ondelete="CASCADE"), nullable=False, index=True)
    persona_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("personas.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="completed", index=True)
    screen_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    choice: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    persona_goal_fit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scores: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    feedback: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    pin_comments: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    flow_analysis: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    persona_snapshot: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    evidence_ids: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    strengths: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    risks: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    recommendations: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    screen_insights: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaABTest(Base):
    __tablename__ = "persona_ab_tests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    service_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, server_default="single")
    screens: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    transitions: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    context_data: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    summary: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enable_consistency_validation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    consistency_run_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaABTestResult(Base):
    __tablename__ = "persona_ab_test_results"
    __table_args__ = (
        UniqueConstraint("ab_test_id", "persona_id", name="uq_persona_ab_test_results_test_persona"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    ab_test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persona_ab_tests.id", ondelete="CASCADE"), nullable=False, index=True)
    persona_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("personas.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="completed", index=True)
    persona_snapshot: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    scores: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    feedback: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    evidence_ids: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaInterview(Base):
    __tablename__ = "persona_interviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    product_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    length: Mapped[str] = mapped_column(String(32), nullable=False, server_default="quick")
    question_set: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pack_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    persona_ids: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    summary: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaInterviewChunk(Base):
    __tablename__ = "persona_interview_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "external_chunk_id", name="uq_persona_interview_chunks_source_external"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("persona_interview_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_chunk_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    experience_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_variables: Mapped[JsonValue] = mapped_column(JSONB, nullable=False, server_default="[]")
    behavioral_signals: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    tags: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    evidence_strength: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    embedding_vector_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    embedded_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersonaInterviewSource(Base):
    __tablename__ = "persona_interview_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    participant_code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False, server_default="ko")
    source_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="uploaded", index=True)
    processing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[JsonValue] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class PersonaInterviewResult(Base):
    __tablename__ = "persona_interview_results"
    __table_args__ = (
        UniqueConstraint("interview_id", "persona_id", name="uq_persona_interview_results_interview_persona"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    interview_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persona_interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    persona_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("personas.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="completed", index=True)
    persona_snapshot: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    summary: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    turns: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    pack: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[JsonValue] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
