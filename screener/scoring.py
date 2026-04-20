"""
screener/scoring.py
find_optimal_participants 엔드포인트의 스텝별 비즈니스 로직
"""
import io
import json
import re
import pandas as pd

from screener.filters import apply_sincerity_filter, detect_column_type
from screener.utils import normalize_column_name, find_matching_column_name
from utils.llm_utils import parse_llm_json_response
from prompts.analysis_prompts import ScreenerPrompts


def step1_map_variables(plan_json: dict, csv_info: dict, openai_service) -> tuple:
    """
    Step 1: 계획서 변수와 CSV 컬럼 매핑 (LLM 호출)

    Returns:
        (mapping_analysis, key_variable_mappings, balance_variable_mappings)
    """
    key_variables = plan_json.get('key_variables', [])
    balance_variables = plan_json.get('balance_variables', [])
    csv_columns = [col['column_name'] for col in csv_info['schema']]
    csv_schema = csv_info.get('schema', [])
    column_metadata = csv_info.get('column_metadata', {})

    print(f"📊 변수 맵핑 시작: {len(column_metadata)}개 컬럼 메타데이터 포함")
    prompt = ScreenerPrompts.prompt_map_variables(
        key_variables, balance_variables, csv_columns, csv_schema, column_metadata
    )
    mapping_result = openai_service.generate_response(prompt, {"model": "gpt-4o"})
    mapping_analysis = parse_llm_json_response(mapping_result)

    key_variable_mappings = mapping_analysis.get('key_variable_mappings')
    if key_variable_mappings is None:
        key_variable_mappings = mapping_analysis.get('variable_mappings', [])
    balance_variable_mappings = mapping_analysis.get('balance_variable_mappings', [])

    # 호환성 보정
    mapping_analysis['key_variable_mappings'] = key_variable_mappings
    mapping_analysis['balance_variable_mappings'] = balance_variable_mappings
    mapping_analysis['variable_mappings'] = key_variable_mappings

    return mapping_analysis, key_variable_mappings, balance_variable_mappings


def step2_create_scoring_criteria(
    target_groups: list,
    key_variable_mappings: list,
    csv_info: dict,
    gemini_service,
) -> dict:
    """
    Step 2: 그룹별 스코어링 기준 생성 (LLM 호출)

    Returns:
        criteria_analysis dict
    """
    csv_schema = csv_info.get('schema', [])
    column_metadata = csv_info.get('column_metadata', {})

    print(f"🎯 스코어링 기준 생성: 메타데이터 포함 ({len(column_metadata)}개 컬럼)")
    prompt = ScreenerPrompts.prompt_create_scoring_criteria(
        target_groups, key_variable_mappings, csv_schema, column_metadata
    )
    criteria_result = gemini_service.generate_response(
        prompt,
        {"temperature": 0.1, "max_output_tokens": 1048576},
        model_name="gemini-2.5-pro"
    )
    criteria_analysis = parse_llm_json_response(criteria_result)

    # pandas 표현식 검증 로그
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

    return criteria_analysis


def step3_build_dataframes(csv_content: str, csv_info: dict) -> tuple:
    """
    Step 3 준비: DataFrame 생성 + 컬럼명 정규화 + participant_id 생성

    Returns:
        (df, df_original, key_mapped_columns, balance_mapped_columns)
    """
    df = pd.read_csv(io.StringIO(csv_content))
    df = df.fillna('')

    original_name_column = csv_info.get('name_column')
    original_schedule_columns = csv_info.get('schedule_columns', [])

    print(f"📌 [Step 3-4] 컬럼명 정규화 전: {list(df.columns[:3])}")
    df.columns = [normalize_column_name(col) for col in df.columns]
    print(f"📌 [Step 3-4] 컬럼명 정규화 후: {list(df.columns[:3])}")
    print(f"📌 [Step 3-4] 컬럼명 정규화 완료: {len(df.columns)}개 컬럼")

    if original_name_column:
        normalized_name = normalize_column_name(original_name_column)
        csv_info['name_column'] = normalized_name
        if normalized_name != original_name_column:
            print(f"✅ name_column 정규화: '{original_name_column}' → '{normalized_name}'")

    if original_schedule_columns:
        normalized_schedule = [normalize_column_name(col) for col in original_schedule_columns]
        csv_info['schedule_columns'] = normalized_schedule
        print(f"✅ schedule_columns 정규화: {len(normalized_schedule)}개")

    df.reset_index(drop=True, inplace=True)
    df['participant_id'] = df.index.map(lambda idx: f'ROW_{idx + 1}')

    df_original = df.copy()
    print(f"📌 총 {len(df)}명의 참여자 데이터 로드 완료 (participant_id: ROW_1 ~ ROW_{len(df)})")

    return df, df_original


def step3_extract_mapped_columns(
    df: pd.DataFrame,
    key_variable_mappings: list,
    balance_variable_mappings: list,
) -> tuple:
    """
    Step 3: 매핑된 컬럼 리스트 추출

    Returns:
        (key_mapped_columns, balance_mapped_columns, all_mapped_columns)
    """
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

    return key_mapped_columns, balance_mapped_columns, all_mapped_columns


def step3_score_participants(
    df: pd.DataFrame,
    df_mapped: pd.DataFrame,
    group_criteria: list,
    sincerity_rules: dict,
    csv_info: dict,
) -> pd.DataFrame:
    """
    Step 3: 성실도 필터링 + 그룹별 스코어링 계산

    Returns:
        df_mapped (점수 컬럼 추가된 상태)
    """
    csv_schema = csv_info.get('schema', [])

    # 성실도 필터링
    df_filtered = apply_sincerity_filter(df, sincerity_rules, csv_schema)
    df_mapped = df_mapped[df_mapped['participant_id'].isin(df_filtered['participant_id'])].copy()
    print(f"📌 성실도 필터링 후: {len(df_filtered)}명 (df_mapped: {len(df_mapped)}명)")

    # 그룹별 스코어링
    for group in group_criteria:
        group_name = group['group_name']
        df_mapped[f'{group_name}_score'] = 0

        for variable in group['logic']:
            col_name = variable['column_name']
            if col_name not in df_mapped.columns:
                print(f"DEBUG: 컬럼 없음 - {col_name} (df_mapped 컬럼: {list(df_mapped.columns[:5])})")
                continue

            col_data = df_mapped[col_name]
            if isinstance(col_data, pd.DataFrame):
                print(f"⚠️ 경고: {col_name}이 DataFrame입니다. 첫 번째 컬럼을 사용합니다.")
                col_data = col_data.iloc[:, 0]
            if not isinstance(col_data, pd.Series):
                print(f"⚠️ 경고: {col_name}이 Series가 아닙니다. 타입: {type(col_data)}")
                continue

            detected_type = detect_column_type(col_data)

            for rule in variable['rules']:
                pandas_expr = rule.get('pandas_expression', '')
                points_added = 0

                if pandas_expr:
                    try:
                        expr_for_eval = pandas_expr.replace("df[", "df_mapped[")
                        mask = eval(expr_for_eval)
                        df_mapped.loc[mask, f'{group_name}_score'] += rule['points']
                        points_added = mask.sum()
                        print(f"✅ {col_name} pandas_expression 사용: {points_added}명 매칭")
                    except Exception as e:
                        print(f"⚠️ pandas_expression 실행 실패: {pandas_expr}")
                        print(f"   에러: {e}")
                        pandas_expr = None

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
                    elif variable['type'] in ('categorical', 'opentext'):
                        target_value = str(rule.get('value', '')).strip()
                        match_mode = (rule.get('match_mode') or rule.get('match_type') or 'exact').lower()

                        if not isinstance(col_data, pd.Series):
                            print(f"⚠️ 오류: {col_name}이 Series가 아닙니다. 타입: {type(col_data)}")
                            continue

                        series = col_data.fillna('').astype(str).str.strip()

                        if match_mode == 'contains':
                            mask = series.str.contains(re.escape(target_value), na=False, case=False) if target_value else pd.Series([False] * len(series))
                        else:
                            mask = series.str.lower() == target_value.lower()

                        df_mapped.loc[mask, f'{group_name}_score'] += rule['points']
                        points_added = int(mask.sum())
                        unique_vals = series.unique()[:5]
                        print(
                            f"DEBUG: {col_name} {variable['type']} '{target_value}' (mode={match_mode}) 매칭: {points_added}명 (샘플: {list(unique_vals)})"
                        )

    # 스코어링 결과 요약 로그
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
    print("=" * 80)

    df_mapped = df_mapped.fillna('')
    return df_mapped


def step3_build_top_candidates(
    df_mapped: pd.DataFrame,
    target_groups: list,
    balance_mapped_columns: list,
) -> tuple:
    """
    Step 3: 그룹별 상위 후보 추출 + LLM 전달용 sample_data 생성

    Returns:
        (scored_data_for_frontend, sample_data)
    """
    scored_data_for_frontend = {}

    for group in target_groups:
        group_name = group['name']
        score_col = f"{group_name}_score"
        target_count = group.get('targetCount', 0)
        min_count = max(15, target_count * 3)

        if score_col in df_mapped.columns:
            top_participants = df_mapped.sort_values(by=score_col, ascending=False).head(min_count)
            records = top_participants.to_dict('records')
            scored_data_for_frontend[group_name] = records
            print(f"DEBUG: {group_name} 후보 추출: {len(records)}명 (요청: 최소 {min_count}명)")

    # LLM 전달용 샘플 데이터 생성
    sample_data = {}
    for group_name, group_records in scored_data_for_frontend.items():
        sample_data[group_name] = []
        for record in group_records:
            participant_id = record.get('participant_id')
            score_col = f"{group_name}_score"
            score = float(record.get(score_col, 0))
            sample_item = {'id': participant_id, 'score': score}
            for balance_col in balance_mapped_columns:
                if balance_col in record:
                    sample_item[balance_col] = str(record[balance_col])
            sample_data[group_name].append(sample_item)

    # 디버깅 로그
    print("=" * 80)
    print("📊 LLM 전달 데이터 요약")
    print("=" * 80)
    print(f"📌 그룹 수: {len(sample_data)}")
    for group_name, records in sample_data.items():
        print(f"📌 {group_name}: {len(records)}명")
        if records:
            columns = [k for k in records[0].keys() if k not in ['id', 'score']]
            print(f"   포함 컬럼: {columns} (균형 변수만)")
    print("=" * 80)

    return scored_data_for_frontend, sample_data


def step4_run_final_selection(
    target_groups: list,
    sample_data: dict,
    plan_json: dict,
    gemini_service,
) -> dict:
    """
    Step 4: 최종 선별 LLM 호출

    Returns:
        final_result (gemini_service 응답)
    """
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
        balance_variables_json=json.dumps(plan_json.get('balance_variables', []), ensure_ascii=False, indent=2),
        group_targets_and_candidates=group_targets_and_candidates
    )

    total_candidates = sum(len(v) for v in sample_data.values())
    print(f"DEBUG: Step 4 - LLM 호출 시작 (프롬프트 길이: {len(prompt)}자, 후보 총 {total_candidates}명)")

    final_result = gemini_service.generate_response(
        prompt,
        generation_config={"temperature": 0.1, "max_output_tokens": 1048576},
        model_name="gemini-2.5-flash-lite"
    )
    print(
        f"DEBUG: Step 4 - LLM 응답 완료 "
        f"(응답 길이: {len(final_result.get('content', '')) if final_result.get('success') else 0}자)"
    )
    return final_result


def step4_parse_and_restore(
    final_result: dict,
    df_original: pd.DataFrame,
    csv_info: dict,
) -> tuple:
    """
    Step 4: LLM 응답 JSON 파싱 + 원본 참여자 데이터 복원.

    Returns:
        (parsed_json, final_participants_data) or raises on parse error
    """
    content = final_result.get('content', '')

    # 코드 블록 제거
    if '```json' in content:
        content = content.split('```json')[1].split('```')[0]
    elif '```' in content:
        content = content.split('```')[1].split('```')[0]

    # JSON 시작 위치
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

    content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content).strip()
    parsed_json = json.loads(content)

    # 중복 제거
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

    # 원본 데이터 복원
    selected_participant_ids = []
    participant_id_to_group_info = {}

    for group in parsed_json.get('recommendations', []):
        group_name = group.get('group_name', '')
        for participant in group.get('participants', []):
            pid = participant.get('id')
            if not pid:
                continue
            selected_participant_ids.append(pid)
            participant_id_to_group_info[pid] = {
                'group_name': group_name,
                'reason': participant.get('reason', ''),
                'score': participant.get('score', 0)
            }

    final_participants_data = []
    if selected_participant_ids:
        filtered_df = df_original[df_original['participant_id'].isin(selected_participant_ids)].copy()
        final_participants_data = filtered_df.to_dict('records')

        name_col = csv_info.get('name_column')
        if final_participants_data:
            matched_name_col = find_matching_column_name(name_col, final_participants_data[0])
            if matched_name_col and matched_name_col != name_col:
                print(f"🔍 name_column 매칭: '{name_col}' → '{matched_name_col}'")
                name_col = matched_name_col

        for participant_data in final_participants_data:
            pid = participant_data.get('participant_id')
            group_info = participant_id_to_group_info.get(pid, {})

            display_name = pid
            if name_col and name_col in participant_data:
                name_value = participant_data[name_col]
                if name_value is not None and str(name_value).strip():
                    display_name = str(name_value).strip()

            participant_data['_display_name'] = display_name
            participant_data['_selection_reason'] = group_info.get('reason', '')
            participant_data['_assigned_group'] = group_info.get('group_name', '')
            participant_data['_group_score'] = group_info.get('score', 0)
    else:
        print("⚠️ WARNING: 선별된 participant_id가 없습니다.")

    print(f"📌 최종 선별 완료: {len(final_participants_data)}명의 참여자 데이터 복원 완료")
    return parsed_json, final_participants_data
