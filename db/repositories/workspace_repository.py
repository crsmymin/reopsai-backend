"""Compatibility wrapper for the workspace repository."""

from reopsai.infrastructure.persistence.repositories.workspace_repository import (
    WorkspaceRepository,
    model_to_dict,
)

__all__ = ["WorkspaceRepository", "model_to_dict"]
