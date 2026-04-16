from flask import Blueprint, request, jsonify, Response
import pandas as pd
import io
import json
import re
import traceback
import copy
from collections import defaultdict
from datetime import datetime

from services.gemini_service import gemini_service
from services.openai_service import openai_service
from prompts.analysis_prompts import ScreenerPrompts
from app import parse_llm_json_response
from sqlalchemy import select

from db.engine import session_scope
from db.models.core import StudySchedule
from routes.auth import tier_required
from screener.utils import normalize_column_name, find_matching_column_name


def detect_column_type(col_data):
    # ⭐ DataFrame이면 첫 번째 컬럼을 Series로 변환
    if isinstance(col_data, pd.DataFrame):
        col_data = col_data.iloc[:, 0]
    # Series인지 확인
    if not isinstance(col_data, pd.Series):
        # Series가 아니면 기본값 반환
        return 'categorical'
    
    # 숫자형 데이터인지 확인
    try:
        pd.to_numeric(col_data, errors='raise')
        return 'numerical'
    except:
        pass
    
    # 문자열 길이로 판단
    if col_data.astype(str).str.len().mean() > 50:
        return 'text'
    else:
        return 'categorical'


def detect_suspicious_patterns(df, csv_schema, prose_columns):
    """
    1차 필터링: 의심 패턴 감지
    
    기준:
    - 5점 척도 문항 5개 이상 연속 같은 값 (straight-lining)
    - 극단값(1 또는 5)만 반복 (80% 이상)
    - 주관식 의미 없는 패턴
    """
    suspicious_indices = set()
    
    # 1. 스키마에서 5점 척도 문항 자동 감지
    likert_columns = []
    for col_info in csv_schema:
        col_name = col_info.get('column_name', '')
        if col_name in df.columns:
            values = df[col_name].unique()
            try:
                numeric_vals = pd.to_numeric(values, errors='coerce')
                # 1-5 범위의 정수만 있고, 고유값이 5개 이하면 5점 척도로 추정
                valid_vals = [v for v in numeric_vals if pd.notna(v) and 1 <= v <= 5]
                if len(valid_vals) >= 3 and len(set(valid_vals)) <= 5:
                    # 최소 3개 샘플이 모두 1-5 범위면 5점 척도로 추정
                    likert_columns.append(col_name)
            except:
                pass
    
    print(f"DEBUG: 5점 척도 문항 감지: {len(likert_columns)}개 - {likert_columns[:5]}")
    
    # 2. 기준 B: 5점 척도 5개 이상 연속 같은 값 (straight-lining)
    if len(likert_columns) >= 5:
        for idx, row in df.iterrows():
            max_consecutive = 0
            current_consecutive = 0
            last_value = None
            
            for col in likert_columns:
                val = row[col]
                if pd.notna(val):
                    try:
                        val = float(val)
                        if val == last_value:
                            current_consecutive += 1
                            max_consecutive = max(max_consecutive, current_consecutive)
                        else:
                            current_consecutive = 1
                            last_value = val
                            max_consecutive = max(max_consecutive, current_consecutive)
                    except:
                        current_consecutive = 0
                        last_value = None
            
            # 5개 이상 연속 같은 값 → 의심
            if max_consecutive >= 5:
                suspicious_indices.add(idx)
    
    # 3. 기준 C: 극단값(1 또는 5)만 반복 (80% 이상)
    if len(likert_columns) >= 5:
        for idx, row in df.iterrows():
            if idx in suspicious_indices:
                continue
            
            extreme_count = 0
            total_count = 0
            
            for col in likert_columns[:15]:  # 최대 15개만 체크
                val = row[col]
                if pd.notna(val):
                    try:
                        val = float(val)
                        total_count += 1
                        if val == 1 or val == 5:
                            extreme_count += 1
                    except:
                        pass
            
            # 80% 이상이 극단값만 → 의심
            if total_count >= 5 and (extreme_count / total_count) >= 0.8:
                suspicious_indices.add(idx)
    
    # 4. 기준 D: 주관식 의미 없는 패턴
    for col in prose_columns:
        if col not in df.columns:
            continue
        
        # 의미 없는 패턴
        meaningless_patterns = ['ㅇㅇ', 'ㅇ', 'ㅋㅋ', 'ㅋ', '없음', '없어요', '없습니다', '..', '...', 'ㄱㄱ']
        
        for idx, row in df.iterrows():
            if idx in suspicious_indices:
                continue
            
            text = str(row[col]).strip()
            
            # 의미 없는 패턴이 있고 글자 수가 적으면 의심
            if any(pattern in text for pattern in meaningless_patterns) and len(text) < 15:
                suspicious_indices.add(idx)
                break
    
    print(f"DEBUG: 의심 케이스 감지: {len(suspicious_indices)}명 / 전체 {len(df)}명")
    
    return suspicious_indices


def apply_sincerity_filter(df, sincerity_rules, csv_schema):
    """
    성실도 필터링: 규칙 기반 점수 계산 + 의심 패턴 감지
    (LLM 재검증 제거 버전)
    
    Returns:
        filtered_df: 필터링된 DataFrame
    """
    prose_columns = sincerity_rules.get('prose_columns', [])
    filter_threshold = sincerity_rules.get('filter_threshold', 3)
    
    print("=" * 80)
    print("📊 성실도 필터링 시작")
    print("=" * 80)
    initial_total = len(df)
    print(f"📌 초기 총 응답자 수: {initial_total}명")
    
    # 1차: 규칙 기반 점수 계산 (주관식 글자 수)
    for col in prose_columns:
        if col in df.columns:
            df[f'{col}_sincerity_score'] = df[col].astype(str).str.len()
    
    sincerity_scores = []
    for col in prose_columns:
        if f'{col}_sincerity_score' in df.columns:
            sincerity_scores.append(f'{col}_sincerity_score')
    
    if sincerity_scores:
        df['avg_sincerity_score'] = df[sincerity_scores].mean(axis=1)
    else:
        # 주관식이 없으면 기본 점수 부여 (통과)
        df['avg_sincerity_score'] = 5
    
    # 2차: 의심 패턴 감지
    suspicious_indices = detect_suspicious_patterns(df, csv_schema, prose_columns)
    suspicious_count = len(suspicious_indices)
    suspicious_percentage = (suspicious_count / initial_total * 100) if initial_total > 0 else 0
    print(f"⚠️  의심 패턴 감지: {suspicious_count}명 ({suspicious_percentage:.1f}%)")
    
    # 의심 케이스는 점수를 1점으로 낮춤 (자동 제외)
    for idx in suspicious_indices:
        df.loc[idx, 'avg_sincerity_score'] = 1
        df.loc[idx, 'sincerity_reason'] = '의심 패턴 감지 (straight-lining, 극단값 반복, 무성의 응답)'
    
    # 최종 필터링
    before_filter_count = len(df)
    filtered_df = df[df['avg_sincerity_score'] >= filter_threshold].copy()
    filtered_count = len(filtered_df)
    excluded_count = before_filter_count - filtered_count
    excluded_percentage = (excluded_count / before_filter_count * 100) if before_filter_count > 0 else 0
    
    print("=" * 80)
    print("✅ 최종 필터링 결과")
    print("=" * 80)
    print(f"📊 필터링 전: {before_filter_count}명")
    print(f"📊 필터링 후: {filtered_count}명")
    print(f"🚫 제외된 인원: {excluded_count}명 ({excluded_percentage:.1f}%)")
    print(f"📌 필터 기준: {filter_threshold}점 이상")
    print("=" * 80)
    
    return filtered_df


SENSITIVE_KEYWORDS = [
    'name', 'contact', 'phone', 'tel', 'email', 'mobile', 'id',
    '이름', '성함', '연락', '전화', '휴대', '번호'
]
SENSITIVE_EXCLUDE_KEYS = {'participant_id', '_participant_id'}


def normalize_column_name(value):
    if not isinstance(value, str):
        return value
    return value.replace('\xa0', ' ').replace('\u3000', ' ').strip()


def mask_text(value):
    if not isinstance(value, str):
        return value
    if not value:
        return value

    masked_chars = []
    buffer = []

    def flush_buffer():
        if not buffer:
            return
        chunk = ''.join(buffer)
        if len(chunk) <= 1:
            masked_chars.append(chunk)
        else:
            masked_chars.append(chunk[0] + '*' * (len(chunk) - 1))
        buffer.clear()

    for ch in value:
        if ch.isalnum():
            buffer.append(ch)
        else:
            flush_buffer()
            masked_chars.append(ch)
    flush_buffer()

    return ''.join(masked_chars)


def should_mask_field(field_name, explicit_sensitive):
    if not field_name or not isinstance(field_name, str):
        return False

    normalized = normalize_column_name(field_name)
    if normalized in SENSITIVE_EXCLUDE_KEYS:
        return False

    if normalized in explicit_sensitive:
        return True

    lowered = normalized.lower()
    for keyword in SENSITIVE_KEYWORDS:
        if keyword in lowered or keyword in normalized:
            return True

    return False


def sanitize_field_value(field_name, value, explicit_sensitive):
    normalized_field = normalize_column_name(field_name) if isinstance(field_name, str) else field_name

    if isinstance(value, str):
        return mask_text(value) if should_mask_field(normalized_field, explicit_sensitive) else value

    if isinstance(value, list):
        if should_mask_field(normalized_field, explicit_sensitive):
            return [mask_text(str(item)) for item in value]

        sanitized_list = []
        for item in value:
            if isinstance(item, str) and should_mask_field(normalized_field, explicit_sensitive):
                sanitized_list.append(mask_text(item))
            elif isinstance(item, dict):
                sanitized_list.append({
                    normalize_column_name(sub_key) if isinstance(sub_key, str) else sub_key:
                    sanitize_field_value(sub_key, sub_val, explicit_sensitive)
                    for sub_key, sub_val in item.items()
                })
            else:
                sanitized_list.append(item)
        return sanitized_list

    if isinstance(value, dict):
        normalized_field_str = str(normalized_field)
        if normalized_field_str in ['_contact_values', '_schedule_values']:
            return {
                normalize_column_name(sub_key) if isinstance(sub_key, str) else sub_key:
                mask_text(str(sub_val)) if isinstance(sub_val, str) else sub_val
                for sub_key, sub_val in value.items()
            }

        return {
            normalize_column_name(sub_key) if isinstance(sub_key, str) else sub_key:
            sanitize_field_value(sub_key, sub_val, explicit_sensitive)
            for sub_key, sub_val in value.items()
        }

    return value


def sanitize_participant(participant, original_name_column, normalized_name_column, explicit_sensitive):
    if not isinstance(participant, dict):
        return participant

    sanitized = {}
    for key, value in participant.items():
        normalized_key = normalize_column_name(key) if isinstance(key, str) else key
        sanitized[normalized_key] = sanitize_field_value(normalized_key, value, explicit_sensitive)

    if normalized_name_column and normalized_name_column in sanitized:
        sanitized[normalized_name_column] = mask_text(str(sanitized[normalized_name_column]))

    if '_display_name' in sanitized:
        sanitized['_display_name'] = mask_text(str(sanitized['_display_name']))

    original_name_value = None
    if original_name_column:
        original_name_value = participant.get(original_name_column) or participant.get(normalized_name_column)

    masked_primary = mask_text(str(
        original_name_value
        or participant.get('_display_name')
        or participant.get('participant_name')
        or ''
    ))
    sanitized['_masked_name'] = masked_primary

    return sanitized


def sanitize_schedule(optimized_schedule):
    if not isinstance(optimized_schedule, dict):
        return optimized_schedule

    sanitized = copy.deepcopy(optimized_schedule)
    assignments = sanitized.get('schedule_assignments')
    if isinstance(assignments, dict):
        for _, day_data in assignments.items():
            if not isinstance(day_data, dict):
                continue
            for slot, value in day_data.items():
                if slot == 'weekday':
                    continue
                if isinstance(value, list):
                    day_data[slot] = [mask_text(str(item)) for item in value]
                elif isinstance(value, str):
                    day_data[slot] = mask_text(value)

    unassigned = sanitized.get('unassigned_participants')
    if isinstance(unassigned, list):
        sanitized['unassigned_participants'] = [mask_text(str(item)) for item in unassigned]

    required = sanitized.get('required_participants')
    if isinstance(required, list):
        sanitized['required_participants'] = [mask_text(str(item)) for item in required]

    return sanitized


def build_group_overview(participants, normalized_name_column):
    groups = defaultdict(lambda: {'final': [], 'reserve': []})

    for participant in participants:
        if not isinstance(participant, dict):
            continue

        group_name = participant.get('_assigned_group') or 'Unassigned'
        status = participant.get('_selection_status') or 'auto_selected'

        masked_name = None
        if normalized_name_column and normalized_name_column in participant:
            masked_name = participant.get(normalized_name_column)
        if not masked_name:
            masked_name = participant.get('_masked_name') or mask_text(str(participant.get('_display_name', '')))

        entry = {
            'participant_id': participant.get('participant_id'),
            'masked_name': masked_name,
            'selection_status': status,
            'score': participant.get('_group_score'),
            'selection_reason': participant.get('_selection_reason')
        }

        if participant.get('_schedule_values'):
            entry['schedule_values'] = participant['_schedule_values']
        if participant.get('_contact_values'):
            entry['contact_values'] = participant['_contact_values']

        if status == 'reserve':
            groups[group_name]['reserve'].append(entry)
        else:
            groups[group_name]['final'].append(entry)

    return groups


def jsonify_safe(data):
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def build_calendar_snapshot(participants_data, optimized_schedule, name_column, schedule_columns):
    """
    모든 선정된 참여자를 저장용 스냅샷으로 생성 (일정 배정 여부와 관계없이)
    - participant_id: 그대로
    - 이름: 첫 글자만 남기고 마스킹
    - 선별된 일정: schedule_assignments에서 해당 참여자가 배정된 날짜/시간 슬롯만 (배정되지 않으면 빈 배열)
    """
    schedule_assignments = {}
    if isinstance(optimized_schedule, dict):
        schedule_assignments = optimized_schedule.get('schedule_assignments', {})
    
    # 1. 달력에 배정된 참여자와 그들의 일정 추출
    participant_schedules = {}  # {participant_name: [assigned_slots]}
    
    for date_key, day_assignments in schedule_assignments.items():
        if not isinstance(day_assignments, dict):
            continue
        
        # LLM이 생성한 weekday 정보 추출
        weekday = day_assignments.get('weekday', '')
        
        for time_slot, participants_list in day_assignments.items():
            if time_slot == 'weekday':
                continue
            if isinstance(participants_list, list):
                for participant_name in participants_list:
                    name_str = str(participant_name).strip()
                    if name_str:
                        if name_str not in participant_schedules:
                            participant_schedules[name_str] = []
                        participant_schedules[name_str].append({
                            'date': date_key,
                            'weekday': weekday,  # LLM이 만든 weekday 저장
                            'time_slot': time_slot
                        })
            elif isinstance(participants_list, str):
                name_str = participants_list.strip()
                if name_str:
                    if name_str not in participant_schedules:
                        participant_schedules[name_str] = []
                    participant_schedules[name_str].append({
                        'date': date_key,
                        'weekday': weekday,  # LLM이 만든 weekday 저장
                        'time_slot': time_slot
                    })
    
    # 2. participants_data에서 모든 참여자 정보 매칭 (배정 여부와 관계없이)
    normalized_name_column = normalize_column_name(name_column) if name_column else None
    snapshot = []
    
    for participant in participants_data:
        if not isinstance(participant, dict):
            continue
        
        # 이름 추출
        participant_name = None
        if name_column and participant.get(name_column):
            participant_name = str(participant[name_column]).strip()
        elif normalized_name_column and participant.get(normalized_name_column):
            participant_name = str(participant[normalized_name_column]).strip()
        elif participant.get('_display_name'):
            participant_name = str(participant['_display_name']).strip()
        elif participant.get('participant_name'):
            participant_name = str(participant['participant_name']).strip()
        
        if not participant_name:
            continue
        
        # participant_id 추출
        pid = str(
            participant.get('participant_id')
            or participant.get('id')
            or participant.get('participantId')
            or ''
        ).strip()
        
        if not pid:
            continue
        
        # 3. 저장용 데이터 구성 (배정되지 않은 참여자도 포함, assigned_schedule은 빈 배열)
        entry = {
            'participant_id': pid,  # 마스킹 안함
            'name': mask_text(participant_name),  # 첫 글자만 남기고 마스킹
            'assigned_schedule': participant_schedules.get(participant_name, []),  # 배정된 일정 (없으면 빈 배열)
            'assigned_group': participant.get('_assigned_group') or participant.get('assigned_group'),
            'selection_status': participant.get('_selection_status') or participant.get('selection_status'),
            'has_schedule': participant_name in participant_schedules  # 일정 배정 여부 플래그
        }
        
        snapshot.append(entry)
    
    return snapshot


screener_bp = Blueprint('screener', __name__, url_prefix='/api/screener')


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
        
        # AI 분석 로직
        prompt = ScreenerPrompts.prompt_analyze_plan(plan_text)
        result = openai_service.generate_response(prompt)
        analysis = parse_llm_json_response(result)
        
        return jsonify({
            'success': True,
            'analysis': analysis
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@screener_bp.route('/upload-csv', methods=['POST'])
@tier_required(['free'])
def upload_csv():
    """2단계: CSV 업로드 + 프로파일링 + 스키마 분석만"""
    try:
        data = request.json
        csv_content = data.get('csv_content')
        
        if not csv_content:
            return jsonify({'success': False, 'error': 'CSV 내용이 필요합니다.'}), 400
        
        # 1. CSV 프로파일링
        df = pd.read_csv(io.StringIO(csv_content))
        df = df.fillna('')
        
        # ⭐ 원본 컬럼명 저장 (정규화 전)
        original_columns = df.columns.tolist()
        
        # ⭐ 컬럼명 정규화: 3-4단계와 동일한 정규화 로직 사용 (통일)
        df.columns = [normalize_column_name(col) for col in df.columns]
        normalized_columns = df.columns.tolist()
        print(f"📌 컬럼명 정규화 완료: {len(df.columns)}개 컬럼")
        
        # 원본-정규화 매핑 생성
        column_name_mapping = dict(zip(normalized_columns, original_columns))
        
        column_schema = []
        print(f"📊 CSV 프로파일링 시작: {len(df)}행 × {len(df.columns)}열")
        
        for col in df.columns:
            col_data = df[col]
            num_unique = col_data.nunique()
            
            col_type = 'Categorical'
            col_values_sample = []
            value_frequencies = {}
            
            if pd.api.types.is_numeric_dtype(col_data) and not pd.api.types.is_bool_dtype(col_data):
                if num_unique < 15:
                    col_type = 'Categorical_Num'
                    col_values_sample = [str(v) for v in sorted(col_data.unique())]
                    # ✨ 빈도수 추가
                    value_counts = col_data.value_counts()
                    value_frequencies = {
                        'top_5': {str(k): int(v) for k, v in value_counts.head(5).items()},
                        'total_responses': int(len(col_data)),
                        'non_empty_responses': int((col_data != '').sum()),
                        'response_rate': float((col_data != '').sum() / len(col_data) * 100)
                    }
                else:
                    col_type = 'Numerical'
                    col_values_sample = [str(col_data.min()), str(col_data.max())]
                    value_frequencies = {
                        'min': float(col_data.min()) if pd.notna(col_data.min()) else None,
                        'max': float(col_data.max()) if pd.notna(col_data.max()) else None,
                        'mean': float(col_data.mean()) if pd.notna(col_data.mean()) else None,
                        'median': float(col_data.median()) if pd.notna(col_data.median()) else None
                    }
            elif pd.api.types.is_bool_dtype(col_data):
                col_type = 'Boolean'
                col_values_sample = [str(v) for v in col_data.unique()]
                value_counts = col_data.value_counts()
                value_frequencies = {
                    'top_5': {str(k): int(v) for k, v in value_counts.items()},
                    'total_responses': int(len(col_data)),
                    'non_empty_responses': int((col_data != '').sum()),
                    'response_rate': float((col_data != '').sum() / len(col_data) * 100)
                }
            else:
                col_str = col_data.astype(str)
                avg_length = col_str.str.len().mean()
                
                if avg_length > 50:
                    col_type = 'OpenText'
                    # 전체 데이터에서 다양한 위치에서 샘플링 (앞, 중간, 뒤)
                    total_rows = len(col_str)
                    sample_indices = []
                    if total_rows > 50:
                        # 0, 10, 20, 30, 40, 50부터 끝까지
                        sample_indices = list(range(0, min(50, total_rows), 10))
                        sample_indices.extend(range(50, total_rows, 20))
                    else:
                        sample_indices = range(min(10, total_rows))
                    col_values_sample = [col_str.iloc[i] for i in sample_indices if i < len(col_str)]
                    # OpenText는 평균 길이 정보만
                    value_frequencies = {
                        'avg_length': float(avg_length),
                        'total_responses': int(len(col_data)),
                        'non_empty_responses': int((col_data != '').sum())
                    }
                else:
                    col_type = 'Categorical'
                    # 전체 데이터에서 고유값이 아닌 실제 샘플을 많이 가져오기
                    unique_vals = col_data.unique()[:30]  # 고유값 30개까지
                    # 추가로 전체 데이터에서 랜덤 샘플도 가져오기
                    if len(col_data) > len(unique_vals):
                        sample_rows = col_data.sample(min(50, len(col_data)), random_state=42).tolist()
                        col_values_sample = list(set([str(v) for v in list(unique_vals) + sample_rows]))[:50]
                    else:
                        col_values_sample = [str(v) for v in unique_vals]
                    # ✨ 빈도수 추가
                    value_counts = col_data.value_counts()
                    value_frequencies = {
                        'top_5': {str(k): int(v) for k, v in value_counts.head(5).items()},
                        'total_responses': int(len(col_data)),
                        'non_empty_responses': int((col_data != '').sum()),
                        'response_rate': float((col_data != '').sum() / len(col_data) * 100)
                    }
            
            # 원본 컬럼명도 함께 저장
            original_col_name = column_name_mapping.get(col, col)
            column_schema.append({
                'column_name': col,  # 정규화된 컬럼명
                'original_column_name': original_col_name,  # 원본 컬럼명 (줄바꿈 등 포함)
                'type': col_type,
                'unique_count': num_unique,
                'values_sample': col_values_sample,
                'value_frequencies': value_frequencies  # ✨ 새로 추가
            })
        
        print(f"✅ 프로파일링 완료: {len(column_schema)}개 컬럼 분석됨")
        
        # 2-1. 이름 컬럼 찾기 (1단계)
        schema_data = {'schema': column_schema}
        print("DEBUG: 1단계 - 이름 컬럼 찾기 시작")
        name_prompt = ScreenerPrompts.prompt_detect_name_column_only(
            json.dumps(schema_data, ensure_ascii=False, indent=2)
        )
        name_result = openai_service.generate_response(name_prompt, {"temperature": 0.1})
        name_analysis = parse_llm_json_response(name_result)
        ai_name_column = name_analysis.get('name_column')
        
        # ⭐ AI가 반환한 컬럼명 처리 (원본 또는 정규화된 컬럼명 모두 처리)
        detected_name_column = None
        if ai_name_column:
            # AI가 원본 컬럼명을 반환했는지 확인
            # column_schema에서 original_column_name과 매칭 시도
            for col_info in column_schema:
                original_col = col_info.get('original_column_name', '')
                normalized_col = col_info.get('column_name', '')
                
                # 원본 컬럼명과 정확히 일치하거나, 정규화된 컬럼명과 일치하는지 확인
                if ai_name_column == original_col or ai_name_column == normalized_col:
                    # 정규화된 컬럼명으로 저장 (DataFrame에서 사용하기 위해)
                    detected_name_column = normalized_col
                    print(f"DEBUG: AI가 찾은 이름 컬럼 (원본): {original_col}")
                    print(f"DEBUG: 매칭된 정규화된 컬럼명: {normalized_col}")
                    break
            
            # 매칭 실패 시 정규화 시도
            if not detected_name_column:
                normalized_ai_col = normalize_column_name(ai_name_column)
                # 정규화된 컬럼명으로 다시 매칭 시도
                for col_info in column_schema:
                    if col_info.get('column_name') == normalized_ai_col:
                        detected_name_column = normalized_ai_col
                        print(f"DEBUG: AI가 찾은 이름 컬럼: {ai_name_column}")
                        print(f"DEBUG: 정규화 후 매칭된 컬럼명: {normalized_ai_col}")
                        break
        
        if not detected_name_column and ai_name_column:
            print(f"⚠️ WARNING: AI가 찾은 컬럼명 '{ai_name_column}'을 매칭할 수 없습니다.")
        
        # 2단계: 일정 컬럼 찾기
        print("DEBUG: 2단계 - 일정 컬럼 찾기 시작")
        schedule_prompt = ScreenerPrompts.prompt_detect_schedule_columns_only(
            json.dumps(schema_data, ensure_ascii=False, indent=2)
        )
        schedule_result = openai_service.generate_response(schedule_prompt, {"temperature": 0.1})
        schedule_analysis = parse_llm_json_response(schedule_result)
        detected_schedule_columns = schedule_analysis.get('schedule_columns', [])
        print(f"DEBUG: AI가 찾은 일정 컬럼(원본): {detected_schedule_columns}")
        
        # 3단계: 성실도 분석 및 기타
        print("DEBUG: 3단계 - 성실도 및 기타 분석 시작")
        schema_prompt = ScreenerPrompts.prompt_analyze_data_schema(
            json.dumps(schema_data, ensure_ascii=False, indent=2)
        )
        schema_result = openai_service.generate_response(schema_prompt, {"temperature": 0.1})
        schema_analysis = parse_llm_json_response(schema_result)
        
        # ✨ column_metadata 생성: 스코어링 시 참조할 구조화된 메타데이터
        column_metadata = {}
        MAX_UNIQUE_FOR_FULL_LIST = 30  # 전체 보기 제공 임계값
        TOP_N_FOR_MAPPING = 10  # 매핑 단계용 상위 N개
        
        for col_info in column_schema:
            col_name = col_info['column_name']
            unique_count = col_info['unique_count']
            
            col_metadata = {
                'type': col_info['type'],
                'unique_count': unique_count,
                'frequencies': col_info.get('value_frequencies', {})
            }
            
            # Categorical 컬럼의 경우 응답 값 처리
            if 'top_5' in col_info.get('value_frequencies', {}):
                col_data = df[col_name]
                value_counts = col_data.value_counts()
                total = col_info['value_frequencies']['total_responses']
                
                # 🎯 전략: unique 개수에 따라 처리 방식 결정
                if unique_count <= MAX_UNIQUE_FOR_FULL_LIST:
                    # ✅ 전체 보기 포함 (30개 이하)
                    col_metadata['all_responses'] = []
                    for value, count in value_counts.items():
                        percentage = (count / total * 100) if total > 0 else 0
                        col_metadata['all_responses'].append({
                            'value': str(value),
                            'count': int(count),
                            'percentage': round(percentage, 1)
                        })
                    col_metadata['has_full_list'] = True
                else:
                    # ⚠️ 너무 많으면 상위 20개만 + 경고
                    col_metadata['all_responses'] = []
                    for i, (value, count) in enumerate(value_counts.items()):
                        if i >= 20:
                            break
                        percentage = (count / total * 100) if total > 0 else 0
                        col_metadata['all_responses'].append({
                            'value': str(value),
                            'count': int(count),
                            'percentage': round(percentage, 1)
                        })
                    col_metadata['has_full_list'] = False
                    col_metadata['truncated_note'] = f"상위 20개만 표시 (전체 {unique_count}개)"
                
                # 매핑 단계용: 상위 10개만
                col_metadata['top_for_mapping'] = col_metadata['all_responses'][:TOP_N_FOR_MAPPING]
                # 호환성: 상위 5개
                col_metadata['top_responses_summary'] = col_metadata['all_responses'][:5]
            
            column_metadata[col_name] = col_metadata
        
        print(f"📈 메타데이터 생성 완료: {len(column_metadata)}개 컬럼")
        
        # ✨ csv_info 구조 개선
        csv_info = {
            'total_rows': len(df),
            'schema': column_schema,
            
            # 식별자 정보
            'identifier': {
                'column': detected_name_column,
                'mode': 'name' if detected_name_column else 'row_number',
                'available': bool(detected_name_column)
            },
            
            # 일정 정보
            'schedule': {
                'columns': detected_schedule_columns,
                'available': bool(detected_schedule_columns),
                'mode': 'schedule' if detected_schedule_columns else 'no_schedule'
            },
            
            # 레거시 호환성 유지
            'name_column': detected_name_column,
            'schedule_columns': detected_schedule_columns,
            'column_type_map': schema_analysis.get('column_type_map', {}),
            'has_name_column': bool(detected_name_column),
            'has_schedule_columns': bool(detected_schedule_columns),
            
            # ✨ 새로 추가: 컬럼별 메타데이터
            'column_metadata': column_metadata
        }
        
        # 분석 결과 로깅
        print(f"🎯 분석 완료 요약:")
        print(f"   - 식별자: {csv_info['identifier']['mode']} ({csv_info['identifier']['column'] or '행 번호'})")
        print(f"   - 일정 정보: {'있음' if csv_info['schedule']['available'] else '없음'} ({len(detected_schedule_columns)}개 컬럼)")
        print(f"   - 메타데이터: {len(column_metadata)}개 컬럼")
        
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

@screener_bp.route('/find-optimal-participants', methods=['POST'])
@tier_required(['free'])
def find_optimal_participants():
    """최적의 참여자 찾기: 변수 맵핑 + 기준 생성 + 점수 산정 + 최종 결과"""

    data = request.json
    csv_content = data.get('csv_content')
    plan_json = data.get('plan_json')
    csv_info = data.get('csv_info')
    sincerity_rules = data.get('sincerity_rules')
    
    if not all([csv_content, plan_json, csv_info, sincerity_rules]):
        def error_generate():
            yield f"data: {json.dumps({'error': '필수 데이터가 누락되었습니다.'})}\n\n"
        return Response(error_generate(), mimetype='text/event-stream')
    
    def generate():
        try:
            # 이미 밖에서 가져온 데이터 사용
            # 변수 맵핑
            if isinstance(plan_json, str):
                parsed_plan_json = json.loads(plan_json)
            else:
                parsed_plan_json = plan_json
            
            key_variables = parsed_plan_json.get('key_variables', [])
            balance_variables = parsed_plan_json.get('balance_variables', [])
            csv_columns = [col['column_name'] for col in csv_info['schema']]
            csv_schema = csv_info.get('schema', [])
            column_metadata = csv_info.get('column_metadata', {})  # ✨ 메타데이터 추출
            
            # ✨ CSV 스키마 + 메타데이터 함께 전달
            print(f"📊 변수 맵핑 시작: {len(column_metadata)}개 컬럼 메타데이터 포함")
            prompt = ScreenerPrompts.prompt_map_variables(key_variables, balance_variables, csv_columns, csv_schema, column_metadata)
            mapping_result = openai_service.generate_response(prompt, {"model": "gpt-4o"})
            mapping_analysis = parse_llm_json_response(mapping_result)

            key_variable_mappings = mapping_analysis.get('key_variable_mappings')
            if key_variable_mappings is None:
                key_variable_mappings = mapping_analysis.get('variable_mappings', [])
            balance_variable_mappings = mapping_analysis.get('balance_variable_mappings', [])

            # 호환성을 위해 필드 보정
            mapping_analysis['key_variable_mappings'] = key_variable_mappings
            mapping_analysis['balance_variable_mappings'] = balance_variable_mappings
            mapping_analysis['variable_mappings'] = key_variable_mappings
            
            # 1단계 완료
            yield f"data: {json.dumps({'step': 1, 'mapping_result': mapping_analysis})}\n\n"
            
            # 2단계: 기준 생성
            target_groups = parsed_plan_json.get('target_groups', [])
            variable_mappings = key_variable_mappings
            # ✨ CSV 스키마 + 메타데이터 함께 전달 (컬럼 값 확인용)
            csv_schema = csv_info.get('schema', [])
            print(f"🎯 스코어링 기준 생성: 메타데이터 포함 ({len(column_metadata)}개 컬럼)")
            prompt = ScreenerPrompts.prompt_create_scoring_criteria(target_groups, variable_mappings, csv_schema, column_metadata)
            # ⭐ Flash Thinking: 빠르면서도 복잡한 논리 처리 가능
            criteria_result = gemini_service.generate_response(prompt, {"temperature": 0.1,"max_output_tokens": 1048576},model_name="gemini-2.5-pro" )
            criteria_analysis = parse_llm_json_response(criteria_result)
            
            # ⭐ pandas 표현식 검증 (있는 경우)
            print("🔍 [Step 2] Pandas 표현식 검증 중...")
            for group in criteria_analysis.get('scoring_criteria', []):
                group_name = group.get('group_name', '')
                exclusive_traits = group.get('exclusive_traits', [])
                if exclusive_traits:
                    print(f"✅ {group_name} 배타적 특성: {exclusive_traits}")
                
                for logic in group.get('logic', []):
                    col_name = logic.get('column_name', '')
                    for rule in logic.get('rules', []):
                        expr = rule.get('pandas_expression', '')
                        if expr:
                            print(f"   검증: {col_name} → {expr[:60]}...")
                        else:
                            print(f"⚠️  경고: {col_name}에 pandas_expression 없음")
            
            # 2단계 완료
            yield f"data: {json.dumps({'step': 2, 'criteria_result': criteria_analysis})}\n\n"
            
            # 3단계: 점수 산정 (백엔드에서 계산)
            df = pd.read_csv(io.StringIO(csv_content))
            df = df.fillna('')
            
            # ⭐ 컬럼명 정규화: 2단계와 동일한 정규화 로직 사용 (통일)
            print(f"📌 [Step 3-4] 컬럼명 정규화 전: {list(df.columns[:3])}")
            
            # 정규화 전 name_column 저장
            original_name_column = csv_info.get('name_column')
            original_schedule_columns = csv_info.get('schedule_columns', [])
            
            df.columns = [normalize_column_name(col) for col in df.columns]
            print(f"📌 [Step 3-4] 컬럼명 정규화 후: {list(df.columns[:3])}")
            print(f"📌 [Step 3-4] 컬럼명 정규화 완료: {len(df.columns)}개 컬럼")
            
            # 🎯 csv_info의 name_column과 schedule_columns도 정규화 (2단계에서 이미 정규화되었지만 재정규화로 확실히)
            if original_name_column:
                normalized_name = normalize_column_name(original_name_column)
                csv_info['name_column'] = normalized_name
                if normalized_name != original_name_column:
                    print(f"✅ name_column 정규화: '{original_name_column}' → '{normalized_name}'")
            
            if original_schedule_columns:
                normalized_schedule = [normalize_column_name(col) for col in original_schedule_columns]
                csv_info['schedule_columns'] = normalized_schedule
                print(f"✅ schedule_columns 정규화: {len(normalized_schedule)}개")
            
            # ⭐ 핵심: 모든 행에 행 번호 기반 ID 생성 (1부터 시작)
            # 첫 번째 데이터 행이 ROW_1이 되도록 함
            df.reset_index(drop=True, inplace=True)  # 인덱스를 0부터 시작하도록 리셋
            df['participant_id'] = df.index.map(lambda idx: f'ROW_{idx + 1}')  # 1부터 시작
            
            # 원본 DataFrame 저장 (최종 데이터 복원용)
            df_original = df.copy()
            
            print(f"📌 총 {len(df)}명의 참여자 데이터 로드 완료 (participant_id: ROW_1 ~ ROW_{len(df)})")
            
            # ⭐ 핵심 변수 + 균형 변수 모두 추출 (스코어링용)
            key_mapped_columns = []
            for mapping in key_variable_mappings:
                mapped_col = mapping.get('mapped_column')
                if mapped_col and mapped_col in df.columns:
                    key_mapped_columns.append(mapped_col)
            
            balance_mapped_columns = []
            for mapping in balance_variable_mappings:
                mapped_col = mapping.get('mapped_column')
                if mapped_col and mapped_col in df.columns:
                    balance_mapped_columns.append(mapped_col)
            
            all_mapped_columns = key_mapped_columns + balance_mapped_columns
            
            print(f"📌 핵심 변수: {len(key_mapped_columns)}개 - {key_mapped_columns}")
            print(f"📌 균형 변수: {len(balance_mapped_columns)}개 - {balance_mapped_columns}")
            print(f"📌 전체 맵핑 변수: {len(all_mapped_columns)}개")
            
            # 모든 변수 + participant_id 포함한 DataFrame 생성 (스코어링용)
            df_mapped = df[['participant_id'] + all_mapped_columns].copy()
            
            group_criteria = criteria_analysis.get('scoring_criteria', [])
            
            # ⭐ 성실도 필터링 (별도 함수로 분리, LLM 재검증 제거)
            csv_schema = csv_info.get('schema', [])
            df = apply_sincerity_filter(df, sincerity_rules, csv_schema)
            
            # ⭐ df_mapped도 동일하게 필터링 (participant_id 기준)
            df_mapped = df_mapped[df_mapped['participant_id'].isin(df['participant_id'])].copy()
            
            print(f"📌 성실도 필터링 후: {len(df)}명 (df_mapped: {len(df_mapped)}명)")
            
            # 그룹별 스코어링 (df_mapped에서 수행)
            for group in group_criteria:
                group_name = group['group_name']
                df_mapped[f'{group_name}_score'] = 0
                
                for variable in group['logic']:
                    col_name = variable['column_name']
                    if col_name not in df_mapped.columns:
                        print(f"DEBUG: 컬럼 없음 - {col_name} (df_mapped 컬럼: {list(df_mapped.columns[:5])})")
                        continue
                        
                    col_data = df_mapped[col_name]
                    # ⭐ DataFrame이면 첫 번째 컬럼을 Series로 변환
                    if isinstance(col_data, pd.DataFrame):
                        print(f"⚠️ 경고: {col_name}이 DataFrame입니다. 첫 번째 컬럼을 사용합니다.")
                        col_data = col_data.iloc[:, 0]
                    # Series인지 확인
                    if not isinstance(col_data, pd.Series):
                        print(f"⚠️ 경고: {col_name}이 Series가 아닙니다. 타입: {type(col_data)}")
                        continue
                    
                    detected_type = detect_column_type(col_data)
                    
                    for rule in variable['rules']:
                        points_added = 0
                        
                        # ⭐ pandas_expression 우선 사용 (있는 경우)
                        pandas_expr = rule.get('pandas_expression', '')
                        if pandas_expr:
                            try:
                                # df를 df_mapped로 치환하여 표현식 실행
                                expr_for_eval = pandas_expr.replace("df[", "df_mapped[")
                                mask = eval(expr_for_eval)
                                df_mapped.loc[mask, f'{group_name}_score'] += rule['points']
                                points_added = mask.sum()
                                print(f"✅ {col_name} pandas_expression 사용: {points_added}명 매칭")
                            except Exception as e:
                                print(f"⚠️ pandas_expression 실행 실패: {pandas_expr}")
                                print(f"   에러: {e}")
                                # Fallback: 기존 로직 사용
                                pandas_expr = None
                        
                        # pandas_expression이 없거나 실패한 경우 기존 로직 사용
                        if not pandas_expr:
                            if variable['type'] == 'numerical':
                                min_val, max_val = rule['range']
                                try:
                                    numeric_data = pd.to_numeric(col_data, errors='coerce')
                                    mask = (numeric_data >= min_val) & (numeric_data <= max_val)
                                    df_mapped.loc[mask, f'{group_name}_score'] += rule['points']
                                    points_added = mask.sum()
                                    print(f"DEBUG: {col_name} numerical 범위 [{min_val}-{max_val}] 매칭: {points_added}명")
                                except Exception as e:
                                    print(f"DEBUG: numerical 처리 오류 - {col_name}: {e}")
                                    continue
                                    
                            elif variable['type'] == 'categorical' or variable['type'] == 'opentext':
                                target_value = str(rule.get('value', '')).strip()
                                match_mode = (rule.get('match_mode') or rule.get('match_type') or 'exact').lower()

                                # ⭐ col_data가 Series인지 확인 (DataFrame이면 에러 발생)
                                if not isinstance(col_data, pd.Series):
                                    print(f"⚠️ 오류: {col_name}이 Series가 아닙니다. 타입: {type(col_data)}")
                                    continue
                                
                                series = col_data.fillna('').astype(str).str.strip()
                                
                                if match_mode == 'contains':
                                    # 부분 일치 (다중 응답, 주관식)
                                    mask = series.str.contains(re.escape(target_value), na=False, case=False) if target_value else pd.Series([False] * len(series))
                                else:
                                    # 정확 일치 (단일 선택, 대소문자 무시)
                                    mask = series.str.lower() == target_value.lower()

                                df_mapped.loc[mask, f'{group_name}_score'] += rule['points']
                                points_added = int(mask.sum())
                                unique_vals = series.unique()[:5]
                                print(
                                    f"DEBUG: {col_name} {variable['type']} '{target_value}' (mode={match_mode}) 매칭: {points_added}명 (샘플: {list(unique_vals)})"
                                )
            
            # ⭐ 스코어링 결과 요약 (디버깅용)
            print("=" * 80)
            print("📊 스코어링 결과 요약")
            print("=" * 80)
            for group in group_criteria:
                group_name = group['group_name']
                score_col = f"{group_name}_score"
                if score_col in df_mapped.columns:
                    scores = df_mapped[score_col]
                    zero_count = (scores == 0).sum()
                    positive_count = (scores > 0).sum()
                    print(f"📌 {group_name}:")
                    print(f"   평균: {scores.mean():.1f}점")
                    print(f"   중간값: {scores.median():.1f}점")
                    print(f"   최소: {scores.min():.0f}점")
                    print(f"   최대: {scores.max():.0f}점")
                    print(f"   0점: {zero_count}명 ({zero_count / len(scores) * 100:.1f}%)")
                    print(f"   양수: {positive_count}명 ({positive_count / len(scores) * 100:.1f}%)")
                    if positive_count > 0:
                        positive_scores = scores[scores > 0]
                        print(f"   양수 평균: {positive_scores.mean():.1f}점")
                    print()
            print("=" * 80)
            
            # NaN을 빈 문자열로 변환하여 JSON 직렬화 오류 방지
            df_mapped = df_mapped.fillna('')
            
            # 3단계: 그룹별 상위 후보 추출하여 전송 (df_mapped에서 수행)
            scored_data_for_frontend = {}
            
            for group in target_groups:
                group_name = group['name']
                score_col = f"{group_name}_score"
                target_count = group.get('targetCount', 0)
                
                # ⭐ 최소 15명 또는 3배수 중 더 큰 값으로 샘플 추출
                min_count = max(15, target_count * 3)
                
                if score_col in df_mapped.columns:
                    top_participants = df_mapped.sort_values(by=score_col, ascending=False).head(min_count)
                    records = top_participants.to_dict('records')
                    scored_data_for_frontend[group_name] = records
                    print(f"DEBUG: {group_name} 후보 추출: {len(records)}명 (요청: 최소 {min_count}명, 맵핑된 열만 포함)")
            
            # 3단계 완료 - 그룹별 상위 3배수만 전송 + csv_info 포함
            yield f"data: {json.dumps({'step': 3, 'scored_data': scored_data_for_frontend, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"
            
            # 4단계: 최종 선별용 샘플 데이터 생성 (그룹별로 분리)
            # ⚠️ 최적화: participant_id + 균형 변수 + 점수만 전달 (핵심 변수는 이미 점수로 변환됨)
            sample_data = {}
            
            # 각 그룹별로 샘플 데이터 생성
            for group_name, group_records in scored_data_for_frontend.items():
                sample_data[group_name] = []
                
                for record in group_records:
                    participant_id = record.get('participant_id')  # 이미 ROW_1, ROW_2 형식으로 있음
                    
                    # 해당 그룹의 점수 가져오기
                    score_col = f"{group_name}_score"
                    score = float(record.get(score_col, 0))
                    
                    # ⭐ participant_id + 균형 변수만 포함 (핵심 변수 제외로 토큰 절약)
                    sample_item = {
                        'id': participant_id,
                        'score': score
                    }
                    
                    # 균형 변수만 추가
                    for balance_col in balance_mapped_columns:
                        if balance_col in record:
                            sample_item[balance_col] = str(record[balance_col])
                    
                    sample_data[group_name].append(sample_item)
            
            # 디버깅: 샘플 데이터 확인
            print("=" * 80)
            print("📊 LLM 전달 데이터 요약")
            print("=" * 80)
            print(f"📌 그룹 수: {len(sample_data)}")
            for group_name, records in sample_data.items():
                print(f"📌 {group_name}: {len(records)}명")
                if records:
                    first_record = records[0]
                    columns = [k for k in first_record.keys() if k not in ['id', 'score']]
                    print(f"   포함 컬럼: {columns} (균형 변수만)")
            print("=" * 80)
            
            # 각 그룹별 추출 인원수와 목표 인원수 계산
            group_targets_and_candidates = {}
            for group in target_groups:
                group_name = group['name']
                target_count = group.get('targetCount', 0)
                candidate_count = len(sample_data.get(group_name, []))
                group_targets_and_candidates[group_name] = {
                    'target_count': target_count,
                    'candidate_count': candidate_count
                }
            
            prompt = ScreenerPrompts.prompt_final_selection(
                target_groups=target_groups,
                scored_data_sample=sample_data,
                balance_variables_json=json.dumps(parsed_plan_json.get('balance_variables', []), ensure_ascii=False, indent=2),
                group_targets_and_candidates=group_targets_and_candidates
            )
            
            print(f"DEBUG: Step 4 - LLM 호출 시작 (프롬프트 길이: {len(prompt)}자, 후보 총 {sum(len(v) for v in sample_data.values())}명)")
            final_result = gemini_service.generate_response(
                prompt,
                generation_config={"temperature": 0.1,"max_output_tokens": 1048576},
                model_name="gemini-2.5-flash-lite"
            )
            print(f"DEBUG: Step 4 - LLM 응답 완료 (응답 길이: {len(final_result.get('content', '')) if final_result.get('success') else 0}자)")
            
            # 4단계 완료 - JSON 파싱 (간소화)
            if isinstance(final_result, dict) and final_result.get('content'):
                try:
                    content = final_result['content']
                    
                    # 1️⃣ 코드 블록 제거
                    if '```json' in content:
                        content = content.split('```json')[1].split('```')[0]
                    elif '```' in content:
                        content = content.split('```')[1].split('```')[0]
                    
                    # 2️⃣ JSON 시작 위치 찾기 (첫 번째 { 또는 [)
                    first_brace = content.find('{')
                    first_bracket = content.find('[')
                    if first_brace != -1 and first_bracket != -1:
                        json_start_pos = min(first_brace, first_bracket)
                    elif first_brace != -1:
                        json_start_pos = first_brace
                    elif first_bracket != -1:
                        json_start_pos = first_bracket
                    else:
                        json_start_pos = 0
                    
                    if json_start_pos > 0:
                        content = content[json_start_pos:]
                    
                    # 3️⃣ 제어 문자 제거
                    content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content).strip()
                    
                    # 4️⃣ JSON 파싱 (실패 시 바로 에러)
                    parsed_json = json.loads(content)
                    
                    # 5️⃣ 중복 제거 (간소화): 한 참가자는 최고 점수 그룹에만 배정
                    seen = set()
                    removed_count = 0
                    for group in parsed_json.get('recommendations', []):
                        valid = []
                        for p in group.get('participants', []):
                            if p['id'] not in seen:
                                seen.add(p['id'])
                                valid.append(p)
                            else:
                                removed_count += 1
                        group['participants'] = valid
                    
                    if removed_count > 0:
                        print(f"⚠️ 중복 참가자 제거: {removed_count}명 (LLM이 중복 추천함)")
                    
                    # ⭐ 선별된 participant_id들을 먼저 수집
                    selected_participant_ids = []
                    participant_id_to_group_info = {}  # participant_id -> {group_name, reason, score}
                    
                    for group in parsed_json.get('recommendations', []):
                        group_name = group.get('group_name', '')
                        for participant in group.get('participants', []):
                            pid = participant.get('id')  # 'ROW_1', 'ROW_5' 등
                            
                            if not pid:
                                continue
                            
                            selected_participant_ids.append(pid)
                            participant_id_to_group_info[pid] = {
                                'group_name': group_name,
                                'reason': participant.get('reason', ''),
                                'score': participant.get('score', 0)
                            }
                    
                    # ⭐ participant_id 배열로 df_original에서 한번에 필터링 (조인 대신)
                    if selected_participant_ids:
                        filtered_df = df_original[df_original['participant_id'].isin(selected_participant_ids)].copy()
                        
                        # DataFrame을 dict 리스트로 변환
                        final_participants_data = filtered_df.to_dict('records')
                        
                        # 각 참여자에 그룹 정보와 display_name 추가
                        name_col = csv_info.get('name_column')
                        # ⭐ 실제 DataFrame의 컬럼명과 매칭
                        if final_participants_data:
                            matched_name_col = find_matching_column_name(name_col, final_participants_data[0])
                            if matched_name_col and matched_name_col != name_col:
                                print(f"🔍 name_column 매칭: '{name_col}' → '{matched_name_col}'")
                                name_col = matched_name_col
                        
                        for participant_data in final_participants_data:
                            pid = participant_data.get('participant_id')
                            group_info = participant_id_to_group_info.get(pid, {})
                            
                            # display_name 설정
                            display_name = pid  # 기본값
                            if name_col and name_col in participant_data:
                                name_value = participant_data[name_col]
                                if name_value is not None and str(name_value).strip():
                                    display_name = str(name_value).strip()
                            
                            participant_data['_display_name'] = display_name
                            participant_data['_selection_reason'] = group_info.get('reason', '')
                            participant_data['_assigned_group'] = group_info.get('group_name', '')
                            participant_data['_group_score'] = group_info.get('score', 0)
                    else:
                        final_participants_data = []
                        print(f"⚠️ WARNING: 선별된 participant_id가 없습니다.")
                    
                    print(f"📌 최종 선별 완료: {len(final_participants_data)}명의 참여자 데이터 복원 완료")

                    # 최종 결과에 participants_data 추가
                    result_data = {
                        'step': 4, 
                        'final_selection': parsed_json, 
                        'participants_data': final_participants_data,
                        'csv_info': csv_info
                    }
                    yield f"data: {json.dumps(result_data, ensure_ascii=False)}\n\n"
                except json.JSONDecodeError as e:
                    print(f"JSON 파싱 오류: {e}")
                    # 원문 일부만 포함 (너무 길면 잘라내기)
                    raw_content = final_result.get('content', '')[:500]
                    # 제어 문자 제거
                    raw_content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', raw_content)
                    error_msg = f'JSON 파싱 실패: {str(e)}'
                    yield f"data: {json.dumps({'step': 4, 'error': error_msg, 'final_selection': {'error': error_msg}, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'step': 4, 'final_selection': final_result, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e), 'step': 'error'})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

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
    
    # ⭐ 디버깅 로그: 일정 최적화 입력 데이터 확인
    print("=" * 80)
    print("🔍 [optimize-schedule] 입력 데이터 확인")
    print("=" * 80)
    print(f"📌 name_column: {name_column}")
    print(f"📅 schedule_columns: {schedule_columns}")
    print(f"👥 participants_data 개수: {len(participants_data)}명")
    if participants_data:
        sample = participants_data[0]
        print(f"📋 첫 번째 참여자 샘플 (키 목록): {list(sample.keys())}")
        # ⭐ 실제 컬럼명과 매칭
        matched_name_col = find_matching_column_name(name_column, sample) if name_column else None
        if matched_name_col and matched_name_col in sample:
            name_val = sample.get(matched_name_col)
            if matched_name_col != name_column:
                print(f"🔍 name_column 매칭: '{name_column}' → '{matched_name_col}'")
            print(f"✅ name_column '{matched_name_col}' 값: '{name_val}' (타입: {type(name_val)}, 길이: {len(str(name_val)) if name_val else 0})")
            if (not name_val or str(name_val).strip() == '') and not has_name_column:
                print(f"⚠️ 경고: name_column 값이 비어있음! participant_id를 대신 사용합니다.")
        else:
            print(f"❌ name_column '{name_column}'이 데이터에 없음 (매칭 결과: '{matched_name_col}')")
        if schedule_columns:
            for col in schedule_columns[:2]:  # 처음 2개만 출력
                if col in sample:
                    print(f"✅ schedule_column '{col}' 값: {sample.get(col)}")
                else:
                    print(f"❌ schedule_column '{col}'이 데이터에 없음")
    print("=" * 80)

    try:
        availability_data = []
        required_from_data = []

        for participant in participants_data:
            if not isinstance(participant, dict):
                continue

            pid = str(participant.get('participant_id') or participant.get('id') or participant.get('participantId') or '')
            if not pid:
                continue

            # ✨ 1순위: 4단계에서 생성한 _display_name 사용
            display_name = ''
            if '_display_name' in participant and participant['_display_name']:
                display_name = str(participant['_display_name']).strip()
            # 2순위: name_column에서 직접 추출
            elif name_column:
                # ⭐ 실제 컬럼명과 매칭
                matched_name_col = find_matching_column_name(name_column, participant)
                if matched_name_col and matched_name_col in participant:
                    name_value = str(participant.get(matched_name_col)).strip()
                    # 빈 문자열이 아닌 실제 값이 있을 때만 사용
                    if name_value and name_value != '':
                        display_name = name_value
                    else:
                        # 컬럼은 있지만 값이 비어있으면 participant_id 사용
                        display_name = pid
                else:
                    # 최종 fallback: participant_id 사용
                    display_name = pid
            else:
                # 최종 fallback: participant_id 사용
                display_name = pid

            availability = []
            for col in schedule_columns:
                raw_value = participant.get(col)
                if not raw_value:
                    continue

                if isinstance(raw_value, list):
                    values = [str(v).strip() for v in raw_value if v]
                else:
                    values = [s.strip() for s in re.split(r'[\n;,]+', str(raw_value)) if s.strip()]

                for value in values:
                    availability.append({
                        'schedule_column': col,
                        'schedule_value': value
                    })

            availability_data.append({
                'participant_id': pid,
                'participant_name': display_name,
                'assigned_group': participant.get('_assigned_group') or participant.get('group_name'),
                'is_user_selected': bool(participant.get('_selection_status') == 'user_selected' or participant.get('_is_user_selected')), 
                'availability': availability
            })

            if availability_data[-1]['is_user_selected']:
                required_from_data.append(display_name)

        if not availability_data:
            return jsonify({'success': False, 'error': '일정 정보가 있는 참여자를 찾을 수 없습니다.'}), 400

        merged_required = []
        for name in list(required_participants) + required_from_data:
            if name and name not in merged_required:
                merged_required.append(name)

        context_info = {
            'study_id': study_id,
            'availability_data': availability_data,
            'target_groups': target_groups,
            'required_participants': merged_required,
            'schedule_columns': schedule_columns
        }

        # ✨ 참여자 수를 미리 계산해서 프롬프트에 전달
        total_participants = len(availability_data)
        print(f"=" * 80)
        print(f"📅 일정 최적화 시작")
        print(f"=" * 80)
        print(f"📌 총 참여자 수: {total_participants}명")
        print(f"📌 필수 포함 인원: {len(merged_required)}명 - {merged_required}")
        print(f"📌 일정 컬럼: {schedule_columns}")
        print(f"=" * 80)
        
        prompt = ScreenerPrompts.prompt_schedule_optimization_with_context(
            json.dumps(context_info, ensure_ascii=False, indent=2),
            total_participants
        )
        result = openai_service.generate_response(prompt, {"temperature": 0.1})
        optimized_schedule = parse_llm_json_response(result)

        if not isinstance(optimized_schedule, dict):
            raise ValueError('LLM이 올바른 JSON을 반환하지 않았습니다.')

        schedule_assignments = optimized_schedule.get('schedule_assignments', {})
        unassigned_participants = optimized_schedule.get('unassigned_participants', []) or []

        # ✨ 슬롯 수와 배정된 참여자 검증
        assigned_names = set()
        total_slots = 0
        slot_details = []  # 디버깅용
        
        for date_key, day_assignments in schedule_assignments.items():
            if isinstance(day_assignments, dict):
                for time_slot, participants_list in day_assignments.items():
                    if time_slot == 'weekday':
                        continue
                    
                    # 슬롯 카운트
                    if isinstance(participants_list, list):
                        slot_count = len(participants_list)
                        total_slots += slot_count
                        assigned_names.update([str(p) for p in participants_list])
                        
                        # 디버깅: 슬롯에 여러 명이 있는지 체크
                        if slot_count > 1:
                            slot_details.append(f"{date_key} {time_slot}: {slot_count}명 - {participants_list}")
                    elif isinstance(participants_list, str):
                        total_slots += 1
                        assigned_names.add(participants_list)

        all_participant_names = set([entry.get('participant_name', '') for entry in availability_data])
        missing_participants = all_participant_names - assigned_names - set(unassigned_participants)

        # ✨ 검증 결과 출력
        print(f"=" * 80)
        print(f"📊 일정 최적화 결과 검증")
        print(f"=" * 80)
        print(f"📌 목표 참여자 수: {total_participants}명")
        print(f"📌 생성된 총 슬롯 수: {total_slots}개")
        print(f"📌 배정된 참여자 수: {len(assigned_names)}명")
        print(f"📌 미배정 참여자 수: {len(unassigned_participants)}명")
        print(f"📌 누락된 참여자 수: {len(missing_participants)}명")
        
        if total_slots != total_participants:
            print(f"⚠️ ❌ 슬롯 수 불일치! 목표: {total_participants}개, 실제: {total_slots}개 (차이: {total_slots - total_participants})")
        else:
            print(f"✅ 슬롯 수 일치: {total_slots}개")
        
        if slot_details:
            print(f"⚠️ ❌ 여러 명이 배정된 슬롯 발견:")
            for detail in slot_details:
                print(f"   - {detail}")
        else:
            print(f"✅ 모든 슬롯에 1명씩 배정됨")
        
        print(f"=" * 80)

        if missing_participants:
            print(f"⚠️ 경고: {len(missing_participants)}명의 참여자가 일정에 배정되지 않았습니다: {missing_participants}")

        if unassigned_participants:
            print(f"⚠️ 경고: {len(unassigned_participants)}명의 참여자가 unassigned로 표시되었습니다: {unassigned_participants}")

        validation_data = {
            'total_participants': len(all_participant_names),
            'total_slots': total_slots,  # ✨ 실제 생성된 슬롯 수
            'assigned_count': len(assigned_names),
            'unassigned_count': len(unassigned_participants),
            'missing_count': len(missing_participants),
            'slot_mismatch': total_slots != total_participants,  # ✨ 슬롯 수 불일치 여부
            'multi_person_slots': slot_details,  # ✨ 여러 명이 배정된 슬롯 목록
            'unassigned_participants': list(unassigned_participants),
            'missing_participants': list(missing_participants)
        }

        return jsonify({
            'success': True,
            'optimized_schedule': optimized_schedule,
            'validation': validation_data
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


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

        # Step 2에서 LLM이 감지한 컬럼 정보 사용 (재검증 없음)
        name_column = csv_info.get('name_column') or data.get('name_column')
        schedule_columns = csv_info.get('schedule_columns') or data.get('schedule_columns') or []
        contact_columns = csv_info.get('contact_columns') or data.get('contact_columns') or []

        has_name_column_flag = csv_info.get('has_name_column')
        has_name_column = bool(has_name_column_flag) if has_name_column_flag is not None else bool(name_column)

        if isinstance(schedule_columns, str):
            schedule_columns = [schedule_columns]
        if isinstance(contact_columns, str):
            contact_columns = [contact_columns]
        
        # ⭐ 디버깅 로그: 5단계 입력 데이터 확인
        print("=" * 80)
        print("🔍 [Step 5: finalize-participants] 입력 데이터 확인")
        print("=" * 80)
        print(f"📊 csv_info: {csv_info}")
        print(f"📌 name_column: {name_column}")
        print(f"📅 schedule_columns: {schedule_columns}")
        print(f"👥 participants_data 개수: {len(participants_data)}명")
        if participants_data:
            sample = participants_data[0]
            print(f"📋 첫 번째 참여자 샘플 (키 목록): {list(sample.keys())}")
            # ⭐ 실제 컬럼명과 매칭
            matched_name_col = find_matching_column_name(name_column, sample) if name_column else None
            if matched_name_col and matched_name_col in sample:
                name_val = sample.get(matched_name_col)
                if matched_name_col != name_column:
                    print(f"🔍 name_column 매칭: '{name_column}' → '{matched_name_col}'")
                print(f"✅ name_column '{matched_name_col}' 값: '{name_val}' (타입: {type(name_val)}, 길이: {len(str(name_val)) if name_val else 0})")
                if (not name_val or str(name_val).strip() == '') and not has_name_column:
                    print(f"⚠️ 경고: name_column 값이 비어있음! participant_id를 대신 사용합니다.")
            else:
                print(f"❌ name_column '{name_column}'이 데이터에 없음 (매칭 결과: '{matched_name_col}')")
            if schedule_columns:
                for col in schedule_columns[:2]:  # 처음 2개만 출력
                    if col in sample:
                        print(f"✅ schedule_column '{col}' 값: {sample.get(col)}")
                    else:
                        print(f"❌ schedule_column '{col}'이 데이터에 없음")
        print("=" * 80)

        balance_variables = plan_json.get('balance_variables', []) if isinstance(plan_json, dict) else []
        balance_variables_json = json.dumps(balance_variables, ensure_ascii=False, indent=2)

        # 타겟 그룹 정보 정리
        group_info_map = {
            grp.get('name'): grp for grp in target_groups
            if isinstance(grp, dict) and grp.get('name')
        }
        ordered_group_names = [grp_name for grp_name in group_info_map.keys()]
        default_group_name = ordered_group_names[0] if ordered_group_names else 'Unassigned'

        def compute_display_name(row: dict) -> str:
            """Step 2 감지 결과를 우선 활용"""
            if name_column:
                # ⭐ 실제 컬럼명과 매칭
                matched_name_col = find_matching_column_name(name_column, row)
                if matched_name_col and matched_name_col in row:
                    raw_value = row.get(matched_name_col)
                    if raw_value is not None:
                        name_value = str(raw_value).strip()
                        if name_value:
                            return name_value
                        if has_name_column:
                            return ''
            if has_name_column:
                return ''
            return str(row.get('participant_id', ''))

        def coerce_score(row: dict, group_name: str) -> float:
            potential_keys = [
                '_group_score',
                f'{group_name}_score',
                '_score',
                'score'
            ]
            for key in potential_keys:
                if key in row and row[key] not in (None, ''):
                    try:
                        return float(row[key])
                    except (ValueError, TypeError):
                        continue
            return 0.0

        participants_map = {}
        participants_by_group = defaultdict(list)
        selected_by_group = defaultdict(list)

        for participant in participants_data:
            if not isinstance(participant, dict):
                continue

            participant_copy = copy.deepcopy(participant)
            pid = str(
                participant_copy.get('participant_id')
                or participant_copy.get('id')
                or participant_copy.get('participantId')
                or ''
            )

            if not pid:
                continue

            participant_copy['participant_id'] = pid
            assigned_group = (
                participant_copy.get('_assigned_group')
                or participant_copy.get('assigned_group')
                or participant_copy.get('group_name')
            )
            if not assigned_group:
                assigned_group = default_group_name
            participant_copy['_assigned_group'] = assigned_group

            is_user_selected = pid in selected_ids or participant_copy.get('_is_selected') is True
            participant_copy['_is_user_selected'] = is_user_selected
            participant_copy['_group_score'] = coerce_score(participant_copy, assigned_group)
            participant_copy['_display_name'] = compute_display_name(participant_copy)

            if schedule_columns:
                schedule_values = {}
                for col in schedule_columns:
                    val = participant_copy.get(col)
                    if val not in (None, ''):
                        schedule_values[col] = str(val)
                if schedule_values:
                    participant_copy['_schedule_values'] = schedule_values

            if contact_columns:
                contact_values = {}
                for col in contact_columns:
                    val = participant_copy.get(col)
                    if val not in (None, ''):
                        contact_values[col] = str(val)
                if contact_values:
                    participant_copy['_contact_values'] = contact_values

            participants_map[pid] = participant_copy
            participants_by_group[assigned_group].append(participant_copy)

            if is_user_selected:
                selected_by_group[assigned_group].append({
                    'id': pid,
                    'name': participant_copy['_display_name']
                })

        # ⭐ 균형 변수 추출 (plan_json에서)
        balance_variables = plan_json.get('balance_variables', [])
        balance_column_names = []
        # balance_variables는 매핑 정보가 없으므로, 이름으로 추정
        # 더 정확하게 하려면 매핑 결과를 저장해둬야 하지만, 여기서는 간단히 처리
        # (실제로는 이미 participants_data에 균형 변수가 포함되어 있으므로, plan_json의 variable_name 기반으로 추출)
        
        # LLM 입력용 데이터 구성
        group_targets_and_candidates = {}
        scored_data_sample = {}
        for group_name, participants_list in participants_by_group.items():
            target_count = group_info_map.get(group_name, {}).get('targetCount')
            selected_count = len(selected_by_group.get(group_name, []))
            non_selected_candidates = [p for p in participants_list if not p.get('_is_user_selected')]

            group_targets_and_candidates[group_name] = {
                'target_count': target_count if target_count is not None else len(participants_list),
                'selected_count': selected_count,
                'remaining_target': max((target_count or 0) - selected_count, 0) if target_count is not None else 0,
                'candidate_count': len(non_selected_candidates)
            }

            group_samples = []
            for participant_copy in participants_list:
                pid = participant_copy['participant_id']
                record = {
                    'id': pid,
                    'score': float(participant_copy.get('_group_score') or 0.0),
                    'is_user_selected': bool(participant_copy.get('_is_user_selected')),
                    'assigned_group_initial': group_name
                }

                # ⚠️ 최적화: 균형 변수만 포함 (핵심 변수는 이미 점수로 변환됨)
                # balance_variables의 variable_name과 유사한 컬럼명을 찾아 포함
                for key, value in participant_copy.items():
                    if key in ['participant_id', '_group_score', '_selection_reason', '_assigned_group', '_is_selected', '_is_user_selected', '_display_name', '_schedule_values', '_contact_values']:
                        continue
                    if key.startswith('_'):
                        continue
                    if isinstance(value, (dict, list)):
                        continue
                    
                    # ⭐ 균형 변수인지 확인 (variable_name 또는 컬럼명에 균형 변수 키워드 포함)
                    is_balance = False
                    for balance_var in balance_variables:
                        balance_var_name = balance_var.get('variable_name', '').lower()
                        balance_var_desc = balance_var.get('description', '').lower()
                        if balance_var_name in key.lower() or balance_var_desc in key.lower():
                            is_balance = True
                            break
                    
                    if is_balance:
                        record[key] = str(value)

                # 일정 컬럼은 항상 포함 (일정 최적화용)
                if schedule_columns:
                    for col in schedule_columns:
                        if participant_copy.get(col) not in (None, ''):
                            record[col] = str(participant_copy.get(col))

                # 연락처 컬럼도 포함 (필수)
                if contact_columns:
                    for col in contact_columns:
                        if participant_copy.get(col) not in (None, ''):
                            record[col] = str(participant_copy.get(col))

                group_samples.append(record)

            scored_data_sample[group_name] = group_samples

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
                prompt,
                generation_config={"temperature": 0.1 })

            if not final_result.get('success'):
                raise ValueError('LLM 호출 실패')

            # JSON 파싱 (간소화)
            content = final_result.get('content', '')
            
            # 코드 블록 제거
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            
            # JSON 시작 위치 찾기
            first_brace = content.find('{')
            if first_brace > 0:
                content = content[first_brace:]
            
            # 제어 문자 제거 및 파싱
            content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content).strip()
            final_selection_payload = json.loads(content)
            llm_success = True
        except Exception as llm_error:
            print(f"⚠️ LLM 최종 선정 실패, 점수 기반으로 대체합니다: {llm_error}")
            traceback.print_exc()

        groups_output = []
        final_participants_flat = []
        reserve_participants_flat = []

        if llm_success and isinstance(final_selection_payload, dict):
            recommendations = final_selection_payload.get('recommendations', [])
            final_ids = set()
            
            # ⭐ 먼저 모든 그룹의 이미 선택된 참여자를 수집 (절대 보존)
            all_selected_by_group = {}
            for group_name, selected_list in selected_by_group.items():
                all_selected_by_group[group_name] = []
                for selected_info in selected_list:
                    pid = str(selected_info.get('id'))
                    if pid and pid in participants_map:
                        all_selected_by_group[group_name].append(pid)
                        final_ids.add(pid)
            
            # ⭐ 디버깅: 이미 선택된 참여자 확인
            print("=" * 80)
            print("🔍 [Step 5] 이미 선택된 참여자 확인")
            print("=" * 80)
            for group_name, selected_pids in all_selected_by_group.items():
                print(f"📌 그룹 '{group_name}': 이미 선택된 참여자 {len(selected_pids)}명 - {selected_pids[:5]}{'...' if len(selected_pids) > 5 else ''}")
            print("=" * 80)

            # LLM이 반환한 그룹 목록을 기반으로 처리
            llm_group_names = {group.get('group_name', '') or default_group_name for group in recommendations}
            
            # 모든 그룹에 대해 처리 (LLM이 반환하지 않은 그룹도 포함)
            all_group_names = set(participants_by_group.keys()) | llm_group_names
            
            for group_name in all_group_names:
                group_target = group_info_map.get(group_name, {}).get('targetCount')
                selected_list = []
                
                # ⭐ 1단계: 이미 선택된 참여자를 먼저 추가 (절대 필수!)
                group_selected_pids = all_selected_by_group.get(group_name, [])
                for pid in group_selected_pids:
                    if pid in final_ids:  # 중복 체크 (다른 그룹에 이미 추가되었을 수 있음)
                        # 이미 다른 그룹에 추가되었으면 건너뛰기
                        continue
                    
                    participant_data = participants_map.get(pid)
                    if not participant_data:
                        continue
                    
                    participant_copy = copy.deepcopy(participant_data)
                    participant_copy['_assigned_group'] = group_name
                    participant_copy['_selection_status'] = 'user_selected'
                    selected_list.append(participant_copy)
                    final_ids.add(pid)
                
                # ⭐ 2단계: LLM이 반환한 참여자 중 이미 선택되지 않은 것만 추가
                # 목표 인원에 도달할 때까지 추가
                remaining_needed = max((group_target or 0) - len(selected_list), 0) if group_target is not None else 0
                
                # LLM이 반환한 그룹 정보 찾기
                llm_group = next((g for g in recommendations if (g.get('group_name', '') or default_group_name) == group_name), None)
                
                if llm_group:
                    for participant_entry in llm_group.get('participants', []):
                        if remaining_needed <= 0:
                            break  # 목표 인원 달성
                        
                        pid = str(participant_entry.get('id'))
                        if not pid or pid in final_ids:  # 이미 추가된 참여자는 제외
                            continue
                        
                        participant_data = participants_map.get(pid)
                        if not participant_data:
                            print(f"⚠️ LLM이 반환한 ID를 찾을 수 없습니다: {pid}")
                            continue

                        participant_copy = copy.deepcopy(participant_data)
                        participant_copy['_assigned_group'] = group_name
                        participant_copy['_selection_status'] = 'auto_selected'
                        participant_copy['_selection_reason'] = participant_entry.get('reason', participant_copy.get('_selection_reason', ''))
                        participant_copy['_group_score'] = participant_entry.get('score', participant_copy.get('_group_score', 0))

                        selected_list.append(participant_copy)
                        final_ids.add(pid)
                        remaining_needed -= 1
                
                # ⭐ LLM이 목표 인원을 채우지 못한 경우 상세 경고
                if remaining_needed > 0:
                    print("=" * 80)
                    print(f"⚠️ [경고] 그룹 '{group_name}' 목표 인원 미달")
                    print("=" * 80)
                    print(f"   목표 인원: {group_target}명")
                    print(f"   현재 선정: {len(selected_list)}명 (부족: {remaining_needed}명)")
                    if llm_group:
                        llm_participants_count = len(llm_group.get('participants', []))
                        llm_auto_selected_count = len([p for p in llm_group.get('participants', []) if not p.get('is_selected', False)])
                        print(f"   LLM 응답: participants 배열 길이 {llm_participants_count}명 (is_selected: false인 참여자 {llm_auto_selected_count}명)")
                        print(f"   → LLM이 '추가_선정_필요' 수치({group_target - len([p for p in selected_list if p.get('_selection_status') == 'user_selected'])})를 정확히 채우지 못했습니다.")
                    else:
                        print(f"   → LLM이 이 그룹에 대한 응답을 반환하지 않았습니다.")
                    print(f"   → 프롬프트를 확인하거나 LLM 응답을 검증하세요.")
                    print("=" * 80)
                
                # ⭐ 디버깅: 그룹별 선정 결과 확인
                user_selected_count = len([p for p in selected_list if p.get('_selection_status') == 'user_selected'])
                auto_selected_count = len([p for p in selected_list if p.get('_selection_status') == 'auto_selected'])
                print(f"📊 그룹 '{group_name}': 목표 {group_target}명, 선정 {len(selected_list)}명 (이미 선택: {user_selected_count}명, 추가 선정: {auto_selected_count}명)")
                
                final_participants_flat.extend(selected_list)

                # 예약자 목록 구성
                reserve_list = []
                for candidate in participants_by_group.get(group_name, []):
                    pid = candidate.get('participant_id')
                    if pid in final_ids:
                        continue
                    reserve_copy = copy.deepcopy(candidate)
                    reserve_copy['_selection_status'] = 'reserve'
                    reserve_list.append(reserve_copy)

                reserve_participants_flat.extend(reserve_list)

                groups_output.append({
                    'group_name': group_name,
                    'target_count': group_target if group_target is not None else len(selected_list),
                    'user_selected_count': user_selected_count,
                    'auto_selected_count': auto_selected_count,
                    'reserve_count': len(reserve_list),
                    'overflow_count': max(len(selected_list) - (group_target or len(selected_list)), 0) if group_target is not None else 0,
                    'remaining_slots': max((group_target or len(selected_list)) - len(selected_list), 0) if group_target is not None else 0,
                    'final_participants': selected_list,
                    'reserve_participants': reserve_list
                })

            summary = {
                'total_input_participants': len(participants_data),
                'total_groups': len(groups_output),
                'total_final_participants': len(final_participants_flat),
                'total_user_selected': len([p for p in final_participants_flat if p.get('_selection_status') == 'user_selected']),
                'total_auto_selected': len([p for p in final_participants_flat if p.get('_selection_status') == 'auto_selected']),
                'total_reserve_participants': len(reserve_participants_flat),
                'groups_with_shortage': [
                    {
                        'group_name': group['group_name'],
                        'remaining_slots': group['remaining_slots']
                    }
                    for group in groups_output
                    if group.get('remaining_slots')
                ],
                'groups_with_overflow': [
                    {
                        'group_name': group['group_name'],
                        'overflow_count': group['overflow_count']
                    }
                    for group in groups_output
                    if group.get('overflow_count')
                ]
            }
            
            # ⭐ 디버깅 로그: 5단계 출력 데이터 확인 (LLM 성공)
            print("=" * 80)
            print("✅ [Step 5: finalize-participants] LLM 기반 최종 선정 완료 - 응답 데이터 확인")
            print("=" * 80)
            print(f"📌 응답 name_column: {name_column}")
            print(f"📅 응답 schedule_columns: {schedule_columns}")
            print(f"👥 final_participants 개수: {len(final_participants_flat)}명")
            if final_participants_flat:
                sample = final_participants_flat[0]
                print(f"📋 첫 번째 참여자 샘플 (키 목록): {list(sample.keys())}")
                # ⭐ 실제 컬럼명과 매칭
                matched_name_col = find_matching_column_name(name_column, sample) if name_column else None
                if matched_name_col and matched_name_col in sample:
                    if matched_name_col != name_column:
                        print(f"🔍 name_column 매칭: '{name_column}' → '{matched_name_col}'")
                    print(f"✅ name_column '{matched_name_col}' 값: {sample.get(matched_name_col)}")
                else:
                    print(f"❌ name_column '{name_column}'이 데이터에 없음 (매칭 결과: '{matched_name_col}')")
                if schedule_columns:
                    for col in schedule_columns[:2]:  # 처음 2개만 출력
                        if col in sample:
                            print(f"✅ schedule_column '{col}' 값: {sample.get(col)}")
                        else:
                            print(f"❌ schedule_column '{col}'이 데이터에 없음")
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

        # LLM 실패 시 점수 기반 로직으로 대체
        groups_candidates = participants_by_group
        groups_output = []
        final_participants_flat = []
        reserve_participants_flat = []
        total_user_selected = 0
        total_auto_selected = 0

        for group_name in list(dict.fromkeys(ordered_group_names + list(groups_candidates.keys()))):
            candidates = groups_candidates.get(group_name, [])
            if not candidates:
                continue

            candidates.sort(
                key=lambda p: (
                    int(bool(p.get('_is_user_selected'))),
                    float(p.get('_group_score') or 0.0)
                ),
                reverse=True
            )

            group_info = group_info_map.get(group_name, {})
            target_count = group_info.get('targetCount')
            if target_count is None:
                target_count = len(candidates)

            user_selected = [copy.deepcopy(p) for p in candidates if p.get('_is_user_selected')]
            auto_candidates = [copy.deepcopy(p) for p in candidates if not p.get('_is_user_selected')]

            total_user_selected += len(user_selected)

            needed_auto = max(target_count - len(user_selected), 0)
            auto_selected = auto_candidates[:needed_auto]
            reserve_list = auto_candidates[needed_auto:]
            total_auto_selected += len(auto_selected)

            for participant_copy in user_selected:
                participant_copy['_selection_status'] = 'user_selected'

            for participant_copy in auto_selected:
                participant_copy['_selection_status'] = 'auto_selected'

            for participant_copy in reserve_list:
                participant_copy['_selection_status'] = 'reserve'

            final_list = user_selected + auto_selected

            groups_output.append({
                'group_name': group_name,
                'target_count': target_count,
                'user_selected_count': len(user_selected),
                'auto_selected_count': len(auto_selected),
                'reserve_count': len(reserve_list),
                'overflow_count': max(len(user_selected) - target_count, 0),
                'remaining_slots': max(target_count - len(final_list), 0),
                'final_participants': final_list,
                'reserve_participants': reserve_list
            })

            final_participants_flat.extend(final_list)
            reserve_participants_flat.extend(reserve_list)

        summary = {
            'total_input_participants': len(participants_data),
            'total_groups': len(groups_output),
            'total_final_participants': len(final_participants_flat),
            'total_user_selected': total_user_selected,
            'total_auto_selected': total_auto_selected,
            'total_reserve_participants': len(reserve_participants_flat),
            'groups_with_shortage': [
                {
                    'group_name': group['group_name'],
                    'remaining_slots': group['remaining_slots']
                }
                for group in groups_output
                if group.get('remaining_slots')
            ],
            'groups_with_overflow': [
                {
                    'group_name': group['group_name'],
                    'overflow_count': group['overflow_count']
                }
                for group in groups_output
                if group.get('overflow_count')
            ]
        }
        
        # ⭐ 디버깅 로그: 5단계 출력 데이터 확인 (Fallback: 점수 기반)
        print("=" * 80)
        print("⚠️ [Step 5: finalize-participants] Fallback 점수 기반 선정 완료 - 응답 데이터 확인")
        print("=" * 80)
        print(f"📌 응답 name_column: {name_column}")
        print(f"📅 응답 schedule_columns: {schedule_columns}")
        print(f"👥 final_participants 개수: {len(final_participants_flat)}명")
        if final_participants_flat:
            sample = final_participants_flat[0]
            print(f"📋 첫 번째 참여자 샘플 (키 목록): {list(sample.keys())}")
            # ⭐ 실제 컬럼명과 매칭
            matched_name_col = find_matching_column_name(name_column, sample) if name_column else None
            if matched_name_col and matched_name_col in sample:
                if matched_name_col != name_column:
                    print(f"🔍 name_column 매칭: '{name_column}' → '{matched_name_col}'")
                print(f"✅ name_column '{matched_name_col}' 값: {sample.get(matched_name_col)}")
            else:
                print(f"❌ name_column '{name_column}'이 데이터에 없음 (매칭 결과: '{matched_name_col}')")
            if schedule_columns:
                for col in schedule_columns[:2]:  # 처음 2개만 출력
                    if col in sample:
                        print(f"✅ schedule_column '{col}' 값: {sample.get(col)}")
                    else:
                        print(f"❌ schedule_column '{col}'이 데이터에 없음")
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

        # 달력 기반 스냅샷 생성 (마스킹 포함, 배정되지 않은 참여자도 포함)
        calendar_snapshot = build_calendar_snapshot(
            participants_data,
            optimized_schedule,
            name_column,
            schedule_columns
        )
        
        # JSON-safe 처리
        calendar_snapshot = jsonify_safe(calendar_snapshot)
        
        # validation_data에서 배정되지 않은 참여자 정보 추출
        unassigned_count = validation_data.get('unassigned_count', 0)
        missing_count = validation_data.get('missing_count', 0)
        unassigned_participants = validation_data.get('unassigned_participants', [])
        missing_participants = validation_data.get('missing_participants', [])
        
        # 일정 배정 통계 계산
        assigned_count = len([p for p in calendar_snapshot if p.get('has_schedule', False)])
        total_count = len(calendar_snapshot)
        unassigned_total = total_count - assigned_count

        if session_scope is None:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        saved_at_dt = datetime.utcnow().replace(microsecond=0)

        # final_participants는 참여자 배열만 저장 (메타데이터 제외 - 개인정보 보호)
        # 메타데이터는 응답에만 포함하고 DB에는 저장하지 않음
        upsert_payload = {
            'study_id': study_id_int,
            'final_participants': calendar_snapshot,  # 참여자 배열만 저장 (이름 마스킹됨)
            'saved_at': saved_at_dt,
            'updated_at': saved_at_dt
        }

        with session_scope() as db_session:
            existing = db_session.execute(
                select(StudySchedule).where(StudySchedule.study_id == study_id_int).limit(1)
            ).scalar_one_or_none()
            if existing:
                existing.final_participants = upsert_payload['final_participants']
                existing.saved_at = saved_at
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
                    saved_at=saved_at,
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
