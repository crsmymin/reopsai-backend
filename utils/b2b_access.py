"""
Utilities for B2C/B2B (business company) access control.

Goal: centralize the repeated logic:
- If tier != enterprise: owner_ids = [user_id]
- If account_type == business: expand owner_ids to the user's company members
"""

from typing import List, Optional, Tuple

from flask_jwt_extended import get_jwt, get_jwt_identity
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import CompanyMember


def _to_int_or_raw(value):
    try:
        return int(value) if value is not None else None
    except Exception:
        return value


def get_owner_ids_for_request(user_id_header) -> Tuple[List[str], Optional[int]]:
    """
    Return (owner_ids, company_id).

    - `owner_ids` is a list of user_id strings allowed to access the resource.
    - For business accounts, it expands to include company members.
    """
    claims = get_jwt()
    account_type = claims.get("account_type")
    company_id = claims.get("company_id")
    identity = get_jwt_identity()

    user_id_int = _to_int_or_raw(user_id_header)
    owner_ids = [str(user_id_int)] if user_id_int is not None else []

    if account_type != "business" or not company_id:
        return owner_ids, None

    if session_scope is None:
        return owner_ids, None

    with session_scope() as db_session:
        member_ids = db_session.execute(
            select(CompanyMember.user_id).where(CompanyMember.company_id == int(company_id))
        ).scalars().all()

    token_user_id = _to_int_or_raw(identity)
    if token_user_id and token_user_id not in member_ids:
        member_ids.append(token_user_id)

    owner_ids = [str(uid) for uid in member_ids] if member_ids else owner_ids
    return owner_ids, int(company_id)
