import re
import pandas as pd


def normalize_column_name(col_name):
    """
    컬럼명 정규화 함수: 2단계와 3-4단계에서 동일하게 사용
    - non-breaking space, 전각 공백 처리
    - 줄바꿈 문자를 공백으로 변환
    - 연속 공백을 단일 공백으로 변환
    - 앞뒤 공백 제거
    """
    if not isinstance(col_name, str):
        return col_name
    normalized = (
        col_name.replace("\n", " ")
        .replace("\r", " ")
        .replace("\xa0", " ")
        .replace("\u3000", " ")
    )
    return re.sub(r"\s+", " ", normalized).strip()


def find_matching_column_name(normalized_name, available_columns):
    """
    정규화된 컬럼명을 실제 DataFrame의 컬럼명과 매칭하는 함수

    Args:
        normalized_name: 정규화된 컬럼명 (csv_info에서 가져온 값)
        available_columns: 실제 DataFrame의 컬럼명 리스트 또는 dict의 키 리스트

    Returns:
        매칭된 실제 컬럼명, 없으면 None
    """
    if not normalized_name:
        return None

    if isinstance(available_columns, dict):
        available_columns = list(available_columns.keys())
    elif not isinstance(available_columns, (list, pd.Index)):
        return None

    if normalized_name in available_columns:
        return normalized_name

    normalized_name_clean = normalize_column_name(normalized_name)
    for col in available_columns:
        if normalize_column_name(col) == normalized_name_clean:
            return col

    normalized_name_lower = normalized_name_clean.lower()
    for col in available_columns:
        col_normalized = normalize_column_name(col)
        col_normalized_lower = col_normalized.lower()
        if normalized_name_lower in col_normalized_lower or col_normalized_lower in normalized_name_lower:
            if len(normalized_name_lower) >= 10 or len(col_normalized_lower) >= 10:
                return col

    return None


def compute_display_name(row: dict, name_column: str, has_name_column: bool) -> str:
    """
    참여자 표시 이름 계산 (Step 2 감지 결과를 우선 활용)
    """
    if name_column:
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
    """
    참여자 점수 추출 (여러 가능한 키 순서대로 시도)
    """
    potential_keys = [
        '_group_score',
        f'{group_name}_score',
        '_score',
        'score',
    ]
    for key in potential_keys:
        if key in row and row[key] not in (None, ''):
            try:
                return float(row[key])
            except (ValueError, TypeError):
                continue
    return 0.0

