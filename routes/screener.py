"""
routes/screener.py  (리팩터링 버전)
모든 비즈니스 로직은 screener/ 서브모듈로 위임.
각 엔드포인트는 요청 파싱 → 서브모듈 호출 → 응답 반환만 담당.
"""
from flask import Blueprint, request, jsonify, Response
import json
import traceback

from reopsai_backend.application.screener_service import screener_service
from utils.usage_metering import build_llm_usage_context, stream_with_llm_usage_context
from reopsai_backend.shared.auth import tier_required

screener_bp = Blueprint('screener', __name__, url_prefix='/api/screener')


def _find_matching_column_name(column_name, sample):
    if not column_name or not isinstance(sample, dict):
        return None
    if column_name in sample:
        return column_name
    normalized_target = str(column_name).strip().lower()
    for key in sample.keys():
        if str(key).strip().lower() == normalized_target:
            return key
    return None


# ---------------------------------------------------------------------------
# 1단계: 계획서 분석
# ---------------------------------------------------------------------------

@screener_bp.route('/analyze-plan', methods=['POST'])
@tier_required(['free'])
def analyze_plan():
    """1단계: 계획서 분석 (studyId 포함)"""
    try:
        data = request.json
        plan_text = data.get('plan_text')
        study_id = data.get('study_id')

        if not plan_text:
            return jsonify({'success': False, 'error': '계획서 텍스트가 필요합니다.'}), 400

        result = screener_service.analyze_plan(plan_text=plan_text)
        return jsonify(result.data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ---------------------------------------------------------------------------
# 2단계: CSV 업로드 + 프로파일링 + 스키마 분석
# ---------------------------------------------------------------------------

@screener_bp.route('/upload-csv', methods=['POST'])
@tier_required(['free'])
def upload_csv():
    """2단계: CSV 업로드 + 프로파일링 + 스키마 분석만"""
    try:
        data = request.json
        csv_content = data.get('csv_content')

        if not csv_content:
            return jsonify({'success': False, 'error': 'CSV 내용이 필요합니다.'}), 400

        result = screener_service.upload_csv(csv_content=csv_content)
        return jsonify(result.data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500



# ---------------------------------------------------------------------------
# 3-4단계: 최적 참여자 찾기 (SSE)
# ---------------------------------------------------------------------------

@screener_bp.route('/find-optimal-participants', methods=['POST'])
@tier_required(['free'])
def find_optimal_participants():
    """최적의 참여자 찾기: 변수 맵핑 + 기준 생성 + 점수 산정 + 최종 결과 (SSE)"""

    data = request.json or {}
    csv_content = data.get('csv_content')
    plan_json = data.get('plan_json')
    csv_info = data.get('csv_info')
    sincerity_rules = data.get('sincerity_rules')

    if not all([csv_content, plan_json, csv_info, sincerity_rules]):
        def error_generate():
            yield f"data: {json.dumps({'error': '필수 데이터가 누락되었습니다.'})}\n\n"
        return Response(error_generate(), mimetype='text/event-stream')

    llm_usage_context = build_llm_usage_context(feature_key="screener")
    return Response(
        stream_with_llm_usage_context(
            llm_usage_context,
            screener_service.find_optimal_participants_stream(
                csv_content=csv_content,
                plan_json=plan_json,
                csv_info=csv_info,
                sincerity_rules=sincerity_rules,
            ),
        ),
        mimetype='text/event-stream',
    )


# ---------------------------------------------------------------------------
# 일정 최적화
# ---------------------------------------------------------------------------

@screener_bp.route('/optimize-schedule', methods=['POST'])
@tier_required(['free'])
def optimize_schedule():
    """참여자 일정 최적화: 선택된 참여자들의 가용 시간을 분석하여 중복 없이 최적 배분"""

    data = request.json or {}
    participants_data = data.get('participants_data') or []
    schedule_columns = data.get('schedule_columns') or []
    name_column = data.get('name_column') or ''

    if isinstance(schedule_columns, str):
        schedule_columns = [schedule_columns]

    if not participants_data:
        return jsonify({'success': False, 'error': 'participants_data is required'}), 400

    if not schedule_columns:
        return jsonify({'success': False, 'error': '일정 컬럼이 감지되지 않았습니다.'}), 400

    print("=" * 80)
    print("🔍 [optimize-schedule] 입력 데이터 확인")
    print("=" * 80)
    print(f"📌 name_column: {name_column}")
    print(f"📅 schedule_columns: {schedule_columns}")
    print(f"👥 participants_data 개수: {len(participants_data)}명")
    if participants_data:
        sample = participants_data[0]
        print(f"📋 첫 번째 참여자 샘플 (키 목록): {list(sample.keys())}")
        matched_name_col = _find_matching_column_name(name_column, sample) if name_column else None
        if matched_name_col and matched_name_col in sample:
            name_val = sample.get(matched_name_col)
            if matched_name_col != name_column:
                print(f"🔍 name_column 매칭: '{name_column}' → '{matched_name_col}'")
            print(f"✅ name_column '{matched_name_col}' 값: '{name_val}'")
    print("=" * 80)

    try:
        result = screener_service.optimize_schedule(data={**data, 'schedule_columns': schedule_columns})
        if result.status == "no_availability":
            return jsonify({'success': False, 'error': result.error}), 400
        return jsonify(result.data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 5단계: 최종 참여자 확정
# ---------------------------------------------------------------------------

@screener_bp.route('/finalize-participants', methods=['POST'])
@tier_required(['free'])
def finalize_participants():
    """Step 5: 4단계 결과 기반으로 최종 참여자 목록을 확정 (LLM 활용)"""
    try:
        data = request.json or {}

        participants_data = data.get('participants_data') or []
        if not participants_data:
            return jsonify({'success': False, 'error': 'participants_data is required'}), 400

        print("=" * 80)
        print("🔍 [Step 5: finalize-participants] 입력 데이터 확인")
        print("=" * 80)
        csv_info = data.get('csv_info') or {}
        name_column = csv_info.get('name_column') or data.get('name_column')
        schedule_columns = csv_info.get('schedule_columns') or data.get('schedule_columns') or []
        if isinstance(schedule_columns, str):
            schedule_columns = [schedule_columns]
        print(f"📌 name_column: {name_column}")
        print(f"📅 schedule_columns: {schedule_columns}")
        print(f"👥 participants_data 개수: {len(participants_data)}명")
        print("=" * 80)

        result = screener_service.finalize_participants(data=data)
        return jsonify(result.data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 5단계: 일정/참여자 저장
# ---------------------------------------------------------------------------

@screener_bp.route('/save-schedule', methods=['POST'])
@tier_required(['free'])
def save_schedule_route():
    """Step 5: 최종 일정/참여자 데이터를 저장"""
    try:
        data = request.json or {}

        study_id = data.get('study_id')
        if study_id is None:
            return jsonify({'success': False, 'error': 'study_id is required'}), 400

        result = screener_service.save_schedule(data=data)
        if result.status == "invalid_study_id":
            return jsonify({'success': False, 'error': result.error}), 400
        if result.status == "db_unavailable":
            return jsonify({'success': False, 'error': result.error}), 500
        return jsonify(result.data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
