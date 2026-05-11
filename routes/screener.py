"""
routes/screener.py  (리팩터링 버전)
모든 비즈니스 로직은 screener/ 서브모듈로 위임.
각 엔드포인트는 요청 파싱 → 서브모듈 호출 → 응답 반환만 담당.
"""
from flask import Blueprint, request, jsonify, Response
import json
import traceback
from datetime import datetime
from sqlalchemy import select

from services.gemini_service import gemini_service
from services.openai_service import openai_service
from prompts.analysis_prompts import ScreenerPrompts
from utils.llm_utils import parse_llm_json_response
from utils.usage_metering import build_llm_usage_context, stream_with_llm_usage_context
from db.engine import session_scope
from db.models.core import StudySchedule
from routes.auth import tier_required

from screener.utils import normalize_column_name, find_matching_column_name
from screener.filters import apply_sincerity_filter
from screener.sanitize import mask_text, should_mask_field, sanitize_field_value, sanitize_participant, sanitize_schedule
from screener.builders import build_group_overview, jsonify_safe, build_calendar_snapshot
from screener.csv_profiler import (
    profile_csv_columns,
    attach_original_column_names,
    detect_identifier_column,
    detect_schedule_columns,
    analyze_data_schema,
    build_column_metadata,
    build_csv_info,
)
from screener.scoring import (
    step1_map_variables,
    step2_create_scoring_criteria,
    step3_build_dataframes,
    step3_extract_mapped_columns,
    step3_score_participants,
    step3_build_top_candidates,
    step4_run_final_selection,
    step4_parse_and_restore,
)
from screener.schedule_logic import parse_availability_data, validate_schedule_result
from screener.participant_logic import (
    build_participants_map,
    build_scored_data_sample,
    apply_llm_selection,
    apply_fallback_score_selection,
    build_finalize_summary,
)

import io
import pandas as pd

screener_bp = Blueprint('screener', __name__, url_prefix='/api/screener')


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

        prompt = ScreenerPrompts.prompt_analyze_plan(plan_text)
        result = openai_service.generate_response(prompt)
        analysis = parse_llm_json_response(result)

        return jsonify({'success': True, 'analysis': analysis})

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

        # DataFrame 생성 및 컬럼명 정규화
        df = pd.read_csv(io.StringIO(csv_content))
        df = df.fillna('')

        original_columns = df.columns.tolist()
        df.columns = [normalize_column_name(col) for col in df.columns]
        normalized_columns = df.columns.tolist()
        print(f"📌 컬럼명 정규화 완료: {len(df.columns)}개 컬럼")

        column_name_mapping = dict(zip(normalized_columns, original_columns))

        # 프로파일링
        column_schema = profile_csv_columns(df)
        column_schema = attach_original_column_names(column_schema, column_name_mapping)

        # LLM: 이름 컬럼, 일정 컬럼, 스키마 분석 (순차 호출)
        detected_name_column = detect_identifier_column(column_schema, openai_service)
        detected_schedule_columns = detect_schedule_columns(column_schema, openai_service)
        schema_analysis = analyze_data_schema(column_schema, openai_service)

        # 컬럼 메타데이터 생성
        column_metadata = build_column_metadata(df, column_schema)

        # csv_info 조립
        csv_info = build_csv_info(
            df, column_schema, column_metadata,
            detected_name_column, detected_schedule_columns, schema_analysis
        )

        return jsonify({
            'success': True,
            'csv_info': csv_info,
            'name_column': detected_name_column,
            'schedule_columns': detected_schedule_columns,
            'column_type_map': schema_analysis.get('column_type_map', {}),
            'sincerity_rules': schema_analysis.get('sincerity_rules'),
            'schema_analysis_text': schema_analysis.get('summary_text')
        })

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

    data = request.json
    csv_content = data.get('csv_content')
    plan_json = data.get('plan_json')
    csv_info = data.get('csv_info')
    sincerity_rules = data.get('sincerity_rules')

    if not all([csv_content, plan_json, csv_info, sincerity_rules]):
        def error_generate():
            yield f"data: {json.dumps({'error': '필수 데이터가 누락되었습니다.'})}\n\n"
        return Response(error_generate(), mimetype='text/event-stream')

    llm_usage_context = build_llm_usage_context(feature_key="screener")

    def generate():
        try:
            if isinstance(plan_json, str):
                parsed_plan_json = json.loads(plan_json)
            else:
                parsed_plan_json = plan_json

            # Step 1: 변수 맵핑
            mapping_analysis, key_variable_mappings, balance_variable_mappings = step1_map_variables(
                parsed_plan_json, csv_info, openai_service
            )
            yield f"data: {json.dumps({'step': 1, 'mapping_result': mapping_analysis})}\n\n"

            # Step 2: 스코어링 기준 생성
            target_groups = parsed_plan_json.get('target_groups', [])
            criteria_analysis = step2_create_scoring_criteria(
                target_groups, key_variable_mappings, csv_info, gemini_service
            )
            yield f"data: {json.dumps({'step': 2, 'criteria_result': criteria_analysis})}\n\n"

            # Step 3: DataFrame 준비 + 매핑 컬럼 추출
            df, df_original = step3_build_dataframes(csv_content, csv_info)
            key_mapped_columns, balance_mapped_columns, all_mapped_columns = step3_extract_mapped_columns(
                df, key_variable_mappings, balance_variable_mappings
            )

            df_mapped = df[['participant_id'] + all_mapped_columns].copy()
            group_criteria = criteria_analysis.get('scoring_criteria', [])

            # 성실도 필터링 + 스코어링
            df_mapped = step3_score_participants(df, df_mapped, group_criteria, sincerity_rules, csv_info)

            # 상위 후보 추출 + LLM 전달용 샘플 생성
            scored_data_for_frontend, sample_data = step3_build_top_candidates(
                df_mapped, target_groups, balance_mapped_columns
            )
            yield f"data: {json.dumps({'step': 3, 'scored_data': scored_data_for_frontend, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"

            # Step 4: 최종 선별 LLM 호출
            final_result = step4_run_final_selection(
                target_groups, sample_data, parsed_plan_json, gemini_service
            )

            if isinstance(final_result, dict) and final_result.get('content'):
                try:
                    parsed_json, final_participants_data = step4_parse_and_restore(
                        final_result, df_original, csv_info
                    )
                    result_data = {
                        'step': 4,
                        'final_selection': parsed_json,
                        'participants_data': final_participants_data,
                        'csv_info': csv_info
                    }
                    yield f"data: {json.dumps(result_data, ensure_ascii=False)}\n\n"
                except json.JSONDecodeError as e:
                    print(f"JSON 파싱 오류: {e}")
                    import re as _re
                    raw_content = _re.sub(r'[\x00-\x1f\x7f-\x9f]', '', final_result.get('content', '')[:500])
                    error_msg = f'JSON 파싱 실패: {str(e)}'
                    yield f"data: {json.dumps({'step': 4, 'error': error_msg, 'final_selection': {'error': error_msg}, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'step': 4, 'final_selection': final_result, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"

        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e), 'step': 'error'})}\n\n"

    return Response(
        stream_with_llm_usage_context(llm_usage_context, generate()),
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
    study_id = data.get('study_id')
    participants_data = data.get('participants_data') or []
    schedule_columns = data.get('schedule_columns') or []
    name_column = data.get('name_column') or ''
    target_groups = data.get('target_groups') or []
    required_participants = data.get('required_participants') or []
    has_name_column = bool(data.get('has_name_column'))

    if isinstance(schedule_columns, str):
        schedule_columns = [schedule_columns]

    if not participants_data:
        return jsonify({'success': False, 'error': 'participants_data is required'}), 400

    if not schedule_columns:
        return jsonify({'success': False, 'error': '일정 컬럼이 감지되지 않았습니다.'}), 400

    # 디버깅 로그
    print("=" * 80)
    print("🔍 [optimize-schedule] 입력 데이터 확인")
    print("=" * 80)
    print(f"📌 name_column: {name_column}")
    print(f"📅 schedule_columns: {schedule_columns}")
    print(f"👥 participants_data 개수: {len(participants_data)}명")
    if participants_data:
        sample = participants_data[0]
        print(f"📋 첫 번째 참여자 샘플 (키 목록): {list(sample.keys())}")
        matched_name_col = find_matching_column_name(name_column, sample) if name_column else None
        if matched_name_col and matched_name_col in sample:
            name_val = sample.get(matched_name_col)
            if matched_name_col != name_column:
                print(f"🔍 name_column 매칭: '{name_column}' → '{matched_name_col}'")
            print(f"✅ name_column '{matched_name_col}' 값: '{name_val}'")
    print("=" * 80)

    try:
        availability_data, required_from_data = parse_availability_data(
            participants_data, schedule_columns, name_column
        )

        if not availability_data:
            return jsonify({'success': False, 'error': '일정 정보가 있는 참여자를 찾을 수 없습니다.'}), 400

        merged_required = []
        for name in list(required_participants) + required_from_data:
            if name and name not in merged_required:
                merged_required.append(name)

        total_participants = len(availability_data)
        print("=" * 80)
        print("📅 일정 최적화 시작")
        print("=" * 80)
        print(f"📌 총 참여자 수: {total_participants}명")
        print(f"📌 필수 포함 인원: {len(merged_required)}명 - {merged_required}")
        print(f"📌 일정 컬럼: {schedule_columns}")
        print("=" * 80)

        context_info = {
            'study_id': study_id,
            'availability_data': availability_data,
            'target_groups': target_groups,
            'required_participants': merged_required,
            'schedule_columns': schedule_columns
        }

        prompt = ScreenerPrompts.prompt_schedule_optimization_with_context(
            json.dumps(context_info, ensure_ascii=False, indent=2),
            total_participants
        )
        result = openai_service.generate_response(prompt, {"temperature": 0.1})
        optimized_schedule = parse_llm_json_response(result)

        if not isinstance(optimized_schedule, dict):
            raise ValueError('LLM이 올바른 JSON을 반환하지 않았습니다.')

        validation_data = validate_schedule_result(optimized_schedule, availability_data)

        return jsonify({
            'success': True,
            'optimized_schedule': optimized_schedule,
            'validation': validation_data
        })

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

        selected_participants = data.get('selected_participants') or []
        selected_ids = {str(pid) for pid in selected_participants if pid is not None}

        target_groups = data.get('target_groups') or []
        csv_info = data.get('csv_info') or {}
        plan_json = data.get('plan_json') or {}
        if isinstance(plan_json, str):
            try:
                plan_json = json.loads(plan_json)
            except json.JSONDecodeError:
                plan_json = {}

        name_column = csv_info.get('name_column') or data.get('name_column')
        schedule_columns = csv_info.get('schedule_columns') or data.get('schedule_columns') or []
        contact_columns = csv_info.get('contact_columns') or data.get('contact_columns') or []

        has_name_column_flag = csv_info.get('has_name_column')
        has_name_column = bool(has_name_column_flag) if has_name_column_flag is not None else bool(name_column)

        if isinstance(schedule_columns, str):
            schedule_columns = [schedule_columns]
        if isinstance(contact_columns, str):
            contact_columns = [contact_columns]

        # 디버깅 로그
        print("=" * 80)
        print("🔍 [Step 5: finalize-participants] 입력 데이터 확인")
        print("=" * 80)
        print(f"📌 name_column: {name_column}")
        print(f"📅 schedule_columns: {schedule_columns}")
        print(f"👥 participants_data 개수: {len(participants_data)}명")
        print("=" * 80)

        balance_variables = plan_json.get('balance_variables', []) if isinstance(plan_json, dict) else []
        balance_variables_json = json.dumps(balance_variables, ensure_ascii=False, indent=2)

        group_info_map = {
            grp.get('name'): grp for grp in target_groups
            if isinstance(grp, dict) and grp.get('name')
        }
        ordered_group_names = list(group_info_map.keys())
        default_group_name = ordered_group_names[0] if ordered_group_names else 'Unassigned'

        # 참여자 맵 구성
        participants_map, participants_by_group, selected_by_group = build_participants_map(
            participants_data, group_info_map, default_group_name,
            name_column, has_name_column, selected_ids,
            schedule_columns, contact_columns,
        )

        # LLM 입력 데이터 생성
        group_targets_and_candidates, scored_data_sample = build_scored_data_sample(
            participants_by_group, selected_by_group, group_info_map,
            balance_variables, schedule_columns, contact_columns,
        )

        # LLM 호출
        llm_success = False
        final_selection_payload = None

        try:
            prompt = ScreenerPrompts.prompt_smart_selection_with_selected(
                selected_participants_info=selected_by_group,
                target_groups=target_groups,
                scored_data_sample=scored_data_sample,
                balance_variables_json=balance_variables_json,
                schedule_columns=schedule_columns,
                group_targets_and_candidates=group_targets_and_candidates
            )
            final_result = openai_service.generate_response(
                prompt, generation_config={"temperature": 0.1}
            )
            if not final_result.get('success'):
                raise ValueError('LLM 호출 실패')

            content = final_result.get('content', '')
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            first_brace = content.find('{')
            if first_brace > 0:
                content = content[first_brace:]
            import re as _re
            content = _re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content).strip()
            final_selection_payload = json.loads(content)
            llm_success = True
        except Exception as llm_error:
            print(f"⚠️ LLM 최종 선정 실패, 점수 기반으로 대체합니다: {llm_error}")
            traceback.print_exc()

        if llm_success and isinstance(final_selection_payload, dict):
            groups_output, final_participants_flat, reserve_participants_flat = apply_llm_selection(
                final_selection_payload, participants_map, participants_by_group,
                selected_by_group, group_info_map, default_group_name,
            )
            summary = build_finalize_summary(
                groups_output, final_participants_flat, reserve_participants_flat,
                len(participants_data)
            )

            # 디버깅 로그
            print("=" * 80)
            print("✅ [Step 5] LLM 기반 최종 선정 완료")
            print(f"👥 final_participants 개수: {len(final_participants_flat)}명")
            print("=" * 80)

            return jsonify({
                'success': True,
                'name_column': name_column,
                'schedule_columns': schedule_columns,
                'groups': groups_output,
                'final_participants': final_participants_flat,
                'reserve_participants': reserve_participants_flat,
                'summary': summary,
                'final_selection': final_selection_payload
            })

        # Fallback: 점수 기반 선정
        groups_output, final_participants_flat, reserve_participants_flat, total_user_selected, total_auto_selected = \
            apply_fallback_score_selection(participants_by_group, group_info_map, ordered_group_names)

        summary = build_finalize_summary(
            groups_output, final_participants_flat, reserve_participants_flat,
            len(participants_data), total_user_selected, total_auto_selected
        )

        print("=" * 80)
        print("⚠️ [Step 5] Fallback 점수 기반 선정 완료")
        print(f"👥 final_participants 개수: {len(final_participants_flat)}명")
        print("=" * 80)

        return jsonify({
            'success': True,
            'name_column': name_column,
            'schedule_columns': schedule_columns,
            'groups': groups_output,
            'final_participants': final_participants_flat,
            'reserve_participants': reserve_participants_flat,
            'summary': summary,
            'final_selection': None
        })

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

        try:
            study_id_int = int(study_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'study_id must be an integer'}), 400

        optimized_schedule = data.get('optimized_schedule') or {}
        participants_data = data.get('participants_data') or []
        schedule_columns = data.get('schedule_columns') or []
        name_column = data.get('name_column')
        target_groups = data.get('target_groups') or []
        validation_data = data.get('validation_data') or {}

        if isinstance(schedule_columns, str):
            schedule_columns = [schedule_columns]

        calendar_snapshot = build_calendar_snapshot(
            participants_data, optimized_schedule, name_column, schedule_columns
        )
        calendar_snapshot = jsonify_safe(calendar_snapshot)

        unassigned_count = validation_data.get('unassigned_count', 0)
        missing_count = validation_data.get('missing_count', 0)
        unassigned_participants = validation_data.get('unassigned_participants', [])
        missing_participants = validation_data.get('missing_participants', [])

        assigned_count = len([p for p in calendar_snapshot if p.get('has_schedule', False)])
        total_count = len(calendar_snapshot)
        unassigned_total = total_count - assigned_count

        if session_scope is None:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        saved_at_dt = datetime.utcnow().replace(microsecond=0)

        upsert_payload = {
            'study_id': study_id_int,
            'final_participants': calendar_snapshot,
            'saved_at': saved_at_dt,
            'updated_at': saved_at_dt
        }

        with session_scope() as db_session:
            existing = db_session.execute(
                select(StudySchedule).where(StudySchedule.study_id == study_id_int).limit(1)
            ).scalar_one_or_none()
            if existing:
                existing.final_participants = upsert_payload['final_participants']
                existing.saved_at = saved_at_dt
                saved_record = {
                    'id': existing.id,
                    'study_id': existing.study_id,
                    'final_participants': existing.final_participants,
                    'saved_at': existing.saved_at.isoformat() if existing.saved_at else None,
                    'updated_at': existing.updated_at.isoformat() if existing.updated_at else None,
                }
            else:
                created = StudySchedule(
                    study_id=study_id_int,
                    final_participants=upsert_payload['final_participants'],
                    saved_at=saved_at_dt,
                )
                db_session.add(created)
                db_session.flush()
                db_session.refresh(created)
                saved_record = {
                    'id': created.id,
                    'study_id': created.study_id,
                    'final_participants': created.final_participants,
                    'saved_at': created.saved_at.isoformat() if created.saved_at else None,
                    'updated_at': created.updated_at.isoformat() if created.updated_at else None,
                }

        return jsonify({
            'success': True,
            'saved_at': saved_at_dt.isoformat() + 'Z',
            'saved_participants_count': total_count,
            'assigned_count': assigned_count,
            'unassigned_count': unassigned_total,
            'validation': {
                'unassigned_count': unassigned_count,
                'missing_count': missing_count,
                'unassigned_participants': unassigned_participants[:10] if unassigned_participants else [],
                'missing_participants': missing_participants[:10] if missing_participants else []
            },
            'saved_record': saved_record
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
