import uuid
from typing import Optional, Union

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    google_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, server_default="free", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    product_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[Union[dict, list]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    initial_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[Union[dict, list]]] = mapped_column(JSONB, nullable=True)
    methodologies: Mapped[Optional[Union[dict, list]]] = mapped_column(JSONB, nullable=True)
    participant_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    timeline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    budget: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_audience: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    additional_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default="member")
    joined_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StudySchedule(Base):
    __tablename__ = "study_schedules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    final_participants: Mapped[Union[dict, list]] = mapped_column(JSONB, nullable=False, server_default="[]")
    saved_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vote: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    study_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("studies.id", ondelete="SET NULL"), nullable=True, index=True)
    study_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ArtifactEditHistory(Base):
    __tablename__ = "artifact_edit_history"
    __table_args__ = (
        CheckConstraint(
            "(selection_from IS NULL AND selection_to IS NULL) "
            "OR (selection_from IS NOT NULL AND selection_to IS NOT NULL AND selection_from < selection_to)",
            name="chk_artifact_edit_history_selection_span",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    before_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    after_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    selection_from: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    selection_to: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
