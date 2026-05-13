from __future__ import annotations

from sqlalchemy import and_, func, select
from werkzeug.security import generate_password_hash

from db.models.core import (
    Artifact,
    Company,
    CompanyMember,
    CompanyUsageEvent,
    Project,
    Study,
    User,
    UserFeedback,
)
from reopsai.shared.usage_metering import ensure_company_initial_grant


DEFAULT_ENTERPRISE_PASSWORD = "0000"
BUSINESS_ACCOUNT_TYPE = "business"


class AdminBackofficeRepository:
    @staticmethod
    def get_user_by_id(session, user_id):
        return session.execute(select(User).where(User.id == int(user_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_company(session, company_id):
        if not company_id:
            return None
        return session.execute(select(Company).where(Company.id == int(company_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def get_company_name(session, company_id, fallback=None):
        company = AdminBackofficeRepository.get_company(session, company_id)
        return company.name if company else fallback

    @staticmethod
    def get_or_create_company(session, name):
        company_name = (name or "").strip()
        if not company_name:
            return None
        company = session.execute(
            select(Company).where(func.lower(Company.name) == company_name.lower()).limit(1)
        ).scalar_one_or_none()
        if company:
            return company
        company = Company(name=company_name, status="active")
        session.add(company)
        session.flush()
        session.refresh(company)
        ensure_company_initial_grant(session, company.id)
        return company

    @staticmethod
    def owner_company_ids(session, user_id):
        return session.execute(
            select(CompanyMember.company_id).where(
                CompanyMember.user_id == int(user_id),
                CompanyMember.role == "owner",
            )
        ).scalars().all()

    @staticmethod
    def is_company_member(session, *, company_ids, user_id):
        if not company_ids:
            return False
        return (
            session.execute(
                select(CompanyMember.id)
                .where(
                    and_(
                        CompanyMember.company_id.in_(company_ids),
                        CompanyMember.user_id == int(user_id),
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    @staticmethod
    def count_owner_memberships(session, user_id):
        return (
            session.execute(
                select(func.count())
                .select_from(CompanyMember)
                .where(CompanyMember.user_id == int(user_id), CompanyMember.role == "owner")
            ).scalar_one()
            or 0
        )

    @staticmethod
    def count_company_memberships(session, user_id):
        return (
            session.execute(
                select(func.count()).select_from(CompanyMember).where(CompanyMember.user_id == int(user_id))
            ).scalar_one()
            or 0
        )

    @staticmethod
    def count_user_projects(session, user_id):
        return (
            session.execute(select(func.count()).select_from(Project).where(Project.owner_id == int(user_id))).scalar_one()
            or 0
        )

    @staticmethod
    def count_user_usage_events(session, user_id):
        return (
            session.execute(
                select(func.count()).select_from(CompanyUsageEvent).where(CompanyUsageEvent.user_id == int(user_id))
            ).scalar_one()
            or 0
        )

    @staticmethod
    def delete_user(session, user):
        session.delete(user)

    @staticmethod
    def list_non_admin_users(session):
        return session.execute(
            select(User)
            .where(func.lower(User.tier).notin_(["super", "admin"]))
            .order_by(User.created_at.desc())
        ).scalars().all()

    @staticmethod
    def user_project_ids(session, user_id):
        return session.execute(select(Project.id).where(Project.owner_id == int(user_id))).scalars().all()

    @staticmethod
    def study_ids_for_projects(session, project_ids):
        if not project_ids:
            return []
        return session.execute(select(Study.id).where(Study.project_id.in_(project_ids))).scalars().all()

    @staticmethod
    def count_artifacts_by_type(session, study_ids, artifact_type):
        if not study_ids:
            return 0
        return (
            session.execute(
                select(func.count())
                .select_from(Artifact)
                .where(and_(Artifact.study_id.in_(study_ids), Artifact.artifact_type == artifact_type))
            ).scalar_one()
            or 0
        )

    @staticmethod
    def get_first_company_membership(session, user_id):
        return session.execute(
            select(CompanyMember).where(CompanyMember.user_id == int(user_id)).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_owner_company_membership(session, user_id):
        return session.execute(
            select(CompanyMember)
            .where(CompanyMember.user_id == int(user_id), CompanyMember.role == "owner")
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def set_business_owner(session, *, user, company_name, department):
        user.tier = "enterprise"
        user.account_type = BUSINESS_ACCOUNT_TYPE
        user.password_hash = generate_password_hash(DEFAULT_ENTERPRISE_PASSWORD)
        user.password_reset_required = True
        if department is not None:
            user.department = department
        if not company_name:
            email = user.email or ""
            company_name = email.split("@", 1)[1].split(".")[0] if "@" in email else f"Business {user.id}"
        company = AdminBackofficeRepository.get_or_create_company(session, company_name)
        if company:
            user.company_id = company.id
        membership = session.execute(
            select(CompanyMember)
            .where(CompanyMember.company_id == user.company_id, CompanyMember.user_id == user.id)
            .limit(1)
        ).scalar_one_or_none()
        if membership:
            membership.role = "owner"
        elif user.company_id:
            session.add(CompanyMember(company_id=user.company_id, user_id=user.id, role="owner"))
        session.flush()
        return company

    @staticmethod
    def admin_stats(session):
        user_rows = session.execute(select(User.id, User.tier)).all()
        total_projects = session.execute(select(func.count()).select_from(Project)).scalar_one() or 0
        total_studies = session.execute(select(func.count()).select_from(Study)).scalar_one() or 0
        return user_rows, int(total_projects), int(total_studies)

    @staticmethod
    def list_user_projects(session, user_id):
        return session.execute(
            select(Project)
            .where(Project.owner_id == int(user_id))
            .order_by(Project.created_at.desc())
        ).scalars().all()

    @staticmethod
    def list_user_study_rows(session, user_id):
        project_ids = AdminBackofficeRepository.user_project_ids(session, user_id)
        if not project_ids:
            return []
        return session.execute(
            select(Study, Project.name)
            .join(Project, Project.id == Study.project_id)
            .where(Study.project_id.in_(project_ids))
            .order_by(Study.created_at.desc())
        ).all()

    @staticmethod
    def get_study(session, study_id):
        return session.execute(select(Study).where(Study.id == int(study_id)).limit(1)).scalar_one_or_none()

    @staticmethod
    def list_study_artifacts(session, study_id):
        return session.execute(
            select(Artifact)
            .where(Artifact.study_id == int(study_id))
            .order_by(Artifact.created_at.desc())
        ).scalars().all()

    @staticmethod
    def create_feedback(session, *, category, vote, comment, user_id, study_id, study_name):
        feedback = UserFeedback(
            category=category,
            vote=vote,
            comment=comment if comment else None,
            user_id=user_id,
            study_id=int(study_id) if study_id else None,
            study_name=study_name if study_name else None,
        )
        session.add(feedback)
        session.flush()
        session.refresh(feedback)
        return feedback

    @staticmethod
    def get_feedback_for_user(session, *, feedback_id, user_id):
        return session.execute(
            select(UserFeedback)
            .where(and_(UserFeedback.id == int(feedback_id), UserFeedback.user_id == int(user_id)))
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def list_feedback(session, category=None):
        query = select(UserFeedback).order_by(UserFeedback.created_at.desc())
        if category:
            query = query.where(UserFeedback.category == category)
        return session.execute(query).scalars().all()
