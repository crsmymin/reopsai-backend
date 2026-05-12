"""Generator 관련 API 라우트 - 파일 업로드 및 AI 처리"""

from flask import Blueprint, request, jsonify
from reopsai.shared.auth import tier_required
from flask_jwt_extended import get_jwt_identity, get_jwt
from reopsai.application.generator_service import generator_service
import traceback

generator_bp = Blueprint('generator', __name__, url_prefix='/api/generator')


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
        result = generator_service.process_upload(file=file, user_id=user_id)
        if result.status in {"empty_filename", "unsupported", "too_large"}:
            return jsonify({'error': result.error}), 400
        if result.status != "ok":
            return jsonify({'error': result.error or '파일 처리 중 오류가 발생했습니다.'}), 500
        return jsonify(result.data)
        
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
        result = generator_service.authorize_file_download(
            filename=filename,
            user_id=jwt_user_id,
            tier=tier,
        )
        if result.status == "not_found":
            return jsonify({'error': '파일을 찾을 수 없습니다.'}), 404
        if result.status == "forbidden":
            return jsonify({'error': '권한이 없습니다.'}), 403

        return send_from_directory(generator_service.upload_folder, result.data["safe_filename"])
    except Exception as e:
        print(f"파일 다운로드 오류: {e}")
        return jsonify({'error': '파일 다운로드 중 오류가 발생했습니다.'}), 500
