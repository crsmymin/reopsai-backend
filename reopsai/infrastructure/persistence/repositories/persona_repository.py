from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_, select

from reopsai.infrastructure.persistence.models.core import CompanyMember
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
    PersonaInterviewResult,
    PersonaLearnedTrait,
    PersonaMemorySettings,
    PersonaUITest,
    PersonaUITestResult,
)


ADMIN_ROLES = {"owner", "admin"}


def utcnow():
    return datetime.now(timezone.utc)


class PersonaRepository:
    @staticmethod
    def get_membership(session, *, company_id: int, user_id: int):
        return session.execute(
            select(CompanyMember)
            .where(
                CompanyMember.company_id == int(company_id),
                CompanyMember.user_id == int(user_id),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def is_company_admin(session, *, company_id: int, user_id: int) -> bool:
        membership = PersonaRepository.get_membership(session, company_id=company_id, user_id=user_id)
        return bool(membership and (membership.role or "member").lower() in ADMIN_ROLES)

    @staticmethod
    def can_modify_record(session, record, *, company_id: int, user_id: int) -> bool:
        if not record or int(record.company_id) != int(company_id):
            return False
        if getattr(record, "created_by_user_id", None) == int(user_id):
            return True
        return PersonaRepository.is_company_admin(session, company_id=company_id, user_id=user_id)

    @staticmethod
    def list_folders(session, *, company_id: int):
        return session.execute(
            select(PersonaFolder)
            .where(PersonaFolder.company_id == int(company_id), PersonaFolder.deleted_at.is_(None))
            .order_by(PersonaFolder.is_default.desc(), PersonaFolder.created_at.asc(), PersonaFolder.id.asc())
        ).scalars().all()

    @staticmethod
    def get_folder(session, *, company_id: int, folder_id: int):
        return session.execute(
            select(PersonaFolder)
            .where(
                PersonaFolder.company_id == int(company_id),
                PersonaFolder.id == int(folder_id),
                PersonaFolder.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_folder(session, *, company_id: int, user_id: int, data: dict):
        folder = PersonaFolder(
            company_id=int(company_id),
            team_id=data.get("team_id"),
            created_by_user_id=int(user_id),
            updated_by_user_id=int(user_id),
            name=data["name"],
            description=data.get("description"),
            color=data.get("color"),
            is_default=bool(data.get("is_default", False)),
        )
        session.add(folder)
        session.flush()
        return folder

    @staticmethod
    def update_folder(session, folder, *, user_id: int, data: dict):
        for key in ("name", "description", "color", "is_default"):
            if key in data:
                setattr(folder, key, data[key])
        folder.updated_by_user_id = int(user_id)
        folder.updated_at = utcnow()
        session.flush()
        return folder

    @staticmethod
    def soft_delete_folder(session, folder, *, user_id: int):
        folder.deleted_at = utcnow()
        folder.updated_by_user_id = int(user_id)
        folder.updated_at = utcnow()
        session.query(Persona).filter(
            Persona.company_id == folder.company_id,
            Persona.folder_id == folder.id,
            Persona.deleted_at.is_(None),
        ).update({Persona.folder_id: None, Persona.updated_by_user_id: int(user_id), Persona.updated_at: utcnow()})
        session.flush()

    @staticmethod
    def list_personas(
        session,
        *,
        company_id: int,
        page: int,
        limit: int,
        search: Optional[str] = None,
        folder_id: Optional[int] = None,
        no_folder: bool = False,
    ):
        filters = [Persona.company_id == int(company_id), Persona.deleted_at.is_(None)]
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(or_(Persona.name.ilike(pattern), Persona.title.ilike(pattern)))
        if folder_id is not None:
            filters.append(Persona.folder_id == int(folder_id))
        if no_folder:
            filters.append(Persona.folder_id.is_(None))

        total = session.execute(select(func.count()).select_from(Persona).where(*filters)).scalar_one()
        items = session.execute(
            select(Persona)
            .where(*filters)
            .order_by(Persona.created_at.desc(), Persona.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        ).scalars().all()
        return items, int(total or 0)

    @staticmethod
    def list_existing_persona_summaries(session, *, company_id: int, limit: Optional[int] = None):
        query = (
            select(Persona)
            .where(Persona.company_id == int(company_id), Persona.deleted_at.is_(None))
            .order_by(Persona.created_at.desc(), Persona.id.desc())
        )
        if limit is not None:
            query = query.limit(int(limit))
        rows = session.execute(query).scalars().all()
        summaries = []
        for row in rows:
            summaries.append(
                {
                    "name": row.name,
                    "age": row.age,
                    "generation": row.generation,
                    "title": row.title,
                    "roleArea": row.role_area,
                    "personality": row.personality,
                }
            )
        return summaries

    @staticmethod
    def list_personas_by_ids(session, *, company_id: int, persona_ids: Iterable[int]):
        ids = [int(persona_id) for persona_id in persona_ids if persona_id is not None]
        if not ids:
            return []
        return session.execute(
            select(Persona)
            .where(Persona.company_id == int(company_id), Persona.id.in_(ids), Persona.deleted_at.is_(None))
            .order_by(Persona.created_at.desc(), Persona.id.desc())
        ).scalars().all()

    @staticmethod
    def list_all_personas(session, *, company_id: int):
        return session.execute(
            select(Persona)
            .where(Persona.company_id == int(company_id), Persona.deleted_at.is_(None))
            .order_by(Persona.created_at.desc(), Persona.id.desc())
        ).scalars().all()

    @staticmethod
    def get_persona(session, *, company_id: int, persona_id: int, include_deleted: bool = False):
        filters = [Persona.company_id == int(company_id), Persona.id == int(persona_id)]
        if not include_deleted:
            filters.append(Persona.deleted_at.is_(None))
        return session.execute(select(Persona).where(*filters).limit(1)).scalar_one_or_none()

    @staticmethod
    def create_persona(session, *, company_id: int, user_id: int, data: dict):
        persona = Persona(
            company_id=int(company_id),
            team_id=data.get("team_id"),
            created_by_user_id=int(user_id),
            updated_by_user_id=int(user_id),
            folder_id=data.get("folder_id"),
            source_external_id=data.get("source_external_id"),
            name=data["name"],
            gender=data.get("gender"),
            title=data.get("title"),
            personality=data.get("personality"),
            language=data.get("language") or "ko",
            source_type=data.get("source_type") or "manual",
            source_data=data.get("source_data"),
            image_asset_id=data.get("image_asset_id"),
            image_url=data.get("image_url"),
            image_data=data.get("image_data"),
            image_mime_type=data.get("image_mime_type"),
            image_prompt=data.get("image_prompt"),
            schema_version=int(data.get("schema_version") or 3),
            locale=data.get("locale"),
            age=data.get("age"),
            attitudes=data.get("attitudes"),
            biography=data.get("biography"),
            demeanour=data.get("demeanour"),
            ethnicity=data.get("ethnicity"),
            interests=data.get("interests"),
            generation=data.get("generation"),
            motivation=data.get("motivation"),
            upbringing=data.get("upbringing"),
            quote=data.get("quote"),
            additional_info=data.get("additional_info"),
            behaviours=data.get("behaviours"),
            cultural_background=data.get("cultural_background"),
            current_city=data.get("current_city"),
            current_country=data.get("current_country"),
            income=data.get("income"),
            locations=data.get("locations"),
            organisation=data.get("organisation"),
            preferences=data.get("preferences"),
            role_area=data.get("role_area"),
            role_level=data.get("role_level"),
            sector=data.get("sector"),
            social_context=data.get("social_context"),
            telecom_usage=data.get("telecom_usage"),
            telecom_values=data.get("telecom_values"),
            ux_interaction=data.get("ux_interaction"),
            telecom_behavior_dimensions=data.get("telecom_behavior_dimensions"),
            profile=data.get("profile"),
            telecom_profile=data.get("telecom_profile"),
            generation_metadata=data.get("generation_metadata"),
        )
        session.add(persona)
        session.flush()
        settings = PersonaMemorySettings(persona_id=persona.id, company_id=int(company_id))
        session.add(settings)
        session.flush()
        return persona

    @staticmethod
    def update_persona(session, persona, *, user_id: int, data: dict):
        allowed = {
            "folder_id",
            "name",
            "gender",
            "title",
            "personality",
            "language",
            "source_data",
            "image_url",
            "image_data",
            "image_mime_type",
            "image_prompt",
            "locale",
            "age",
            "attitudes",
            "biography",
            "demeanour",
            "ethnicity",
            "interests",
            "generation",
            "motivation",
            "upbringing",
            "quote",
            "additional_info",
            "behaviours",
            "cultural_background",
            "current_city",
            "current_country",
            "income",
            "locations",
            "organisation",
            "preferences",
            "role_area",
            "role_level",
            "sector",
            "social_context",
            "telecom_usage",
            "telecom_values",
            "ux_interaction",
            "telecom_behavior_dimensions",
            "profile",
            "telecom_profile",
            "generation_metadata",
        }
        for key in allowed:
            if key in data:
                setattr(persona, key, data[key])
        persona.updated_by_user_id = int(user_id)
        persona.updated_at = utcnow()
        session.flush()
        return persona

    @staticmethod
    def soft_delete_persona(session, persona, *, user_id: int):
        persona.deleted_at = utcnow()
        persona.updated_by_user_id = int(user_id)
        persona.updated_at = utcnow()
        session.flush()

    @staticmethod
    def get_memory_settings(session, *, company_id: int, persona_id: int):
        return session.execute(
            select(PersonaMemorySettings)
            .where(
                PersonaMemorySettings.company_id == int(company_id),
                PersonaMemorySettings.persona_id == int(persona_id),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def upsert_memory_settings(session, *, company_id: int, persona_id: int, data: dict):
        settings = PersonaRepository.get_memory_settings(session, company_id=company_id, persona_id=persona_id)
        if settings is None:
            settings = PersonaMemorySettings(persona_id=int(persona_id), company_id=int(company_id))
            session.add(settings)
        for key in ("enable_memory", "memory_strength", "apply_to_chat", "apply_to_tests"):
            if key in data:
                setattr(settings, key, data[key])
        settings.updated_at = utcnow()
        session.flush()
        return settings

    @staticmethod
    def list_activities(session, *, company_id: int, persona_id: int):
        return session.execute(
            select(PersonaActivity)
            .where(PersonaActivity.company_id == int(company_id), PersonaActivity.persona_id == int(persona_id))
            .order_by(PersonaActivity.created_at.desc(), PersonaActivity.id.desc())
        ).scalars().all()

    @staticmethod
    def create_activity(session, *, company_id: int, persona_id: int, data: dict):
        activity = PersonaActivity(
            company_id=int(company_id),
            persona_id=int(persona_id),
            activity_type=data["activity_type"],
            activity_id=data.get("activity_id"),
            summary=data.get("summary"),
            was_validated=bool(data.get("was_validated", False)),
            was_correct=data.get("was_correct"),
            metadata_=data.get("metadata"),
        )
        session.add(activity)
        session.flush()
        return activity

    @staticmethod
    def list_traits(session, *, company_id: int, persona_id: int):
        return session.execute(
            select(PersonaLearnedTrait)
            .where(
                PersonaLearnedTrait.company_id == int(company_id),
                PersonaLearnedTrait.persona_id == int(persona_id),
                PersonaLearnedTrait.is_active.is_(True),
            )
            .order_by(PersonaLearnedTrait.category.asc(), PersonaLearnedTrait.id.asc())
        ).scalars().all()

    @staticmethod
    def create_trait(session, *, company_id: int, persona_id: int, data: dict):
        trait = PersonaLearnedTrait(
            company_id=int(company_id),
            persona_id=int(persona_id),
            trait=data["trait"],
            category=data.get("category") or "general",
            confidence=float(data.get("confidence") or 0),
            source_count=int(data.get("source_count") or 1),
            sources=data.get("sources"),
        )
        session.add(trait)
        session.flush()
        return trait

    @staticmethod
    def get_trait(session, *, company_id: int, persona_id: int, trait_id: int):
        return session.execute(
            select(PersonaLearnedTrait)
            .where(
                PersonaLearnedTrait.company_id == int(company_id),
                PersonaLearnedTrait.persona_id == int(persona_id),
                PersonaLearnedTrait.id == int(trait_id),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def deactivate_trait(session, trait):
        trait.is_active = False
        trait.updated_at = utcnow()
        session.flush()

    @staticmethod
    def create_asset(session, *, company_id: int, user_id: int, data: dict):
        asset = PersonaAsset(
            company_id=int(company_id),
            team_id=data.get("team_id"),
            created_by_user_id=int(user_id),
            updated_by_user_id=int(user_id),
            asset_type=data.get("asset_type") or "upload",
            storage_backend=data.get("storage_backend") or "local",
            storage_key=data["storage_key"],
            original_filename=data.get("original_filename"),
            mime_type=data.get("mime_type"),
            byte_size=data.get("byte_size"),
            metadata_=data.get("metadata"),
            data=data.get("data"),
        )
        session.add(asset)
        session.flush()
        return asset

    @staticmethod
    def get_asset(session, *, company_id: int, asset_id: int):
        return session.execute(
            select(PersonaAsset)
            .where(
                PersonaAsset.company_id == int(company_id),
                PersonaAsset.id == int(asset_id),
                PersonaAsset.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def attach_persona_image(session, persona, *, user_id: int, asset_id: Optional[int], image_url: Optional[str], image_prompt: Optional[str]):
        persona.image_asset_id = asset_id
        persona.image_url = image_url
        persona.image_prompt = image_prompt
        persona.updated_by_user_id = int(user_id)
        persona.updated_at = utcnow()
        session.flush()
        return persona

    @staticmethod
    def list_ui_tests(session, *, company_id: int):
        return session.execute(
            select(PersonaUITest)
            .where(PersonaUITest.company_id == int(company_id), PersonaUITest.deleted_at.is_(None))
            .order_by(PersonaUITest.created_at.desc(), PersonaUITest.id.desc())
        ).scalars().all()

    @staticmethod
    def get_ui_test(session, *, company_id: int, test_id: int):
        return session.execute(
            select(PersonaUITest)
            .where(
                PersonaUITest.company_id == int(company_id),
                PersonaUITest.id == int(test_id),
                PersonaUITest.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_ui_test(session, *, company_id: int, user_id: int, data: dict):
        test = PersonaUITest(
            company_id=int(company_id),
            team_id=data.get("team_id"),
            created_by_user_id=int(user_id),
            updated_by_user_id=int(user_id),
            name=data["name"],
            description=data.get("description"),
            device_type=data.get("device_type") or "pc",
            validation_type=data.get("validation_type") or "single",
            scope_type=data.get("scope_type") or "screen",
            source_type=data["source_type"],
            status=data.get("status") or "draft",
            persona_count=data.get("persona_count"),
            screen_count=int(data.get("screen_count") or 0),
            source_data=data.get("source_data"),
            summary=data.get("summary"),
        )
        session.add(test)
        session.flush()
        return test

    @staticmethod
    def update_ui_test(session, test, *, user_id: int, data: dict):
        for key in (
            "name",
            "description",
            "device_type",
            "validation_type",
            "scope_type",
            "source_type",
            "status",
            "progress",
            "error_message",
            "persona_count",
            "screen_count",
            "source_data",
            "summary",
            "started_at",
            "completed_at",
        ):
            if key in data:
                setattr(test, key, data[key])
        test.updated_by_user_id = int(user_id)
        test.updated_at = utcnow()
        session.flush()
        return test

    @staticmethod
    def soft_delete_ui_test(session, test, *, user_id: int):
        test.deleted_at = utcnow()
        test.updated_by_user_id = int(user_id)
        test.updated_at = utcnow()
        session.flush()

    @staticmethod
    def list_ui_test_results(session, *, company_id: int, test_id: int):
        return session.execute(
            select(PersonaUITestResult)
            .where(PersonaUITestResult.company_id == int(company_id), PersonaUITestResult.test_id == int(test_id))
            .order_by(PersonaUITestResult.created_at.desc(), PersonaUITestResult.id.desc())
        ).scalars().all()

    @staticmethod
    def create_ui_test_result(session, *, company_id: int, test_id: int, persona_id: Optional[int], data: dict):
        result = PersonaUITestResult(
            company_id=int(company_id),
            test_id=int(test_id),
            persona_id=int(persona_id) if persona_id is not None else None,
            status=data.get("status") or "completed",
            screen_index=data.get("screen_index"),
            choice=data.get("choice"),
            summary=data.get("summary"),
            persona_goal_fit=data.get("persona_goal_fit"),
            scores=data.get("scores"),
            feedback=data.get("feedback"),
            pin_comments=data.get("pin_comments"),
            flow_analysis=data.get("flow_analysis"),
            persona_snapshot=data.get("persona_snapshot"),
            confidence=data.get("confidence"),
            evidence_ids=data.get("evidence_ids"),
            strengths=data.get("strengths"),
            risks=data.get("risks"),
            recommendations=data.get("recommendations"),
            screen_insights=data.get("screen_insights"),
            evidence=data.get("evidence"),
            raw_response=data.get("raw_response"),
            error_message=data.get("error_message"),
        )
        session.add(result)
        session.flush()
        return result

    @staticmethod
    def delete_ui_test_results(session, *, company_id: int, test_id: int):
        session.query(PersonaUITestResult).filter(
            PersonaUITestResult.company_id == int(company_id),
            PersonaUITestResult.test_id == int(test_id),
        ).delete(synchronize_session=False)
        session.query(PersonaActivity).filter(
            PersonaActivity.company_id == int(company_id),
            PersonaActivity.activity_type == "ui_test",
            PersonaActivity.activity_id == str(test_id),
        ).delete(synchronize_session=False)
        session.flush()

    @staticmethod
    def list_ab_tests(session, *, company_id: int):
        return session.execute(
            select(PersonaABTest)
            .where(PersonaABTest.company_id == int(company_id), PersonaABTest.deleted_at.is_(None))
            .order_by(PersonaABTest.created_at.desc(), PersonaABTest.id.desc())
        ).scalars().all()

    @staticmethod
    def get_ab_test(session, *, company_id: int, ab_test_id: int):
        return session.execute(
            select(PersonaABTest)
            .where(
                PersonaABTest.company_id == int(company_id),
                PersonaABTest.id == int(ab_test_id),
                PersonaABTest.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_ab_test(session, *, company_id: int, user_id: int, data: dict):
        test = PersonaABTest(
            company_id=int(company_id),
            team_id=data.get("team_id"),
            created_by_user_id=int(user_id),
            updated_by_user_id=int(user_id),
            name=data["name"],
            purpose=data.get("purpose"),
            service_context=data.get("service_context"),
            mode=data.get("mode") or "single",
            screens=data.get("screens"),
            transitions=data.get("transitions"),
            context_data=data.get("context_data"),
            summary=data.get("summary"),
            status=data.get("status") or "draft",
            enable_consistency_validation=bool(data.get("enable_consistency_validation", False)),
            consistency_run_count=int(data.get("consistency_run_count") or 3),
        )
        session.add(test)
        session.flush()
        return test

    @staticmethod
    def update_ab_test(session, test, *, user_id: int, data: dict):
        for key in (
            "name",
            "purpose",
            "service_context",
            "mode",
            "screens",
            "transitions",
            "context_data",
            "summary",
            "status",
            "progress",
            "error_message",
            "enable_consistency_validation",
            "consistency_run_count",
        ):
            if key in data:
                setattr(test, key, data[key])
        test.updated_by_user_id = int(user_id)
        test.updated_at = utcnow()
        session.flush()
        return test

    @staticmethod
    def soft_delete_ab_test(session, test, *, user_id: int):
        test.deleted_at = utcnow()
        test.updated_by_user_id = int(user_id)
        test.updated_at = utcnow()
        session.flush()

    @staticmethod
    def list_ab_test_results(session, *, company_id: int, ab_test_id: int):
        return session.execute(
            select(PersonaABTestResult)
            .where(PersonaABTestResult.company_id == int(company_id), PersonaABTestResult.ab_test_id == int(ab_test_id))
            .order_by(PersonaABTestResult.created_at.asc(), PersonaABTestResult.id.asc())
        ).scalars().all()

    @staticmethod
    def create_ab_test_result(session, *, company_id: int, ab_test_id: int, persona_id: Optional[int], data: dict):
        result = PersonaABTestResult(
            company_id=int(company_id),
            ab_test_id=int(ab_test_id),
            persona_id=int(persona_id) if persona_id is not None else None,
            status=data.get("status") or "completed",
            persona_snapshot=data.get("persona_snapshot"),
            scores=data.get("scores"),
            feedback=data.get("feedback"),
            confidence=data.get("confidence"),
            evidence_ids=data.get("evidence_ids"),
            raw_response=data.get("raw_response"),
            error_message=data.get("error_message"),
        )
        session.add(result)
        session.flush()
        return result

    @staticmethod
    def delete_ab_test_results(session, *, company_id: int, ab_test_id: int):
        session.query(PersonaABTestResult).filter(
            PersonaABTestResult.company_id == int(company_id),
            PersonaABTestResult.ab_test_id == int(ab_test_id),
        ).delete(synchronize_session=False)
        session.flush()

    @staticmethod
    def list_interviews(session, *, company_id: int):
        return session.execute(
            select(PersonaInterview)
            .where(PersonaInterview.company_id == int(company_id), PersonaInterview.deleted_at.is_(None))
            .order_by(PersonaInterview.created_at.desc(), PersonaInterview.id.desc())
        ).scalars().all()

    @staticmethod
    def get_interview(session, *, company_id: int, interview_id: int):
        return session.execute(
            select(PersonaInterview)
            .where(
                PersonaInterview.company_id == int(company_id),
                PersonaInterview.id == int(interview_id),
                PersonaInterview.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_interview(session, *, company_id: int, user_id: int, data: dict):
        interview = PersonaInterview(
            company_id=int(company_id),
            team_id=data.get("team_id"),
            created_by_user_id=int(user_id),
            updated_by_user_id=int(user_id),
            name=data["name"],
            goal=data["goal"],
            product_description=data.get("product_description"),
            length=data.get("length") or "quick",
            question_set=data.get("question_set"),
            model=data.get("model"),
            pack_model=data.get("pack_model"),
            status=data.get("status") or "draft",
            persona_ids=data.get("persona_ids") or [],
            summary=data.get("summary"),
        )
        session.add(interview)
        session.flush()
        return interview

    @staticmethod
    def update_interview(session, interview, *, user_id: int, data: dict):
        for key in (
            "name",
            "goal",
            "product_description",
            "length",
            "question_set",
            "model",
            "pack_model",
            "status",
            "progress",
            "persona_ids",
            "summary",
            "error_message",
            "started_at",
            "completed_at",
        ):
            if key in data:
                setattr(interview, key, data[key])
        interview.updated_by_user_id = int(user_id)
        interview.updated_at = utcnow()
        session.flush()
        return interview

    @staticmethod
    def soft_delete_interview(session, interview, *, user_id: int):
        interview.deleted_at = utcnow()
        interview.updated_by_user_id = int(user_id)
        interview.updated_at = utcnow()
        session.flush()

    @staticmethod
    def list_interview_results(session, *, company_id: int, interview_id: int):
        return session.execute(
            select(PersonaInterviewResult)
            .where(PersonaInterviewResult.company_id == int(company_id), PersonaInterviewResult.interview_id == int(interview_id))
            .order_by(PersonaInterviewResult.created_at.asc(), PersonaInterviewResult.id.asc())
        ).scalars().all()

    @staticmethod
    def delete_interview_results(session, *, company_id: int, interview_id: int):
        session.query(PersonaInterviewResult).filter(
            PersonaInterviewResult.company_id == int(company_id),
            PersonaInterviewResult.interview_id == int(interview_id),
        ).delete(synchronize_session=False)
        session.flush()

    @staticmethod
    def create_interview_result(session, *, company_id: int, interview_id: int, persona_id: Optional[int], data: dict):
        result = PersonaInterviewResult(
            company_id=int(company_id),
            interview_id=int(interview_id),
            persona_id=int(persona_id) if persona_id is not None else None,
            status=data.get("status") or "completed",
            persona_snapshot=data.get("persona_snapshot"),
            summary=data.get("summary"),
            turns=data.get("turns"),
            pack=data.get("pack"),
            raw_response=data.get("raw_response"),
            error_message=data.get("error_message"),
        )
        session.add(result)
        session.flush()
        return result

    @staticmethod
    def get_figma_account(session, *, company_id: int, user_id: int):
        return session.execute(
            select(PersonaFigmaAccount)
            .where(
                PersonaFigmaAccount.company_id == int(company_id),
                PersonaFigmaAccount.created_by_user_id == int(user_id),
                PersonaFigmaAccount.deleted_at.is_(None),
            )
            .order_by(PersonaFigmaAccount.updated_at.desc(), PersonaFigmaAccount.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def upsert_figma_account(session, *, company_id: int, user_id: int, data: dict):
        account = session.execute(
            select(PersonaFigmaAccount)
            .where(
                PersonaFigmaAccount.company_id == int(company_id),
                PersonaFigmaAccount.created_by_user_id == int(user_id),
                PersonaFigmaAccount.figma_user_id == data["figma_user_id"],
            )
            .limit(1)
        ).scalar_one_or_none()
        if account is None:
            account = PersonaFigmaAccount(
                company_id=int(company_id),
                created_by_user_id=int(user_id),
                figma_user_id=data["figma_user_id"],
                access_token_encrypted=data["access_token_encrypted"],
            )
            session.add(account)
        for key in (
            "figma_email",
            "figma_handle",
            "access_token_encrypted",
            "refresh_token_encrypted",
            "scope",
            "expires_at",
            "figma_avatar_url",
        ):
            if key in data:
                setattr(account, key, data[key])
        account.deleted_at = None
        account.updated_at = utcnow()
        session.flush()
        return account

    @staticmethod
    def disconnect_figma_account(session, account):
        account.deleted_at = utcnow()
        account.updated_at = utcnow()
        session.flush()

    @staticmethod
    def list_figma_files(session, *, company_id: int, account_id: Optional[int] = None):
        filters = [PersonaFigmaFile.company_id == int(company_id)]
        if account_id is not None:
            filters.append(PersonaFigmaFile.figma_account_id == int(account_id))
        return session.execute(
            select(PersonaFigmaFile)
            .where(*filters)
            .order_by(PersonaFigmaFile.updated_at.desc(), PersonaFigmaFile.id.desc())
        ).scalars().all()

    @staticmethod
    def upsert_figma_file(session, *, company_id: int, account_id: int, data: dict):
        figma_file = session.execute(
            select(PersonaFigmaFile)
            .where(
                PersonaFigmaFile.company_id == int(company_id),
                PersonaFigmaFile.figma_file_key == data["figma_file_key"],
            )
            .limit(1)
        ).scalar_one_or_none()
        if figma_file is None:
            figma_file = PersonaFigmaFile(
                company_id=int(company_id),
                figma_account_id=int(account_id),
                figma_file_key=data["figma_file_key"],
                figma_file_name=data.get("figma_file_name") or data["figma_file_key"],
            )
            session.add(figma_file)
        for key in ("figma_account_id", "figma_file_name", "figma_file_link", "thumbnail_url", "last_synced_at", "sync_status", "sync_error"):
            if key in data:
                setattr(figma_file, key, data[key])
        figma_file.updated_at = utcnow()
        session.flush()
        return figma_file

    @staticmethod
    def get_figma_file(session, *, company_id: int, file_id: int):
        return session.execute(
            select(PersonaFigmaFile)
            .where(PersonaFigmaFile.company_id == int(company_id), PersonaFigmaFile.id == int(file_id))
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def list_figma_flows(session, *, company_id: int, file_id: int):
        return session.execute(
            select(PersonaFigmaFlow)
            .where(
                PersonaFigmaFlow.company_id == int(company_id),
                PersonaFigmaFlow.figma_file_id == int(file_id),
                PersonaFigmaFlow.active.is_(True),
            )
            .order_by(PersonaFigmaFlow.created_at.asc(), PersonaFigmaFlow.id.asc())
        ).scalars().all()

    @staticmethod
    def replace_figma_flows(session, *, company_id: int, file_id: int, flows: Iterable[dict]):
        session.query(PersonaFigmaFlow).filter(
            PersonaFigmaFlow.company_id == int(company_id),
            PersonaFigmaFlow.figma_file_id == int(file_id),
        ).update({PersonaFigmaFlow.active: False, PersonaFigmaFlow.updated_at: utcnow()})
        created = []
        for flow_data in flows:
            flow = PersonaFigmaFlow(
                company_id=int(company_id),
                figma_file_id=int(file_id),
                figma_page_id=flow_data.get("figma_page_id"),
                figma_page_name=flow_data.get("figma_page_name"),
                figma_start_node_id=flow_data["figma_start_node_id"],
                figma_flow_name=flow_data.get("figma_flow_name") or flow_data["figma_start_node_id"],
                metadata_=flow_data.get("metadata"),
            )
            session.add(flow)
            created.append(flow)
        session.flush()
        return created
