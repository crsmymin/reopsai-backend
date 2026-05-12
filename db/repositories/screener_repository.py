from sqlalchemy import select

from db.models.core import StudySchedule


class ScreenerRepository:
    @staticmethod
    def upsert_study_schedule(session, *, study_id, final_participants, saved_at):
        existing = session.execute(
            select(StudySchedule).where(StudySchedule.study_id == study_id).limit(1)
        ).scalar_one_or_none()
        if existing:
            existing.final_participants = final_participants
            existing.saved_at = saved_at
            row = existing
        else:
            row = StudySchedule(
                study_id=study_id,
                final_participants=final_participants,
                saved_at=saved_at,
            )
            session.add(row)
            session.flush()
            session.refresh(row)

        return {
            'id': row.id,
            'study_id': row.study_id,
            'final_participants': row.final_participants,
            'saved_at': row.saved_at.isoformat() if row.saved_at else None,
            'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        }
