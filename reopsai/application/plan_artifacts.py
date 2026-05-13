"""Artifact state helpers for background plan generation."""

from __future__ import annotations

from api_logger import log_analysis_complete, log_data_processing, log_error


def complete_oneshot_plan_artifact(record_service, *, artifact_id, study_id, final_plan):
    record_service.complete_artifact(
        artifact_id=artifact_id,
        content=final_plan,
    )
    log_analysis_complete()
    log_data_processing(
        "계획서 생성 완료",
        {"artifact_id": artifact_id, "study_id": study_id},
        "백그라운드 계획서 생성 성공",
    )


def delete_oneshot_plan_artifact(record_service, *, artifact_id):
    record_service.delete_artifact(artifact_id=artifact_id)


def cleanup_oneshot_plan_artifact_after_error(record_service, *, artifact_id):
    try:
        record_service.delete_artifact(artifact_id=artifact_id)
    except Exception as delete_error:
        log_error(delete_error, f"생성 오류 후 artifact 삭제 실패: artifact_id={artifact_id}")


def complete_conversation_plan_artifact(record_service, *, artifact_id, study_id, content):
    record_service.complete_artifact(
        artifact_id=artifact_id,
        content=content,
    )
    log_analysis_complete()
    log_data_processing(
        "ConversationStudyMaker 계획서 생성 완료",
        {"artifact_id": artifact_id, "study_id": study_id},
        "성공",
    )


def fail_conversation_plan_artifact(record_service, *, artifact_id, error):
    try:
        record_service.fail_artifact(artifact_id=artifact_id, message=str(error))
    except Exception as update_error:
        log_error(update_error, f"ConversationStudyMaker 실패 후 artifact 업데이트 실패: artifact_id={artifact_id}")
