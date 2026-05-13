"""
Workspace API public entrypoint.

Concrete workspace endpoints live in sibling modules. This module keeps the
blueprint and service attributes stable so app registration and tests can keep
using ``reopsai.api.workspace.workspace_bp`` and monkeypatching services here.
"""

from flask import Blueprint

from reopsai.application.workspace_ai_service import workspace_ai_service
from reopsai.application.workspace_service import workspace_service


workspace_bp = Blueprint('workspace', __name__, url_prefix='/api')


def _workspace_service_ready():
    return getattr(workspace_service, "db_ready", lambda: True)()


# Import endpoint groups after the public blueprint/helpers are defined.
import reopsai.api.workspace_ai  # noqa: E402,F401
import reopsai.api.workspace_resources  # noqa: E402,F401


__all__ = [
    "workspace_bp",
    "workspace_service",
    "workspace_ai_service",
]
