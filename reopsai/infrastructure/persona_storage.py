from __future__ import annotations

import os
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from config import Config


class PersonaStorage:
    def __init__(self, *, local_dir: str | None = None):
        self.local_dir = Path(local_dir or Config.PERSONA_STORAGE_LOCAL_DIR)

    def save_upload(self, file: FileStorage, *, company_id: int, asset_type: str = "upload") -> dict:
        original_filename = secure_filename(file.filename or "upload")
        suffix = Path(original_filename).suffix
        storage_key = f"company-{int(company_id)}/{asset_type}/{uuid.uuid4().hex}{suffix}"
        target = self.local_dir / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        file.save(target)
        byte_size = target.stat().st_size
        return {
            "asset_type": asset_type,
            "storage_backend": "local",
            "storage_key": storage_key,
            "original_filename": original_filename,
            "mime_type": file.mimetype,
            "byte_size": byte_size,
        }

    def save_bytes(self, image_bytes: bytes, *, company_id: int, filename: str, mime_type: str, asset_type: str = "generated_image") -> dict:
        suffix = Path(filename).suffix or ".png"
        storage_key = f"company-{int(company_id)}/{asset_type}/{uuid.uuid4().hex}{suffix}"
        target = self.local_dir / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)
        return {
            "asset_type": asset_type,
            "storage_backend": "local",
            "storage_key": storage_key,
            "original_filename": filename,
            "mime_type": mime_type,
            "byte_size": len(image_bytes),
        }

    def resolve_local_path(self, storage_key: str) -> Path:
        candidate = (self.local_dir / storage_key).resolve()
        root = self.local_dir.resolve()
        if os.path.commonpath([str(candidate), str(root)]) != str(root):
            raise ValueError("Invalid storage key")
        return candidate


persona_storage = PersonaStorage()
