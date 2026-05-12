from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reopsai_backend.infrastructure.repositories import (
    AdminBackofficeRepository,
    DEFAULT_ENTERPRISE_PASSWORD,
)


@dataclass(frozen=True)
class AdminBackofficeResult:
    status: str
    data: Any = None


def serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def normalize_tier(tier):
    value = (tier or "").strip().lower()
    return "super" if value == "admin" else value


class AdminBackofficeService:
    _DEFAULT_SESSION_FACTORY = object()

    def __init__(self, repository=None, session_factory=_DEFAULT_SESSION_FACTORY):
        if repository is None:
            repository = AdminBackofficeRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai_backend.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory

    def db_ready(self):
        return self.session_factory is not None

    def _company_name_for(self, db_session, company_id, fallback=None):
        return self.repository.get_company_name(db_session, company_id, fallback)

    def _user_payload(self, db_session, user, membership=None):
        tier = user.tier or "free"
        if membership is None:
            membership = self.repository.get_first_company_membership(db_session, user.id)
        company_id = user.company_id or (membership.company_id if membership else None)
        return {
            "id": user.id,
            "email": user.email,
            "company_id": company_id,
            "company_name": self._company_name_for(db_session, company_id),
            "department": user.department,
            "tier": tier,
            "account_type": user.account_type or "individual",
            "password_reset_required": bool(user.password_reset_required),
            "created_at": serialize_dt(user.created_at),
        }

    def _deleted_user_payload(self, db_session, user):
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "tier": user.tier,
            "account_type": user.account_type,
            "company_id": user.company_id,
            "company_name": self._company_name_for(db_session, user.company_id),
            "department": user.department,
        }

    def _user_list_payload(self, db_session, user):
        project_ids = self.repository.user_project_ids(db_session, user.id)
        study_ids = self.repository.study_ids_for_projects(db_session, project_ids)
        membership = self.repository.get_first_company_membership(db_session, user.id)
        company_id = user.company_id or (membership.company_id if membership else None)
        return {
            "id": user.id,
            "email": user.email,
            "company_id": company_id,
            "company_name": self._company_name_for(db_session, company_id),
            "department": user.department,
            "tier": user.tier or "free",
            "account_type": user.account_type or "individual",
            "password_reset_required": bool(user.password_reset_required),
            "created_at": serialize_dt(user.created_at),
            "google_id": user.google_id,
            "project_count": len(project_ids),
            "study_count": len(study_ids),
            "plan_count": int(self.repository.count_artifacts_by_type(db_session, study_ids, "plan")),
            "guideline_count": int(self.repository.count_artifacts_by_type(db_session, study_ids, "guideline")),
            "screener_count": int(self.repository.count_artifacts_by_type(db_session, study_ids, "survey")),
            "business_company_id": company_id,
            "business_company_role": membership.role if membership else None,
        }

    def _study_payload(self, study, project_name=None):
        payload = {
            "id": study.id,
            "project_id": study.project_id,
            "name": study.name,
            "slug": study.slug,
            "initial_input": study.initial_input,
            "keywords": study.keywords,
            "methodologies": study.methodologies,
            "participant_count": study.participant_count,
            "start_date": study.start_date.isoformat() if study.start_date else None,
            "end_date": study.end_date.isoformat() if study.end_date else None,
            "timeline": study.timeline,
            "budget": study.budget,
            "target_audience": study.target_audience,
            "additional_requirements": study.additional_requirements,
            "created_at": serialize_dt(study.created_at),
            "updated_at": serialize_dt(study.updated_at),
        }
        if project_name is not None:
            payload["projects"] = {"name": project_name}
        return payload

    def _feedback_payload(self, feedback):
        return {
            "id": feedback.id,
            "category": feedback.category,
            "vote": feedback.vote,
            "comment": feedback.comment,
            "user_id": feedback.user_id,
            "study_id": feedback.study_id,
            "study_name": feedback.study_name,
            "created_at": serialize_dt(feedback.created_at),
            "updated_at": serialize_dt(feedback.updated_at),
        }

    def delete_user(self, *, user_id, requester_id, requester_tier) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            target = self.repository.get_user_by_id(db_session, user_id)
            if not target:
                return AdminBackofficeResult("not_found")

            is_super = normalize_tier(requester_tier) == "super"
            owner_company_ids = self.repository.owner_company_ids(db_session, requester_id)
            is_owned_company_member = self.repository.is_company_member(
                db_session,
                company_ids=owner_company_ids,
                user_id=user_id,
            )
            if not (is_super or is_owned_company_member):
                return AdminBackofficeResult("forbidden")

            if not is_super and normalize_tier(target.tier) == "super":
                return AdminBackofficeResult("target_super_forbidden")

            owner_company_count = self.repository.count_owner_memberships(db_session, user_id)
            if not is_super and owner_company_count:
                return AdminBackofficeResult("target_owner_forbidden")

            membership_count = self.repository.count_company_memberships(db_session, user_id)
            project_count = self.repository.count_user_projects(db_session, user_id)
            usage_event_count = self.repository.count_user_usage_events(db_session, user_id)
            payload = self._deleted_user_payload(db_session, target)
            self.repository.delete_user(db_session, target)
            return AdminBackofficeResult(
                "ok",
                {
                    "deleted_user": payload,
                    "affected": {
                        "company_memberships": int(membership_count),
                        "owned_companies_released": int(owner_company_count),
                        "owned_projects": int(project_count),
                        "usage_events_anonymized": int(usage_event_count),
                    },
                },
            )

    def list_users(self) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            users = self.repository.list_non_admin_users(db_session)
            payload = [self._user_list_payload(db_session, user) for user in users]
            return AdminBackofficeResult("ok", {"users": payload, "count": len(payload)})

    def update_user_tier(self, *, user_id, tier) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, user_id)
            if not user:
                return AdminBackofficeResult("not_found")
            user.tier = tier
            return AdminBackofficeResult(
                "ok",
                {
                    "id": user.id,
                    "email": user.email,
                    "tier": user.tier,
                    "created_at": serialize_dt(user.created_at),
                    "google_id": user.google_id,
                },
            )

    def get_user_enterprise_info(self, *, user_id) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, user_id)
            if not user:
                return AdminBackofficeResult("not_found")
            membership = self.repository.get_first_company_membership(db_session, user_id)
            company_id = user.company_id or (membership.company_id if membership else None)
            company = self.repository.get_company(db_session, company_id) if company_id else None
            company_payload = (
                {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "role": membership.role if membership else None,
                    "joined_at": serialize_dt(membership.joined_at) if membership else None,
                }
                if company
                else None
            )
            tier = user.tier or "free"
            return AdminBackofficeResult(
                "ok",
                {
                    "user": self._user_payload(db_session, user, membership),
                    "tier": tier,
                    "company": company_payload,
                },
            )

    def init_enterprise_team_for_user(self, *, user_id, company_name, department) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            user = self.repository.get_user_by_id(db_session, user_id)
            if not user:
                return AdminBackofficeResult("not_found")

            existing_owner = self.repository.get_owner_company_membership(db_session, user_id)
            if existing_owner:
                company_id = user.company_id or existing_owner.company_id
                return AdminBackofficeResult(
                    "already_exists",
                    {
                        "user": self._user_payload(db_session, user, existing_owner),
                        "company": {
                            "id": existing_owner.company_id,
                            "name": self._company_name_for(db_session, existing_owner.company_id),
                            "role": existing_owner.role,
                        },
                    },
                )

            self.repository.set_business_owner(
                db_session,
                user=user,
                company_name=company_name,
                department=department,
            )
            return AdminBackofficeResult(
                "ok",
                {
                    "user": self._user_payload(db_session, user),
                    "company": {
                        "id": user.company_id,
                        "name": self._company_name_for(db_session, user.company_id),
                    },
                },
            )

    def get_admin_stats(self) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            user_rows, total_projects, total_studies = self.repository.admin_stats(db_session)
            tier_counts = {}
            for _uid, tier in user_rows:
                value = tier or "free"
                tier_counts[value] = tier_counts.get(value, 0) + 1
            return AdminBackofficeResult(
                "ok",
                {
                    "stats": {
                        "total_users": int(len(user_rows)),
                        "tier_counts": tier_counts,
                        "total_projects": int(total_projects),
                        "total_studies": int(total_studies),
                    }
                },
            )

    def get_user_projects(self, *, user_id) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            projects = self.repository.list_user_projects(db_session, user_id)
            payload = [
                {
                    "id": project.id,
                    "owner_id": project.owner_id,
                    "name": project.name,
                    "slug": project.slug,
                    "product_url": project.product_url,
                    "keywords": project.keywords,
                    "created_at": serialize_dt(project.created_at),
                    "updated_at": serialize_dt(project.updated_at),
                }
                for project in projects
            ]
            return AdminBackofficeResult("ok", {"projects": payload, "count": len(payload)})

    def get_user_studies(self, *, user_id) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            rows = self.repository.list_user_study_rows(db_session, user_id)
            studies = [self._study_payload(study, project_name) for study, project_name in rows]
            return AdminBackofficeResult("ok", {"studies": studies, "count": len(studies)})

    def get_study(self, *, study_id) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            study = self.repository.get_study(db_session, study_id)
            if not study:
                return AdminBackofficeResult("not_found")
            return AdminBackofficeResult("ok", self._study_payload(study))

    def get_study_artifacts(self, *, study_id) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            artifacts = self.repository.list_study_artifacts(db_session, study_id)
            return AdminBackofficeResult(
                "ok",
                {
                    "artifacts": [
                        {
                            "id": artifact.id,
                            "study_id": artifact.study_id,
                            "owner_id": artifact.owner_id,
                            "artifact_type": artifact.artifact_type,
                            "content": artifact.content,
                            "status": artifact.status,
                            "created_at": serialize_dt(artifact.created_at),
                            "updated_at": serialize_dt(artifact.updated_at),
                        }
                        for artifact in artifacts
                    ]
                },
            )

    def submit_feedback(self, *, category, vote, comment, user_id, study_id, study_name) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            feedback = self.repository.create_feedback(
                db_session,
                category=category,
                vote="true" if bool(vote) else "false",
                comment=comment,
                user_id=user_id,
                study_id=study_id,
                study_name=study_name,
            )
            return AdminBackofficeResult("ok", self._feedback_payload(feedback))

    def update_feedback_comment(self, *, feedback_id, user_id, comment) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            feedback = self.repository.get_feedback_for_user(db_session, feedback_id=feedback_id, user_id=user_id)
            if not feedback:
                return AdminBackofficeResult("not_found")
            feedback.comment = comment if comment else None
            return AdminBackofficeResult("ok", self._feedback_payload(feedback))

    def list_feedback(self, *, category=None) -> AdminBackofficeResult:
        if not self.db_ready():
            return AdminBackofficeResult("db_unavailable")
        with self.session_factory() as db_session:
            rows = self.repository.list_feedback(db_session, category=category)
            feedback = [self._feedback_payload(row) for row in rows]
            return AdminBackofficeResult(
                "ok",
                {
                    "feedback": feedback,
                    "count": len(feedback),
                    "category": category if category else "all",
                },
            )


admin_backoffice_service = AdminBackofficeService()
