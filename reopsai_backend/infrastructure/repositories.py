"""Repository exports for the layered package.

The first refactor phase keeps the existing SQLAlchemy implementation intact
and exposes it through the new infrastructure namespace.
"""

from db.repositories.workspace_repository import WorkspaceRepository, model_to_dict

__all__ = ["WorkspaceRepository", "model_to_dict"]
