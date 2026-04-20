"""
screener/csv_profiler.py
CSV 업로드 단계(upload_csv)의 프로파일링 및 LLM 감지 로직
"""
import json
import pandas as pd

from screener.utils import normalize_column_name
from utils.llm_utils import parse_llm_json_response
from prompts.analysis_prompts import ScreenerPrompts


def profile_csv_columns(df: pd.DataFrame) -> list:
    """
    DataFrame의 각 컬럼을 분석하여 컬럼 스키마 목록 반환.
    컬럼명은 이미 정규화되어 있다고 가정하며,
    original_column_name은 column_name_mapping 인자로 전달.
    """
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
                total_rows = len(col_str)
                sample_indices = []
                if total_rows > 50:
                    sample_indices = list(range(0, min(50, total_rows), 10))
                    sample_indices.extend(range(50, total_rows, 20))
                else:
                    sample_indices = range(min(10, total_rows))
                col_values_sample = [col_str.iloc[i] for i in sample_indices if i < len(col_str)]
                value_frequencies = {
                    'avg_length': float(avg_length),
                    'total_responses': int(len(col_data)),
                    'non_empty_responses': int((col_data != '').sum())
                }
            else:
                col_type = 'Categorical'
                unique_vals = col_data.unique()[:30]
                if len(col_data) > len(unique_vals):
                    sample_rows = col_data.sample(min(50, len(col_data)), random_state=42).tolist()
                    col_values_sample = list(set([str(v) for v in list(unique_vals) + sample_rows]))[:50]
                else:
                    col_values_sample = [str(v) for v in unique_vals]
                value_counts = col_data.value_counts()
                value_frequencies = {
                    'top_5': {str(k): int(v) for k, v in value_counts.head(5).items()},
                    'total_responses': int(len(col_data)),
                    'non_empty_responses': int((col_data != '').sum()),
                    'response_rate': float((col_data != '').sum() / len(col_data) * 100)
                }

        column_schema.append({
            'column_name': col,
            'type': col_type,
            'unique_count': num_unique,
            'values_sample': col_values_sample,
            'value_frequencies': value_frequencies
        })

    print(f"✅ 프로파일링 완료: {len(column_schema)}개 컬럼 분석됨")
    return column_schema


def attach_original_column_names(column_schema: list, column_name_mapping: dict) -> list:
    """column_schema 각 항목에 original_column_name 필드를 추가하여 반환."""
    for col_info in column_schema:
        col = col_info['column_name']
        col_info['original_column_name'] = column_name_mapping.get(col, col)
    return column_schema


def detect_identifier_column(column_schema: list, openai_service) -> str | None:
    """LLM을 통해 이름(식별자) 컬럼을 감지하고 정규화된 컬럼명 반환."""
    schema_data = {'schema': column_schema}
    print("DEBUG: 1단계 - 이름 컬럼 찾기 시작")
    name_prompt = ScreenerPrompts.prompt_detect_name_column_only(
        json.dumps(schema_data, ensure_ascii=False, indent=2)
    )
    name_result = openai_service.generate_response(name_prompt, {"temperature": 0.1})
    name_analysis = parse_llm_json_response(name_result)
    ai_name_column = name_analysis.get('name_column')

    detected_name_column = None
    if ai_name_column:
        for col_info in column_schema:
            original_col = col_info.get('original_column_name', '')
            normalized_col = col_info.get('column_name', '')
            if ai_name_column == original_col or ai_name_column == normalized_col:
                detected_name_column = normalized_col
                print(f"DEBUG: AI가 찾은 이름 컬럼 (원본): {original_col}")
                print(f"DEBUG: 매칭된 정규화된 컬럼명: {normalized_col}")
                break

        if not detected_name_column:
            normalized_ai_col = normalize_column_name(ai_name_column)
            for col_info in column_schema:
                if col_info.get('column_name') == normalized_ai_col:
                    detected_name_column = normalized_ai_col
                    print(f"DEBUG: AI가 찾은 이름 컬럼: {ai_name_column}")
                    print(f"DEBUG: 정규화 후 매칭된 컬럼명: {normalized_ai_col}")
                    break

    if not detected_name_column and ai_name_column:
        print(f"⚠️ WARNING: AI가 찾은 컬럼명 '{ai_name_column}'을 매칭할 수 없습니다.")

    return detected_name_column


def detect_schedule_columns(column_schema: list, openai_service) -> list:
    """LLM을 통해 일정 컬럼 목록을 감지하여 반환."""
    schema_data = {'schema': column_schema}
    print("DEBUG: 2단계 - 일정 컬럼 찾기 시작")
    schedule_prompt = ScreenerPrompts.prompt_detect_schedule_columns_only(
        json.dumps(schema_data, ensure_ascii=False, indent=2)
    )
    schedule_result = openai_service.generate_response(schedule_prompt, {"temperature": 0.1})
    schedule_analysis = parse_llm_json_response(schedule_result)
    detected_schedule_columns = schedule_analysis.get('schedule_columns', [])
    print(f"DEBUG: AI가 찾은 일정 컬럼(원본): {detected_schedule_columns}")
    return detected_schedule_columns


def analyze_data_schema(column_schema: list, openai_service) -> dict:
    """LLM을 통해 컬럼 타입 맵과 성실도 규칙 분석."""
    schema_data = {'schema': column_schema}
    print("DEBUG: 3단계 - 성실도 및 기타 분석 시작")
    schema_prompt = ScreenerPrompts.prompt_analyze_data_schema(
        json.dumps(schema_data, ensure_ascii=False, indent=2)
    )
    schema_result = openai_service.generate_response(schema_prompt, {"temperature": 0.1})
    return parse_llm_json_response(schema_result)


def build_column_metadata(df: pd.DataFrame, column_schema: list) -> dict:
    """컬럼별 상세 메타데이터 생성 (스코어링 참조용)."""
    column_metadata = {}
    MAX_UNIQUE_FOR_FULL_LIST = 30
    TOP_N_FOR_MAPPING = 10

    for col_info in column_schema:
        col_name = col_info['column_name']
        unique_count = col_info['unique_count']

        col_metadata = {
            'type': col_info['type'],
            'unique_count': unique_count,
            'frequencies': col_info.get('value_frequencies', {})
        }

        if 'top_5' in col_info.get('value_frequencies', {}):
            col_data = df[col_name]
            value_counts = col_data.value_counts()
            total = col_info['value_frequencies']['total_responses']

            if unique_count <= MAX_UNIQUE_FOR_FULL_LIST:
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

            col_metadata['top_for_mapping'] = col_metadata['all_responses'][:TOP_N_FOR_MAPPING]
            col_metadata['top_responses_summary'] = col_metadata['all_responses'][:5]

        column_metadata[col_name] = col_metadata

    print(f"📈 메타데이터 생성 완료: {len(column_metadata)}개 컬럼")
    return column_metadata


def build_csv_info(
    df: pd.DataFrame,
    column_schema: list,
    column_metadata: dict,
    detected_name_column: str | None,
    detected_schedule_columns: list,
    schema_analysis: dict,
) -> dict:
    """최종 csv_info 딕셔너리 조립."""
    csv_info = {
        'total_rows': len(df),
        'schema': column_schema,
        'identifier': {
            'column': detected_name_column,
            'mode': 'name' if detected_name_column else 'row_number',
            'available': bool(detected_name_column)
        },
        'schedule': {
            'columns': detected_schedule_columns,
            'available': bool(detected_schedule_columns),
            'mode': 'schedule' if detected_schedule_columns else 'no_schedule'
        },
        # 레거시 호환성
        'name_column': detected_name_column,
        'schedule_columns': detected_schedule_columns,
        'column_type_map': schema_analysis.get('column_type_map', {}),
        'has_name_column': bool(detected_name_column),
        'has_schedule_columns': bool(detected_schedule_columns),
        'column_metadata': column_metadata
    }

    print(f"🎯 분석 완료 요약:")
    print(f"   - 식별자: {csv_info['identifier']['mode']} ({csv_info['identifier']['column'] or '행 번호'})")
    print(f"   - 일정 정보: {'있음' if csv_info['schedule']['available'] else '없음'} ({len(detected_schedule_columns)}개 컬럼)")
    print(f"   - 메타데이터: {len(column_metadata)}개 컬럼")

    return csv_info
