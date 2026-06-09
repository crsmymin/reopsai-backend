from __future__ import annotations

from sqlalchemy import delete, or_, select, update

from reopsai.infrastructure.persistence.models.core import (
    Artifact,
    ArtifactEditHistory,
    CompanyMember,
    CompanyTokenLedger,
    CompanyUsageEvent,
    LlmUsageDailyAggregate,
    LlmUsageEvent,
    Project,
    Study,
    StudySchedule,
    Team,
    TeamMember,
    TeamUsageEvent,
    User,
    UserFeedback,
)
from reopsai.infrastructure.persistence.models.persona import (
    Persona,
    PersonaABTest,
    PersonaABTestResult,
    PersonaActivity,
    PersonaAsset,
    PersonaFigmaAccount,
    PersonaFigmaFile,
    PersonaFigmaFlow,
    PersonaFolder,
    PersonaInterview,
    PersonaInterviewChunk,
    PersonaInterviewResult,
    PersonaInterviewSource,
    PersonaLearnedTrait,
    PersonaMemorySettings,
    PersonaResultShare,
    PersonaUITest,
    PersonaUITestResult,
)


def _ids(session, stmt):
    return list(session.execute(stmt).scalars().all())


def _rowcount(result):
    return int(result.rowcount or 0)


def _delete_count(session, stmt):
    return _rowcount(session.execute(stmt))


def _delete_where_ids(session, model, column, ids):
    if not ids:
        return 0
    return _delete_count(session, delete(model).where(column.in_(ids)))


def _null_updated_by(session, model, user_id):
    if not hasattr(model, "updated_by_user_id"):
        return 0
    return _rowcount(
        session.execute(
            update(model)
            .where(model.updated_by_user_id == int(user_id))
            .values(updated_by_user_id=None)
        )
    )


def delete_user_account_data(session, *, user_id):
    """Hard-delete a user account and data owned by that account."""

    user_id_int = int(user_id)

    project_ids = _ids(session, select(Project.id).where(Project.owner_id == user_id_int))
    study_ids = _ids(session, select(Study.id).where(Study.project_id.in_(project_ids))) if project_ids else []
    artifact_filters = [Artifact.owner_id == user_id_int]
    if study_ids:
        artifact_filters.append(Artifact.study_id.in_(study_ids))
    artifact_ids = _ids(session, select(Artifact.id).where(or_(*artifact_filters)))

    persona_ids = _ids(session, select(Persona.id).where(Persona.created_by_user_id == user_id_int))
    folder_ids = _ids(session, select(PersonaFolder.id).where(PersonaFolder.created_by_user_id == user_id_int))
    asset_ids = _ids(session, select(PersonaAsset.id).where(PersonaAsset.created_by_user_id == user_id_int))
    ui_test_ids = _ids(session, select(PersonaUITest.id).where(PersonaUITest.created_by_user_id == user_id_int))
    ab_test_ids = _ids(session, select(PersonaABTest.id).where(PersonaABTest.created_by_user_id == user_id_int))
    interview_ids = _ids(session, select(PersonaInterview.id).where(PersonaInterview.created_by_user_id == user_id_int))
    source_ids = _ids(
        session,
        select(PersonaInterviewSource.id).where(PersonaInterviewSource.created_by_user_id == user_id_int),
    )
    figma_account_ids = _ids(
        session,
        select(PersonaFigmaAccount.id).where(PersonaFigmaAccount.created_by_user_id == user_id_int),
    )
    figma_file_ids = (
        _ids(session, select(PersonaFigmaFile.id).where(PersonaFigmaFile.figma_account_id.in_(figma_account_ids)))
        if figma_account_ids
        else []
    )
    llm_event_ids = _ids(session, select(LlmUsageEvent.id).where(LlmUsageEvent.user_id == user_id_int))
    owner_company_ids = _ids(
        session,
        select(CompanyMember.company_id).where(
            CompanyMember.user_id == user_id_int,
            CompanyMember.role == "owner",
        ),
    )

    affected = {
        "owned_companies_released": len(owner_company_ids),
        "company_memberships": _delete_count(
            session,
            delete(CompanyMember).where(CompanyMember.user_id == user_id_int),
        ),
        "team_memberships": _delete_count(
            session,
            delete(TeamMember).where(TeamMember.user_id == user_id_int),
        ),
        "owned_teams_released": _rowcount(
            session.execute(update(Team).where(Team.owner_id == user_id_int).values(owner_id=None))
        ),
    }

    affected["usage"] = {
        "company_usage_events": _delete_count(
            session,
            delete(CompanyUsageEvent).where(CompanyUsageEvent.user_id == user_id_int),
        ),
        "team_usage_events": _delete_count(
            session,
            delete(TeamUsageEvent).where(TeamUsageEvent.user_id == user_id_int),
        ),
        "llm_usage_daily_aggregates": _delete_count(
            session,
            delete(LlmUsageDailyAggregate).where(LlmUsageDailyAggregate.user_id == user_id_int),
        ),
        "llm_usage_events": 0,
        "company_token_ledgers_released": _rowcount(
            session.execute(
                update(CompanyTokenLedger)
                .where(CompanyTokenLedger.created_by == user_id_int)
                .values(created_by=None)
            )
        ),
        "company_token_ledger_event_refs_released": 0,
    }
    if llm_event_ids:
        affected["usage"]["company_token_ledger_event_refs_released"] = _rowcount(
            session.execute(
                update(CompanyTokenLedger)
                .where(CompanyTokenLedger.reference_event_id.in_(llm_event_ids))
                .values(reference_event_id=None)
            )
        )
        affected["usage"]["llm_usage_events"] = _delete_where_ids(session, LlmUsageEvent, LlmUsageEvent.id, llm_event_ids)

    affected["feedback"] = _delete_count(
        session,
        delete(UserFeedback).where(UserFeedback.user_id == user_id_int),
    )

    share_filters = [PersonaResultShare.created_by_user_id == user_id_int]
    if ui_test_ids:
        share_filters.append(
            (PersonaResultShare.resource_type == "ui_test") & PersonaResultShare.resource_id.in_(ui_test_ids)
        )
    if ab_test_ids:
        share_filters.append(
            (PersonaResultShare.resource_type == "ab_test") & PersonaResultShare.resource_id.in_(ab_test_ids)
        )
    if interview_ids:
        share_filters.append(
            (PersonaResultShare.resource_type == "interview") & PersonaResultShare.resource_id.in_(interview_ids)
        )
    affected["persona"] = {
        "result_shares": _delete_count(session, delete(PersonaResultShare).where(or_(*share_filters))),
        "ui_test_results": 0,
        "ab_test_results": 0,
        "interview_results": 0,
        "activities": 0,
        "memory_settings": _delete_where_ids(
            session,
            PersonaMemorySettings,
            PersonaMemorySettings.persona_id,
            persona_ids,
        ),
        "learned_traits": _delete_where_ids(
            session,
            PersonaLearnedTrait,
            PersonaLearnedTrait.persona_id,
            persona_ids,
        ),
        "interview_chunks": _delete_where_ids(
            session,
            PersonaInterviewChunk,
            PersonaInterviewChunk.source_id,
            source_ids,
        ),
        "figma_flows": _delete_where_ids(
            session,
            PersonaFigmaFlow,
            PersonaFigmaFlow.figma_file_id,
            figma_file_ids,
        ),
        "figma_files": _delete_where_ids(
            session,
            PersonaFigmaFile,
            PersonaFigmaFile.id,
            figma_file_ids,
        ),
        "figma_accounts": _delete_where_ids(
            session,
            PersonaFigmaAccount,
            PersonaFigmaAccount.id,
            figma_account_ids,
        ),
    }

    ui_result_filters = []
    if ui_test_ids:
        ui_result_filters.append(PersonaUITestResult.test_id.in_(ui_test_ids))
    if persona_ids:
        ui_result_filters.append(PersonaUITestResult.persona_id.in_(persona_ids))
    if ui_result_filters:
        affected["persona"]["ui_test_results"] = _delete_count(
            session,
            delete(PersonaUITestResult).where(or_(*ui_result_filters)),
        )

    ab_result_filters = []
    if ab_test_ids:
        ab_result_filters.append(PersonaABTestResult.ab_test_id.in_(ab_test_ids))
    if persona_ids:
        ab_result_filters.append(PersonaABTestResult.persona_id.in_(persona_ids))
    if ab_result_filters:
        affected["persona"]["ab_test_results"] = _delete_count(
            session,
            delete(PersonaABTestResult).where(or_(*ab_result_filters)),
        )

    interview_result_filters = []
    if interview_ids:
        interview_result_filters.append(PersonaInterviewResult.interview_id.in_(interview_ids))
    if persona_ids:
        interview_result_filters.append(PersonaInterviewResult.persona_id.in_(persona_ids))
    if interview_result_filters:
        affected["persona"]["interview_results"] = _delete_count(
            session,
            delete(PersonaInterviewResult).where(or_(*interview_result_filters)),
        )

    activity_filters = []
    if persona_ids:
        activity_filters.append(PersonaActivity.persona_id.in_(persona_ids))
    if ui_test_ids:
        activity_filters.append(
            (PersonaActivity.activity_type == "ui_test")
            & PersonaActivity.activity_id.in_([str(test_id) for test_id in ui_test_ids])
        )
    if ab_test_ids:
        activity_filters.append(
            (PersonaActivity.activity_type == "ab_test")
            & PersonaActivity.activity_id.in_([str(test_id) for test_id in ab_test_ids])
        )
    if interview_ids:
        activity_filters.append(
            (PersonaActivity.activity_type == "interview")
            & PersonaActivity.activity_id.in_([str(interview_id) for interview_id in interview_ids])
        )
    if activity_filters:
        affected["persona"]["activities"] = _delete_count(
            session,
            delete(PersonaActivity).where(or_(*activity_filters)),
        )

    updated_by_models = (
        PersonaFolder,
        Persona,
        PersonaAsset,
        PersonaFigmaAccount,
        PersonaUITest,
        PersonaABTest,
        PersonaInterview,
        PersonaInterviewSource,
    )
    affected["persona"]["updated_by_refs_released"] = sum(
        _null_updated_by(session, model, user_id_int) for model in updated_by_models
    )

    if folder_ids:
        session.execute(update(Persona).where(Persona.folder_id.in_(folder_ids)).values(folder_id=None))
    if asset_ids:
        session.execute(update(Persona).where(Persona.image_asset_id.in_(asset_ids)).values(image_asset_id=None))

    affected["persona"].update(
        {
            "ui_tests": _delete_where_ids(session, PersonaUITest, PersonaUITest.id, ui_test_ids),
            "ab_tests": _delete_where_ids(session, PersonaABTest, PersonaABTest.id, ab_test_ids),
            "interviews": _delete_where_ids(session, PersonaInterview, PersonaInterview.id, interview_ids),
            "interview_sources": _delete_where_ids(
                session,
                PersonaInterviewSource,
                PersonaInterviewSource.id,
                source_ids,
            ),
            "personas": _delete_where_ids(session, Persona, Persona.id, persona_ids),
            "assets": _delete_where_ids(session, PersonaAsset, PersonaAsset.id, asset_ids),
            "folders": _delete_where_ids(session, PersonaFolder, PersonaFolder.id, folder_ids),
        }
    )

    artifact_history_filters = []
    if artifact_ids:
        artifact_history_filters.append(ArtifactEditHistory.artifact_id.in_(artifact_ids))
    artifact_history_filters.append(ArtifactEditHistory.user_id == user_id_int)
    affected["research"] = {
        "artifact_edit_histories": _delete_count(
            session,
            delete(ArtifactEditHistory).where(or_(*artifact_history_filters)),
        ),
        "artifacts": _delete_where_ids(session, Artifact, Artifact.id, artifact_ids),
        "study_schedules": _delete_where_ids(session, StudySchedule, StudySchedule.study_id, study_ids),
        "studies": _delete_where_ids(session, Study, Study.id, study_ids),
        "projects": _delete_where_ids(session, Project, Project.id, project_ids),
    }

    affected["users"] = _delete_count(session, delete(User).where(User.id == user_id_int))
    session.flush()
    return affected
