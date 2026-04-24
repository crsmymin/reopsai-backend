"""Generator 관련 API 라우트 - 파일 업로드 및 AI 처리"""

from flask import Blueprint, request, jsonify
from routes.auth import tier_required
import os
import base64
import uuid
import json
import threading
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from flask_jwt_extended import get_jwt_identity, get_jwt
from services.openai_service import openai_service
from services.gemini_service import gemini_service
import traceback
from pii_utils import detect_pii, sanitize_prompt_for_llm

generator_bp = Blueprint('generator', __name__, url_prefix='/api/generator')

# 업로드된 파일 저장 디렉토리
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 업로드 폴더가 없으면 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

_OWNERS_LOCK = threading.Lock()
_OWNERS_PATH = os.path.join(UPLOAD_FOLDER, "_owners.json")


def _load_owners() -> dict:
    if not os.path.exists(_OWNERS_PATH):
        return {}
    try:
        with open(_OWNERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_owners(owners: dict) -> None:
    tmp_path = _OWNERS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(owners, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _OWNERS_PATH)


def _record_file_owner(file_id: str, user_id: str) -> None:
    if not file_id or not user_id:
        return
    with _OWNERS_LOCK:
        owners = _load_owners()
        owners[file_id] = {
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_owners(owners)


def _get_owner(file_id: str) -> dict:
    with _OWNERS_LOCK:
        owners = _load_owners()
        return owners.get(file_id) or {}


def allowed_file(filename):
    """허용된 파일 확장자인지 확인"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def process_image_with_vision(file_path, file_type):
    """이미지를 Vision API로 처리"""
    try:
        # 이미지를 base64로 인코딩
        with open(file_path, 'rb') as f:
            image_bytes = f.read()
            image_data = base64.b64encode(image_bytes).decode('utf-8')
        
        # MIME 타입 결정
        mime_type = file_type
        
        # Gemini Vision API 사용
        prompt = """이 이미지를 자세히 분석하고, 리서치 컨텍스트에서 유용한 정보를 추출해주세요.
- 이미지에 나타난 내용을 설명해주세요
- 리서치 계획 수립에 도움이 될 만한 인사이트가 있다면 언급해주세요
- 텍스트가 있다면 읽어서 정리해주세요

한국어로 간결하게 답변해주세요."""
        
        result = gemini_service.analyze_image_with_vision(
            image_data=image_data,
            mime_type=mime_type,
            prompt=prompt
        )
        
        return result
    except Exception as e:
        print(f"이미지 처리 오류: {e}")
        traceback.print_exc()
        raise


def process_pdf(file_path):
    """PDF 파일을 텍스트로 추출"""
    try:
        import PyPDF2
        
        text_content = []
        with open(file_path, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            for page in pdf_reader.pages:
                text_content.append(page.extract_text())
        
        extracted_text = '\n\n'.join(text_content)
        pii_flags = detect_pii(extracted_text)
        sanitized_text, redacted, counts = sanitize_prompt_for_llm(extracted_text)
        
        # 텍스트가 너무 길면 요약
        if len(sanitized_text) > 3000:
            prompt = f"""다음 PDF 내용을 요약해주세요. 리서치 계획 수립에 도움이 될 만한 핵심 정보를 추출해주세요.

PDF 내용:
{sanitized_text[:3000]}...

한국어로 간결하게 핵심 내용만 정리해주세요."""
            
            result = gemini_service.generate_text(prompt)
            return result, {"pii_flags": pii_flags, "pii_redacted": bool(redacted), "pii_counts": counts}
        else:
            return sanitized_text, {"pii_flags": pii_flags, "pii_redacted": bool(redacted), "pii_counts": counts}
    except ImportError:
        # PyPDF2가 없으면 기본 텍스트 반환
        return "PDF 파일이 업로드되었습니다. (PDF 텍스트 추출 기능을 사용하려면 PyPDF2 라이브러리가 필요합니다.)", {
            "pii_flags": {"email": False, "phone": False, "rrn": False},
            "pii_redacted": False,
            "pii_counts": {"email": 0, "phone": 0, "rrn": 0},
        }
    except Exception as e:
        print(f"PDF 처리 오류: {e}")
        traceback.print_exc()
        return f"PDF 처리 중 오류가 발생했습니다: {str(e)}", {
            "pii_flags": {"email": False, "phone": False, "rrn": False},
            "pii_redacted": False,
            "pii_counts": {"email": 0, "phone": 0, "rrn": 0},
        }


@generator_bp.route('/upload-file', methods=['POST'])
@tier_required(['free'])
def upload_file():
    """파일 업로드 및 AI 처리"""
    try:
        # 사용자 인증 확인 (JWT 우선, 헤더는 하위호환)
        jwt_user_id = get_jwt_identity()
        user_id_header = request.headers.get('X-User-ID') or request.headers.get('x-user-id')
        user_id = jwt_user_id or user_id_header
        if not user_id:
            return jsonify({'error': '사용자 인증이 필요합니다.'}), 401
        
        # 파일 확인
        if 'file' not in request.files:
            return jsonify({'error': '파일이 없습니다.'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '파일이 선택되지 않았습니다.'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': '지원하지 않는 파일 형식입니다. (이미지: JPEG, PNG, GIF, WebP / PDF)'}), 400
        
        # 파일 크기 확인
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': f'파일 크기는 {MAX_FILE_SIZE // (1024*1024)}MB 이하여야 합니다.'}), 400
        
        # 파일 저장
        file_ext = file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4()}.{file_ext}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(file_path)

        # 업로더 소유권 기록 (다운로드 권한 제한용)
        _record_file_owner(unique_filename, str(user_id))
        
        # 파일 타입에 따라 처리
        file_type = file.content_type
        processed_content = None
        pii_meta = None
        
        if file_type.startswith('image/'):
            # 이미지 처리
            processed_content = process_image_with_vision(file_path, file_type)
        elif file_type == 'application/pdf':
            # PDF 처리
            processed_content, pii_meta = process_pdf(file_path)
        else:
            return jsonify({'error': '지원하지 않는 파일 형식입니다.'}), 400
        
        # 파일 URL 생성 (실제로는 파일 서빙 엔드포인트 필요)
        file_url = f"/api/generator/file/{unique_filename}"
        
        return jsonify({
            'success': True,
            'file_id': unique_filename,
            'file_name': file.filename,
            'file_type': file_type,
            'file_size': file_size,
            'file_url': file_url,
            'processed_content': processed_content,
            # PII 주의/마스킹 정보 (best-effort)
            'pii': pii_meta or {
                "pii_flags": {"email": False, "phone": False, "rrn": False},
                "pii_redacted": False,
                "pii_counts": {"email": 0, "phone": 0, "rrn": 0},
            }
        })
        
    except Exception as e:
        print(f"파일 업로드 오류: {e}")
        traceback.print_exc()
        return jsonify({'error': f'파일 처리 중 오류가 발생했습니다: {str(e)}'}), 500


@generator_bp.route('/file/<filename>', methods=['GET'])
@tier_required(['free'])
def get_file(filename):
    """업로드된 파일 다운로드"""
    try:
        from flask import send_from_directory

        # 소유권 기반 다운로드 제한
        jwt_user_id = get_jwt_identity()
        claims = get_jwt() or {}
        tier = claims.get("tier")

        # 보안: 파일명 검증
        safe_filename = secure_filename(filename)
        file_path = os.path.join(UPLOAD_FOLDER, safe_filename)
        
        if not os.path.exists(file_path):
            return jsonify({'error': '파일을 찾을 수 없습니다.'}), 404

        owner = _get_owner(safe_filename)
        owner_id = owner.get("user_id")
        if owner_id:
            if str(jwt_user_id) != str(owner_id) and tier not in ("super", "admin"):
                return jsonify({'error': '권한이 없습니다.'}), 403
        else:
            # 소유권 정보가 없으면(이전 업로드 등) admin만 허용
            if tier not in ("super", "admin"):
                return jsonify({'error': '권한이 없습니다.'}), 403
        
        return send_from_directory(UPLOAD_FOLDER, safe_filename)
    except Exception as e:
        print(f"파일 다운로드 오류: {e}")
        return jsonify({'error': '파일 다운로드 중 오류가 발생했습니다.'}), 500
