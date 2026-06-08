from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from config import Config


@dataclass(frozen=True)
class PersonaAssetContent:
    bytes: bytes
    mime_type: str | None
    source_backend: str


class PersonaStorage:
    def __init__(self, *, local_dir: str | None = None):
        self.local_dir = Path(local_dir or Config.PERSONA_STORAGE_LOCAL_DIR)
        self.backend = (Config.PERSONA_STORAGE_BACKEND or "local").strip().lower()
        self.s3_bucket = Config.PERSONA_S3_BUCKET
        self.s3_prefix = (Config.PERSONA_S3_PREFIX or "").strip("/")
        self.s3_region = Config.PERSONA_S3_REGION
        self.s3_endpoint_url = Config.PERSONA_S3_ENDPOINT_URL
        self.s3_local_fallback = Config.PERSONA_S3_LOCAL_FALLBACK
        self._s3_client = None

    def save_upload(self, file: FileStorage, *, company_id: int, asset_type: str = "upload") -> dict:
        original_filename = secure_filename(file.filename or "upload")
        suffix = Path(original_filename).suffix
        storage_key = f"company-{int(company_id)}/{asset_type}/{uuid.uuid4().hex}{suffix}"
        if self._should_use_s3():
            image_bytes = file.read()
            try:
                file.seek(0)
            except Exception:
                pass
            self._put_s3_object(storage_key, image_bytes, file.mimetype)
            return {
                "asset_type": asset_type,
                "storage_backend": "s3",
                "storage_key": storage_key,
                "original_filename": original_filename,
                "mime_type": file.mimetype,
                "byte_size": len(image_bytes),
            }
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
        if self._should_use_s3():
            self._put_s3_object(storage_key, image_bytes, mime_type)
            return {
                "asset_type": asset_type,
                "storage_backend": "s3",
                "storage_key": storage_key,
                "original_filename": filename,
                "mime_type": mime_type,
                "byte_size": len(image_bytes),
            }
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

    def read_asset_bytes(self, asset) -> PersonaAssetContent:
        backend = (getattr(asset, "storage_backend", None) or "local").strip().lower()
        storage_key = getattr(asset, "storage_key")
        if backend == "s3":
            try:
                response = self._s3_client_or_raise().get_object(
                    Bucket=self.s3_bucket,
                    Key=self._s3_key(storage_key),
                )
                body = response["Body"].read()
                return PersonaAssetContent(
                    bytes=body,
                    mime_type=response.get("ContentType") or getattr(asset, "mime_type", None),
                    source_backend="s3",
                )
            except Exception as exc:
                if not self.s3_local_fallback:
                    raise
                path = self.resolve_local_path(storage_key)
                if not path.exists() or not path.is_file():
                    raise FileNotFoundError(str(path)) from exc
                return PersonaAssetContent(
                    bytes=path.read_bytes(),
                    mime_type=getattr(asset, "mime_type", None),
                    source_backend="local_fallback",
                )

        path = self.resolve_local_path(storage_key)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        return PersonaAssetContent(
            bytes=path.read_bytes(),
            mime_type=getattr(asset, "mime_type", None),
            source_backend="local",
        )

    def s3_object_exists(self, storage_key: str) -> bool:
        return self.s3_object_size(storage_key) is not None

    def s3_object_size(self, storage_key: str) -> int | None:
        try:
            response = self._s3_client_or_raise().head_object(Bucket=self.s3_bucket, Key=self._s3_key(storage_key))
            return int(response.get("ContentLength", 0))
        except Exception:
            return None

    def copy_local_file_to_s3(self, storage_key: str, *, mime_type: str | None = None) -> int:
        path = self.resolve_local_path(storage_key)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        image_bytes = path.read_bytes()
        self._put_s3_object(storage_key, image_bytes, mime_type)
        return len(image_bytes)

    def _should_use_s3(self) -> bool:
        if self.backend != "s3":
            return False
        self._s3_client_or_raise()
        return True

    def _s3_key(self, storage_key: str) -> str:
        normalized = storage_key.lstrip("/")
        return f"{self.s3_prefix}/{normalized}" if self.s3_prefix else normalized

    def _put_s3_object(self, storage_key: str, data: bytes, mime_type: str | None):
        extra_args = {"ContentType": mime_type} if mime_type else {}
        self._s3_client_or_raise().put_object(
            Bucket=self.s3_bucket,
            Key=self._s3_key(storage_key),
            Body=data,
            **extra_args,
        )

    def _s3_client_or_raise(self):
        if not self.s3_bucket:
            raise RuntimeError("PERSONA_S3_BUCKET is required when PERSONA_STORAGE_BACKEND=s3")
        if self._s3_client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("boto3 is required for persona S3 storage") from exc
            kwargs = {}
            if self.s3_region:
                kwargs["region_name"] = self.s3_region
            if self.s3_endpoint_url:
                kwargs["endpoint_url"] = self.s3_endpoint_url
            self._s3_client = boto3.client("s3", **kwargs)
        return self._s3_client


persona_storage = PersonaStorage()
