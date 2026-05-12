from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import json
import os
import threading
import traceback
import uuid
from typing import Any

from werkzeug.utils import secure_filename

from pii_utils import detect_pii, sanitize_prompt_for_llm


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}
MAX_FILE_SIZE = 10 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@dataclass(frozen=True)
class GeneratorResult:
    status: str
    data: Any = None
    error: str | None = None


class GeneratorService:
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        *,
        upload_folder=UPLOAD_FOLDER,
        allowed_extensions=None,
        max_file_size=MAX_FILE_SIZE,
        gemini_adapter=_DEFAULT_ADAPTER,
    ):
        self.upload_folder = upload_folder
        self.allowed_extensions = allowed_extensions or ALLOWED_EXTENSIONS
        self.max_file_size = max_file_size
        self.gemini_adapter = gemini_adapter
        self.owners_path = os.path.join(upload_folder, "_owners.json")
        self.owners_lock = threading.Lock()
        os.makedirs(upload_folder, exist_ok=True)

    def _get_gemini_adapter(self):
        if self.gemini_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_gemini_service

            self.gemini_adapter = get_gemini_service()
        return self.gemini_adapter

    def allowed_file(self, filename):
        return (
            '.' in filename
            and filename.rsplit('.', 1)[1].lower() in self.allowed_extensions
        )

    def process_upload(self, *, file, user_id) -> GeneratorResult:
        if file is None:
            return GeneratorResult("no_file", error="파일이 없습니다.")
        if file.filename == '':
            return GeneratorResult("empty_filename", error="파일이 선택되지 않았습니다.")
        if not self.allowed_file(file.filename):
            return GeneratorResult("unsupported", error="지원하지 않는 파일 형식입니다. (이미지: JPEG, PNG, GIF, WebP / PDF)")

        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > self.max_file_size:
            return GeneratorResult("too_large", error=f"파일 크기는 {self.max_file_size // (1024*1024)}MB 이하여야 합니다.")

        file_ext = file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4()}.{file_ext}"
        file_path = os.path.join(self.upload_folder, unique_filename)
        file.save(file_path)
        self.record_file_owner(unique_filename, str(user_id))

        file_type = file.content_type
        pii_meta = None
        if file_type.startswith('image/'):
            processed_content = self.process_image_with_vision(file_path, file_type)
        elif file_type == 'application/pdf':
            processed_content, pii_meta = self.process_pdf(file_path)
        else:
            return GeneratorResult("unsupported", error="지원하지 않는 파일 형식입니다.")

        return GeneratorResult(
            "ok",
            {
                'success': True,
                'file_id': unique_filename,
                'file_name': file.filename,
                'file_type': file_type,
                'file_size': file_size,
                'file_url': f"/api/generator/file/{unique_filename}",
                'processed_content': processed_content,
                'pii': pii_meta or self.empty_pii_meta(),
            },
        )

    def process_image_with_vision(self, file_path, file_type):
        try:
            with open(file_path, 'rb') as file_obj:
                image_bytes = file_obj.read()
                image_data = base64.b64encode(image_bytes).decode('utf-8')

            prompt = """이 이미지를 자세히 분석하고, 리서치 컨텍스트에서 유용한 정보를 추출해주세요.
- 이미지에 나타난 내용을 설명해주세요
- 리서치 계획 수립에 도움이 될 만한 인사이트가 있다면 언급해주세요
- 텍스트가 있다면 읽어서 정리해주세요

한국어로 간결하게 답변해주세요."""
            return self._get_gemini_adapter().analyze_image_with_vision(
                image_data=image_data,
                mime_type=file_type,
                prompt=prompt,
            )
        except Exception as exc:
            print(f"이미지 처리 오류: {exc}")
            traceback.print_exc()
            raise

    def process_pdf(self, file_path):
        try:
            import PyPDF2

            text_content = []
            with open(file_path, 'rb') as file_obj:
                pdf_reader = PyPDF2.PdfReader(file_obj)
                for page in pdf_reader.pages:
                    text_content.append(page.extract_text())

            extracted_text = '\n\n'.join(text_content)
            pii_flags = detect_pii(extracted_text)
            sanitized_text, redacted, counts = sanitize_prompt_for_llm(extracted_text)
            pii_meta = {
                "pii_flags": pii_flags,
                "pii_redacted": bool(redacted),
                "pii_counts": counts,
            }

            if len(sanitized_text) > 3000:
                prompt = f"""다음 PDF 내용을 요약해주세요. 리서치 계획 수립에 도움이 될 만한 핵심 정보를 추출해주세요.

PDF 내용:
{sanitized_text[:3000]}...

한국어로 간결하게 핵심 내용만 정리해주세요."""
                result = self._get_gemini_adapter().generate_text(prompt)
                return result, pii_meta
            return sanitized_text, pii_meta
        except ImportError:
            return "PDF 파일이 업로드되었습니다. (PDF 텍스트 추출 기능을 사용하려면 PyPDF2 라이브러리가 필요합니다.)", self.empty_pii_meta()
        except Exception as exc:
            print(f"PDF 처리 오류: {exc}")
            traceback.print_exc()
            return f"PDF 처리 중 오류가 발생했습니다: {str(exc)}", self.empty_pii_meta()

    @staticmethod
    def empty_pii_meta():
        return {
            "pii_flags": {"email": False, "phone": False, "rrn": False},
            "pii_redacted": False,
            "pii_counts": {"email": 0, "phone": 0, "rrn": 0},
        }

    def authorize_file_download(self, *, filename, user_id, tier) -> GeneratorResult:
        safe_filename = secure_filename(filename)
        file_path = os.path.join(self.upload_folder, safe_filename)
        if not os.path.exists(file_path):
            return GeneratorResult("not_found", error="파일을 찾을 수 없습니다.")

        owner = self.get_owner(safe_filename)
        owner_id = owner.get("user_id")
        if owner_id:
            if str(user_id) != str(owner_id) and tier not in ("super", "admin"):
                return GeneratorResult("forbidden", error="권한이 없습니다.")
        elif tier not in ("super", "admin"):
            return GeneratorResult("forbidden", error="권한이 없습니다.")

        return GeneratorResult("ok", {"safe_filename": safe_filename})

    def load_owners(self) -> dict:
        if not os.path.exists(self.owners_path):
            return {}
        try:
            with open(self.owners_path, "r", encoding="utf-8") as file_obj:
                return json.load(file_obj) or {}
        except Exception:
            return {}

    def save_owners(self, owners: dict) -> None:
        tmp_path = self.owners_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(owners, file_obj, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.owners_path)

    def record_file_owner(self, file_id: str, user_id: str) -> None:
        if not file_id or not user_id:
            return
        with self.owners_lock:
            owners = self.load_owners()
            owners[file_id] = {
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.save_owners(owners)

    def get_owner(self, file_id: str) -> dict:
        with self.owners_lock:
            owners = self.load_owners()
            return owners.get(file_id) or {}


generator_service = GeneratorService()
