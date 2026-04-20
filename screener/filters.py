"""
screener/filters.py
Sincerity filter helpers extracted from routes/screener.py
"""
import pandas as pd


def detect_column_type(col_data):
    # DataFrame이면 첫 번째 컬럼을 Series로 변환
    if isinstance(col_data, pd.DataFrame):
        col_data = col_data.iloc[:, 0]
    if not isinstance(col_data, pd.Series):
        return 'categorical'

    try:
        pd.to_numeric(col_data, errors='raise')
        return 'numerical'
    except Exception:
        pass

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

    likert_columns = []
    for col_info in csv_schema:
        col_name = col_info.get('column_name', '')
        if col_name in df.columns:
            values = df[col_name].unique()
            try:
                numeric_vals = pd.to_numeric(values, errors='coerce')
                valid_vals = [v for v in numeric_vals if pd.notna(v) and 1 <= v <= 5]
                if len(valid_vals) >= 3 and len(set(valid_vals)) <= 5:
                    likert_columns.append(col_name)
            except Exception:
                pass

    print(f"DEBUG: 5점 척도 문항 감지: {len(likert_columns)}개 - {likert_columns[:5]}")

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
                    except Exception:
                        current_consecutive = 0
                        last_value = None

            if max_consecutive >= 5:
                suspicious_indices.add(idx)

    if len(likert_columns) >= 5:
        for idx, row in df.iterrows():
            if idx in suspicious_indices:
                continue

            extreme_count = 0
            total_count = 0

            for col in likert_columns[:15]:
                val = row[col]
                if pd.notna(val):
                    try:
                        val = float(val)
                        total_count += 1
                        if val == 1 or val == 5:
                            extreme_count += 1
                    except Exception:
                        pass

            if total_count >= 5 and (extreme_count / total_count) >= 0.8:
                suspicious_indices.add(idx)

    for col in prose_columns:
        if col not in df.columns:
            continue

        meaningless_patterns = ['ㅇㅇ', 'ㅇ', 'ㅋㅋ', 'ㅋ', '없음', '없어요', '없습니다', '..', '...', 'ㄱㄱ']

        for idx, row in df.iterrows():
            if idx in suspicious_indices:
                continue

            text = str(row[col]).strip()

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
        df['avg_sincerity_score'] = 5

    suspicious_indices = detect_suspicious_patterns(df, csv_schema, prose_columns)
    suspicious_count = len(suspicious_indices)
    suspicious_percentage = (suspicious_count / initial_total * 100) if initial_total > 0 else 0
    print(f"⚠️  의심 패턴 감지: {suspicious_count}명 ({suspicious_percentage:.1f}%)")

    for idx in suspicious_indices:
        df.loc[idx, 'avg_sincerity_score'] = 1
        df.loc[idx, 'sincerity_reason'] = '의심 패턴 감지 (straight-lining, 극단값 반복, 무성의 응답)'

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
