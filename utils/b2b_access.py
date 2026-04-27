"""
Utilities for B2C/B2B (enterprise team) access control.

Goal: centralize the repeated logic:
- If tier != enterprise: owner_ids = [user_id]
- If tier == enterprise: expand owner_ids to the user's team members
"""

from typing import List, Optional, Tuple

from flask_jwt_extended import get_jwt, get_jwt_identity
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import Team, TeamMember
from routes.auth import get_primary_team_id_for_user


def _to_int_or_raw(value):
    try:
        return int(value) if value is not None else None
    except Exception:
        return value


def get_enterprise_team_id(db_session, claims: dict, token_identity) -> Optional[int]:
    team_id = claims.get("team_id")
    if team_id:
        active_team_id = db_session.execute(
            select(Team.id).where(Team.id == int(team_id), Team.status != "deleted").limit(1)
        ).scalar_one_or_none()
        if active_team_id:
            return active_team_id
    token_user_id = _to_int_or_raw(token_identity)
    if token_user_id:
        try:
            return get_primary_team_id_for_user(db_session, token_user_id)
        except Exception:
            return None
    return None


def get_owner_ids_for_request(user_id_header) -> Tuple[List[str], Optional[int]]:
    """
    Return (owner_ids, team_id).

    - `owner_ids` is a list of user_id strings allowed to access the resource.
    - For enterprise tier, it expands to include team members.
    """
    claims = get_jwt()
    tier = claims.get("tier")
    identity = get_jwt_identity()

    user_id_int = _to_int_or_raw(user_id_header)
    owner_ids = [str(user_id_int)] if user_id_int is not None else []

    if tier != "enterprise":
        return owner_ids, None

    if session_scope is None:
        return owner_ids, None

    with session_scope() as db_session:
        team_id = get_enterprise_team_id(db_session, claims, identity)
        if not team_id:
            return owner_ids, None
        member_ids = db_session.execute(
            select(TeamMember.user_id).where(TeamMember.team_id == int(team_id))
        ).scalars().all()

    token_user_id = _to_int_or_raw(identity)
    if token_user_id and token_user_id not in member_ids:
        member_ids.append(token_user_id)

    owner_ids = [str(uid) for uid in member_ids] if member_ids else owner_ids
    return owner_ids, team_id
