"""
Admin 전용 API 라우트
"""

import traceback
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import and_, func, select, update

from db.engine import session_scope
from db.models.core import Artifact, Project, Study, Team, TeamMember, User, UserFeedback
from routes.auth import get_primary_team_id_for_user, tier_required


admin_bp = Blueprint("admin", __name__)


def log_error(error, context=""):
    """에러 로깅"""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] ❌ 에러 발생: {context}")
    print(f"에러 내용: {str(error)}")
    traceback.print_exc()


def _serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _to_int_or_none(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _ensure_db():
    if session_scope is None:
        return False
    return True


@admin_bp.route("/api/admin/users", methods=["GET"])
@tier_required(["admin"])
def get_all_users_with_tier():
    """모든 사용자 조회 (tier 정보 및 통계 포함) - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            users = db_session.execute(
                select(User).order_by(User.created_at.desc())
            ).scalars().all()

            payload_users = []
            for user in users:
                project_ids = db_session.execute(
                    select(Project.id).where(Project.owner_id == user.id)
                ).scalars().all()
                project_count = len(project_ids)

                if project_ids:
                    study_ids = db_session.execute(
                        select(Study.id).where(Study.project_id.in_(project_ids))
                    ).scalars().all()
                else:
                    study_ids = []

                study_count = len(study_ids)
                plan_count = 0
                guideline_count = 0
                screener_count = 0

                if study_ids:
                    plan_count = (
                        db_session.execute(
                            select(func.count())
                            .select_from(Artifact)
                            .where(
                                and_(
                                    Artifact.study_id.in_(study_ids),
                                    Artifact.artifact_type == "plan",
                                )
                            )
                        ).scalar_one()
                        or 0
                    )
                    guideline_count = (
                        db_session.execute(
                            select(func.count())
                            .select_from(Artifact)
                            .where(
                                and_(
                                    Artifact.study_id.in_(study_ids),
                                    Artifact.artifact_type == "guideline",
                                )
                            )
                        ).scalar_one()
                        or 0
                    )
                    screener_count = (
                        db_session.execute(
                            select(func.count())
                            .select_from(Artifact)
                            .where(
                                and_(
                                    Artifact.study_id.in_(study_ids),
                                    Artifact.artifact_type == "survey",
                                )
                            )
                        ).scalar_one()
                        or 0
                    )

                tier = user.tier or "free"
                enterprise_team_id = None
                enterprise_team_name = None
                if tier == "enterprise":
                    try:
                        team_id = get_primary_team_id_for_user(db_session, user.id)
                        if team_id:
                            team = db_session.execute(
                                select(Team).where(Team.id == int(team_id)).limit(1)
                            ).scalar_one_or_none()
                            if team:
                                enterprise_team_id = team.id
                                enterprise_team_name = team.name
                    except Exception as exc:
                        log_error(exc, f"Admin - 엔터프라이즈 팀 정보 조회 실패 (user_id: {user.id})")

                payload_users.append(
                    {
                        "id": user.id,
                        "email": user.email,
                        "tier": tier,
                        "created_at": _serialize_dt(user.created_at),
                        "google_id": user.google_id,
                        "project_count": project_count,
                        "study_count": study_count,
                        "plan_count": int(plan_count),
                        "guideline_count": int(guideline_count),
                        "screener_count": int(screener_count),
                        "enterprise_team_id": enterprise_team_id,
                        "enterprise_team_name": enterprise_team_name,
                    }
                )

        return jsonify({"success": True, "users": payload_users, "count": len(payload_users)})
    except Exception as e:
        log_error(e, "Admin - 사용자 목록 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/tier", methods=["PUT"])
@tier_required(["admin"])
def update_user_tier(user_id):
    """사용자 tier 변경 - admin 전용"""
    try:
        data = request.json or {}
        new_tier = data.get("tier")

        valid_tiers = ["free", "basic", "premium", "enterprise", "admin"]
        if new_tier not in valid_tiers:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 tier입니다. 가능한 값: {valid_tiers}",
                }
            ), 400

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404
            user.tier = new_tier

            payload = {
                "id": user.id,
                "email": user.email,
                "tier": user.tier,
                "created_at": _serialize_dt(user.created_at),
                "google_id": user.google_id,
            }

        return jsonify(
            {
                "success": True,
                "message": f"사용자 tier가 {new_tier}로 변경되었습니다.",
                "user": payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 사용자 tier 변경 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise", methods=["GET"])
@tier_required(["admin"])
def get_user_enterprise_info(user_id):
    """
    특정 사용자의 엔터프라이즈/B2B 관련 정보 조회 (admin 전용)
    - tier
    - 대표 team_id
    - owner로 있는 팀 목록
    - member로 속한 팀 목록
    """
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            tier = user.tier or "free"
            primary_team_id = None
            try:
                primary_team_id = get_primary_team_id_for_user(db_session, user_id_int)
            except Exception as exc:
                log_error(exc, "Admin - 대표 팀 ID 조회 실패")

            owner_teams = db_session.execute(
                select(Team).where(Team.owner_id == user_id_int)
            ).scalars().all()
            owner_teams_payload = [
                {
                    "id": team.id,
                    "name": team.name,
                    "description": team.description,
                    "status": team.status,
                    "created_at": _serialize_dt(team.created_at),
                    "updated_at": _serialize_dt(team.updated_at),
                }
                for team in owner_teams
            ]

            member_rows = db_session.execute(
                select(TeamMember).where(TeamMember.user_id == user_id_int)
            ).scalars().all()
            member_team_ids = list({row.team_id for row in member_rows if row.team_id})

            teams_by_id = {}
            if member_team_ids:
                member_teams = db_session.execute(
                    select(Team).where(Team.id.in_(member_team_ids))
                ).scalars().all()
                teams_by_id = {t.id: t for t in member_teams}

            member_teams_payload = []
            for row in member_rows:
                team_obj = teams_by_id.get(row.team_id)
                member_teams_payload.append(
                    {
                        "team_id": row.team_id,
                        "role": row.role or "member",
                        "team": {
                            "id": team_obj.id if team_obj else row.team_id,
                            "name": team_obj.name if team_obj else None,
                            "description": team_obj.description if team_obj else None,
                            "status": team_obj.status if team_obj else None,
                            "created_at": _serialize_dt(team_obj.created_at) if team_obj else None,
                            "updated_at": _serialize_dt(team_obj.updated_at) if team_obj else None,
                        },
                    }
                )

            user_payload = {
                "id": user.id,
                "email": user.email,
                "tier": tier,
                "created_at": _serialize_dt(user.created_at),
            }

        return jsonify(
            {
                "success": True,
                "user": user_payload,
                "tier": tier,
                "primary_team_id": primary_team_id,
                "owner_teams": owner_teams_payload,
                "member_teams": member_teams_payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 사용자 엔터프라이즈 정보 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/enterprise/init-team", methods=["POST"])
@tier_required(["admin"])
def init_enterprise_team_for_user(user_id):
    """
    Admin이 특정 사용자를 엔터프라이즈 장으로 지정하면서
    - 해당 사용자의 tier를 'enterprise'로 올리고
    - teams 테이블에 새 팀을 생성하고 owner로 등록
    - team_members에도 owner로 추가
    """
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        data = request.json or {}
        team_name = data.get("team_name")
        team_description = data.get("description") or ""

        with session_scope() as db_session:
            user = db_session.execute(
                select(User).where(User.id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "사용자를 찾을 수 없습니다."}), 404

            existing_owner_team = db_session.execute(
                select(Team).where(Team.owner_id == user_id_int).limit(1)
            ).scalar_one_or_none()
            if existing_owner_team:
                return jsonify(
                    {
                        "success": True,
                        "message": "이미 대표 팀이 존재합니다.",
                        "user": {
                            "id": user.id,
                            "email": user.email,
                            "tier": user.tier or "free",
                            "created_at": _serialize_dt(user.created_at),
                        },
                        "team": {
                            "id": existing_owner_team.id,
                            "name": existing_owner_team.name,
                        },
                    }
                )

            if not team_name:
                email = user.email or ""
                if "@" in email:
                    domain_part = email.split("@", 1)[1].split(".")[0]
                    team_name = f"{domain_part} 팀"
                else:
                    team_name = f"Enterprise 팀 ({str(user_id)[:8]})"

            try:
                user.tier = "enterprise"
            except Exception as exc:
                log_error(exc, "Admin - 사용자 tier enterprise 업데이트 실패")

            team = Team(
                name=team_name,
                description=team_description,
                owner_id=user_id_int,
                status="active",
            )
            db_session.add(team)
            db_session.flush()
            db_session.refresh(team)

            member_exists = db_session.execute(
                select(TeamMember.id)
                .where(
                    and_(
                        TeamMember.team_id == team.id,
                        TeamMember.user_id == user_id_int,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if not member_exists:
                db_session.add(TeamMember(team_id=team.id, user_id=user_id_int, role="owner"))

            user_payload = {
                "id": user.id,
                "email": user.email,
                "tier": user.tier or "enterprise",
                "created_at": _serialize_dt(user.created_at),
            }
            team_payload = {
                "id": team.id,
                "name": team.name,
                "description": team.description,
                "owner_id": team.owner_id,
                "status": team.status,
                "created_at": _serialize_dt(team.created_at),
                "updated_at": _serialize_dt(team.updated_at),
            }

        return jsonify(
            {
                "success": True,
                "message": "엔터프라이즈 팀이 생성되고 사용자가 오너로 등록되었습니다.",
                "user": user_payload,
                "team": team_payload,
            }
        )
    except Exception as e:
        log_error(e, f"Admin - 엔터프라이즈 팀 생성 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/stats", methods=["GET"])
@tier_required(["admin"])
def get_admin_stats():
    """관리자 대시보드 통계 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            user_rows = db_session.execute(select(User.id, User.tier)).all()
            total_users = len(user_rows)
            tier_counts = {}
            for _uid, tier in user_rows:
                t = tier or "free"
                tier_counts[t] = tier_counts.get(t, 0) + 1

            total_projects = db_session.execute(
                select(func.count()).select_from(Project)
            ).scalar_one() or 0
            total_studies = db_session.execute(
                select(func.count()).select_from(Study)
            ).scalar_one() or 0

        return jsonify(
            {
                "success": True,
                "stats": {
                    "total_users": int(total_users),
                    "tier_counts": tier_counts,
                    "total_projects": int(total_projects),
                    "total_studies": int(total_studies),
                },
            }
        )
    except Exception as e:
        log_error(e, "Admin - 통계 조회")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/projects", methods=["GET"])
@tier_required(["admin"])
def get_user_projects(user_id):
    """특정 사용자의 프로젝트 목록 조회 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            projects = db_session.execute(
                select(Project)
                .where(Project.owner_id == user_id_int)
                .order_by(Project.created_at.desc())
            ).scalars().all()

        payload = [
            {
                "id": p.id,
                "owner_id": p.owner_id,
                "name": p.name,
                "slug": p.slug,
                "product_url": p.product_url,
                "keywords": p.keywords,
                "created_at": _serialize_dt(p.created_at),
                "updated_at": _serialize_dt(p.updated_at),
            }
            for p in projects
        ]
        return jsonify({"success": True, "projects": payload, "count": len(payload)})
    except Exception as e:
        log_error(e, f"Admin - 사용자 프로젝트 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/users/<user_id>/studies", methods=["GET"])
@tier_required(["admin"])
def get_user_studies(user_id):
    """특정 사용자의 스터디 목록 조회 - admin 전용"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(user_id)
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자 ID입니다."}), 400

        with session_scope() as db_session:
            project_ids = db_session.execute(
                select(Project.id).where(Project.owner_id == user_id_int)
            ).scalars().all()
            if not project_ids:
                return jsonify({"success": True, "studies": [], "count": 0})

            rows = db_session.execute(
                select(Study, Project.name)
                .join(Project, Project.id == Study.project_id)
                .where(Study.project_id.in_(project_ids))
                .order_by(Study.created_at.desc())
            ).all()

        studies = []
        for study, project_name in rows:
            studies.append(
                {
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
                    "created_at": _serialize_dt(study.created_at),
                    "updated_at": _serialize_dt(study.updated_at),
                    "projects": {"name": project_name},
                }
            )

        return jsonify({"success": True, "studies": studies, "count": len(studies)})
    except Exception as e:
        log_error(e, f"Admin - 사용자 스터디 조회 (user_id: {user_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/studies/<int:study_id>", methods=["GET"])
@tier_required(["admin"])
def admin_get_study(study_id):
    """Admin 전용 - Study 조회 (권한 검증 없이)"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            study = db_session.execute(
                select(Study).where(Study.id == study_id).limit(1)
            ).scalar_one_or_none()
            if not study:
                return jsonify({"success": False, "error": "연구를 찾을 수 없습니다."}), 404

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
                "created_at": _serialize_dt(study.created_at),
                "updated_at": _serialize_dt(study.updated_at),
            }
        return jsonify(payload)
    except Exception as e:
        log_error(e, f"Admin - Study 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/studies/<int:study_id>/artifacts", methods=["GET"])
@tier_required(["admin"])
def admin_get_study_artifacts(study_id):
    """Admin 전용 - Study의 Artifacts 조회 (권한 검증 없이)"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        with session_scope() as db_session:
            artifacts = db_session.execute(
                select(Artifact)
                .where(Artifact.study_id == study_id)
                .order_by(Artifact.created_at.desc())
            ).scalars().all()

        payload = [
            {
                "id": a.id,
                "study_id": a.study_id,
                "owner_id": a.owner_id,
                "artifact_type": a.artifact_type,
                "content": a.content,
                "status": a.status,
                "created_at": _serialize_dt(a.created_at),
                "updated_at": _serialize_dt(a.updated_at),
            }
            for a in artifacts
        ]
        return jsonify({"success": True, "artifacts": payload})
    except Exception as e:
        log_error(e, f"Admin - Study Artifacts 조회 (study_id: {study_id})")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/feedback", methods=["POST"])
@jwt_required()
def submit_feedback():
    """피드백 저장 - 로그인한 사용자만 사용 가능"""
    try:
        data = request.json or {}
        category = data.get("category")
        vote = data.get("vote")
        comment = data.get("comment", "")

        valid_categories = ["plan", "screener", "guide", "participants"]
        if not category or category not in valid_categories:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 category입니다. 가능한 값: {valid_categories}",
                }
            ), 400
        if vote is None:
            return jsonify({"success": False, "error": "vote 값이 필요합니다. (true 또는 false)"}), 400

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id = get_jwt_identity()
        user_id_int = _to_int_or_none(user_id)
        study_id = data.get("study_id")
        study_name = data.get("study_name", "")
        vote_str = "true" if bool(vote) else "false"

        with session_scope() as db_session:
            feedback = UserFeedback(
                category=category,
                vote=vote_str,
                comment=comment if comment else None,
                user_id=user_id_int,
                study_id=int(study_id) if study_id else None,
                study_name=study_name if study_name else None,
            )
            db_session.add(feedback)
            db_session.flush()
            db_session.refresh(feedback)

            feedback_payload = {
                "id": feedback.id,
                "category": feedback.category,
                "vote": feedback.vote,
                "comment": feedback.comment,
                "user_id": feedback.user_id,
                "study_id": feedback.study_id,
                "study_name": feedback.study_name,
                "created_at": _serialize_dt(feedback.created_at),
                "updated_at": _serialize_dt(feedback.updated_at),
            }

        return jsonify(
            {
                "success": True,
                "message": "피드백이 저장되었습니다.",
                "feedback": feedback_payload,
            }
        )
    except Exception as e:
        log_error(e, "피드백 저장")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/feedback/<int:feedback_id>", methods=["PATCH"])
@jwt_required()
def update_feedback_comment(feedback_id):
    """피드백 코멘트만 업데이트"""
    try:
        data = request.json or {}
        comment = data.get("comment", "")

        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        user_id_int = _to_int_or_none(get_jwt_identity())
        if user_id_int is None:
            return jsonify({"success": False, "error": "유효하지 않은 사용자입니다."}), 401

        with session_scope() as db_session:
            feedback = db_session.execute(
                select(UserFeedback)
                .where(
                    and_(
                        UserFeedback.id == feedback_id,
                        UserFeedback.user_id == user_id_int,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if not feedback:
                return jsonify({"success": False, "error": "피드백을 찾을 수 없거나 권한이 없습니다."}), 404

            feedback.comment = comment if comment else None
            feedback_payload = {
                "id": feedback.id,
                "category": feedback.category,
                "vote": feedback.vote,
                "comment": feedback.comment,
                "user_id": feedback.user_id,
                "study_id": feedback.study_id,
                "study_name": feedback.study_name,
                "created_at": _serialize_dt(feedback.created_at),
                "updated_at": _serialize_dt(feedback.updated_at),
            }

        return jsonify(
            {
                "success": True,
                "message": "코멘트가 업데이트되었습니다.",
                "feedback": feedback_payload,
            }
        )
    except Exception as e:
        log_error(e, f"피드백 {feedback_id} 코멘트 업데이트")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/feedback", methods=["GET"])
@tier_required(["admin"])
def get_feedback():
    """피드백 조회 - admin 전용, category 필터링 지원"""
    try:
        if not _ensure_db():
            return jsonify({"success": False, "error": "데이터베이스 연결이 필요합니다."}), 500

        category = request.args.get("category", None)
        valid_categories = ["plan", "screener", "guide", "participants"]
        if category and category not in valid_categories:
            return jsonify(
                {
                    "success": False,
                    "error": f"유효하지 않은 category입니다. 가능한 값: {valid_categories}",
                }
            ), 400

        with session_scope() as db_session:
            query = select(UserFeedback).order_by(UserFeedback.created_at.desc())
            if category:
                query = query.where(UserFeedback.category == category)
            rows = db_session.execute(query).scalars().all()

        feedback_payload = [
            {
                "id": row.id,
                "category": row.category,
                "vote": row.vote,
                "comment": row.comment,
                "user_id": row.user_id,
                "study_id": row.study_id,
                "study_name": row.study_name,
                "created_at": _serialize_dt(row.created_at),
                "updated_at": _serialize_dt(row.updated_at),
            }
            for row in rows
        ]

        return jsonify(
            {
                "success": True,
                "feedback": feedback_payload,
                "count": len(feedback_payload),
                "category": category if category else "all",
            }
        )
    except Exception as e:
        log_error(e, "Admin - 피드백 조회")
        return jsonify({"success": False, "error": str(e)}), 500
