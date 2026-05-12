from __future__ import annotations

from sqlalchemy import delete, func, select, update
from werkzeug.security import generate_password_hash

from db.models.core import Artifact, Company, Project, Study, Team, TeamMember, User


BUSINESS_ACCOUNT_TYPE = "business"
INDIVIDUAL_ACCOUNT_TYPE = "individual"


class AuthRepository:
    @staticmethod
    def get_user_by_id(session, user_id):
        return session.execute(select(User).where(User.id == int(user_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_user_by_email(session, email: str):
        return session.execute(select(User).where(User.email == email).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_user_by_email_lower(session, email: str):
        return session.execute(
            select(User).where(func.lower(User.email) == email.lower()).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_user_for_google_login(session, *, email: str, google_id=None):
        query = select(User).where(User.email == email)
        if google_id:
            query = query.where(User.google_id == google_id)
        return session.execute(query.limit(1)).scalar_one_or_none()

    @staticmethod
    def list_users(session):
        return session.execute(select(User).order_by(User.created_at.desc())).scalars().all()

    @staticmethod
    def create_user(session, *, email: str, name: str, google_id=None):
        user = User(
            email=email,
            name=name,
            google_id=google_id,
            tier="free",
            account_type=INDIVIDUAL_ACCOUNT_TYPE,
            password_reset_required=False,
        )
        session.add(user)
        session.flush()
        session.refresh(user)
        return user

    @staticmethod
    def set_google_id(session, *, user_id, google_id: str):
        session.execute(update(User).where(User.id == int(user_id)).values(google_id=google_id))
        user = AuthRepository.get_user_by_id(session, user_id)
        if user:
            user.google_id = google_id
        return user

    @staticmethod
    def get_company_name(session, company_id):
        if not company_id:
            return None
        return session.execute(
            select(Company.name).where(Company.id == int(company_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_primary_team_id_for_user(session, user_id):
        owner_team_id = session.execute(
            select(Team.id)
            .where(Team.owner_id == int(user_id), Team.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()
        if owner_team_id is not None:
            return int(owner_team_id)

        member_team_id = session.execute(
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == int(user_id), Team.status != "deleted")
            .limit(1)
        ).scalar_one_or_none()
        return int(member_team_id) if member_team_id is not None else None

    @staticmethod
    def update_business_password(session, *, user_id, new_password: str):
        user = AuthRepository.get_user_by_id(session, user_id)
        if not user:
            return None
        user.password_hash = generate_password_hash(new_password)
        user.password_reset_required = False
        session.flush()
        return user

    @staticmethod
    def update_business_profile(session, *, user_id, name=None, department=None, update_department=False):
        user = AuthRepository.get_user_by_id(session, user_id)
        if not user:
            return None
        if name is not None:
            user.name = name
        if update_department:
            user.department = department
        session.flush()
        return user

    @staticmethod
    def get_or_create_dev_user(session, *, email: str, name: str):
        user = AuthRepository.get_user_by_email(session, email)
        if user:
            return user, False
        user = User(
            email=email,
            name=name,
            google_id=f"dev_{email}",
            tier="free",
            account_type=INDIVIDUAL_ACCOUNT_TYPE,
            password_reset_required=False,
        )
        session.add(user)
        session.flush()
        session.refresh(user)
        return user, True

    @staticmethod
    def get_database_test_payload(session):
        count = session.execute(select(func.count()).select_from(User)).scalar_one()
        sample = session.execute(select(User).limit(1)).scalar_one_or_none()
        return int(count or 0), sample

    @staticmethod
    def delete_account_payload(session, *, user_id):
        user_id_int = int(user_id)
        project_ids = session.execute(
            select(Project.id).where(Project.owner_id == user_id_int)
        ).scalars().all()

        if project_ids:
            study_ids = session.execute(
                select(Study.id).where(Study.project_id.in_(project_ids))
            ).scalars().all()
        else:
            study_ids = []

        if study_ids:
            total_artifacts = (
                session.execute(
                    select(func.count()).select_from(Artifact).where(Artifact.study_id.in_(study_ids))
                ).scalar_one()
                or 0
            )
        else:
            total_artifacts = 0

        session.execute(delete(User).where(User.id == user_id_int))
        return {
            "deleted_projects": len(project_ids),
            "deleted_studies": len(study_ids),
            "deleted_artifacts": int(total_artifacts),
        }
