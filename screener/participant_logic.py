"""
screener/participant_logic.py
finalize_participants 엔드포인트의 비즈니스 로직
"""
import copy
import json
from collections import defaultdict

from screener.utils import compute_display_name, coerce_score, find_matching_column_name


def build_participants_map(
    participants_data: list,
    group_info_map: dict,
    default_group_name: str,
    name_column: str,
    has_name_column: bool,
    selected_ids: set,
    schedule_columns: list,
    contact_columns: list,
) -> tuple:
    """
    participants_data를 순회하여 map, by_group, selected_by_group 구조 생성.

    Returns:
        (participants_map, participants_by_group, selected_by_group)
    """
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
        participant_copy['_display_name'] = compute_display_name(
            participant_copy, name_column, has_name_column
        )

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

    return participants_map, participants_by_group, selected_by_group


def build_scored_data_sample(
    participants_by_group: dict,
    selected_by_group: dict,
    group_info_map: dict,
    balance_variables: list,
    schedule_columns: list,
    contact_columns: list,
) -> tuple:
    """
    LLM 입력용 scored_data_sample 및 group_targets_and_candidates 생성.

    Returns:
        (group_targets_and_candidates, scored_data_sample)
    """
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

            for key, value in participant_copy.items():
                if key in [
                    'participant_id', '_group_score', '_selection_reason',
                    '_assigned_group', '_is_selected', '_is_user_selected',
                    '_display_name', '_schedule_values', '_contact_values'
                ]:
                    continue
                if key.startswith('_'):
                    continue
                if isinstance(value, (dict, list)):
                    continue

                is_balance = False
                for balance_var in balance_variables:
                    balance_var_name = balance_var.get('variable_name', '').lower()
                    balance_var_desc = balance_var.get('description', '').lower()
                    if balance_var_name in key.lower() or balance_var_desc in key.lower():
                        is_balance = True
                        break
                if is_balance:
                    record[key] = str(value)

            if schedule_columns:
                for col in schedule_columns:
                    if participant_copy.get(col) not in (None, ''):
                        record[col] = str(participant_copy.get(col))

            if contact_columns:
                for col in contact_columns:
                    if participant_copy.get(col) not in (None, ''):
                        record[col] = str(participant_copy.get(col))

            group_samples.append(record)

        scored_data_sample[group_name] = group_samples

    return group_targets_and_candidates, scored_data_sample


def apply_llm_selection(
    llm_payload: dict,
    participants_map: dict,
    participants_by_group: dict,
    selected_by_group: dict,
    group_info_map: dict,
    default_group_name: str,
) -> tuple:
    """
    LLM 응답 기반으로 최종 참여자 선정.

    Returns:
        (groups_output, final_participants_flat, reserve_participants_flat)
    """
    recommendations = llm_payload.get('recommendations', [])
    final_ids = set()

    # 이미 선택된 참여자 수집
    all_selected_by_group = {}
    for group_name, selected_list in selected_by_group.items():
        all_selected_by_group[group_name] = []
        for selected_info in selected_list:
            pid = str(selected_info.get('id'))
            if pid and pid in participants_map:
                all_selected_by_group[group_name].append(pid)
                final_ids.add(pid)

    print("=" * 80)
    print("🔍 [Step 5] 이미 선택된 참여자 확인")
    print("=" * 80)
    for group_name, selected_pids in all_selected_by_group.items():
        print(f"📌 그룹 '{group_name}': 이미 선택된 참여자 {len(selected_pids)}명 - {selected_pids[:5]}{'...' if len(selected_pids) > 5 else ''}")
    print("=" * 80)

    llm_group_names = {group.get('group_name', '') or default_group_name for group in recommendations}
    all_group_names = set(participants_by_group.keys()) | llm_group_names

    groups_output = []
    final_participants_flat = []
    reserve_participants_flat = []

    for group_name in all_group_names:
        group_target = group_info_map.get(group_name, {}).get('targetCount')
        selected_list = []

        # 1단계: 이미 선택된 참여자 추가
        for pid in all_selected_by_group.get(group_name, []):
            if pid in final_ids and pid not in [p.get('participant_id') for p in selected_list]:
                pass
            participant_data = participants_map.get(pid)
            if not participant_data:
                continue
            already_in = any(p.get('participant_id') == pid for p in selected_list)
            if already_in:
                continue
            participant_copy = copy.deepcopy(participant_data)
            participant_copy['_assigned_group'] = group_name
            participant_copy['_selection_status'] = 'user_selected'
            selected_list.append(participant_copy)

        remaining_needed = max((group_target or 0) - len(selected_list), 0) if group_target is not None else 0

        # 2단계: LLM 추천 참여자 추가
        llm_group = next(
            (g for g in recommendations if (g.get('group_name', '') or default_group_name) == group_name),
            None
        )
        if llm_group:
            for participant_entry in llm_group.get('participants', []):
                if remaining_needed <= 0:
                    break
                pid = str(participant_entry.get('id'))
                if not pid or pid in final_ids:
                    continue
                participant_data = participants_map.get(pid)
                if not participant_data:
                    print(f"⚠️ LLM이 반환한 ID를 찾을 수 없습니다: {pid}")
                    continue
                participant_copy = copy.deepcopy(participant_data)
                participant_copy['_assigned_group'] = group_name
                participant_copy['_selection_status'] = 'auto_selected'
                participant_copy['_selection_reason'] = participant_entry.get(
                    'reason', participant_copy.get('_selection_reason', '')
                )
                participant_copy['_group_score'] = participant_entry.get(
                    'score', participant_copy.get('_group_score', 0)
                )
                selected_list.append(participant_copy)
                final_ids.add(pid)
                remaining_needed -= 1

        if remaining_needed > 0:
            print("=" * 80)
            print(f"⚠️ [경고] 그룹 '{group_name}' 목표 인원 미달")
            print("=" * 80)
            print(f"   목표 인원: {group_target}명")
            print(f"   현재 선정: {len(selected_list)}명 (부족: {remaining_needed}명)")
            print("=" * 80)

        user_selected_count = len([p for p in selected_list if p.get('_selection_status') == 'user_selected'])
        auto_selected_count = len([p for p in selected_list if p.get('_selection_status') == 'auto_selected'])
        print(
            f"📊 그룹 '{group_name}': 목표 {group_target}명, 선정 {len(selected_list)}명 "
            f"(이미 선택: {user_selected_count}명, 추가 선정: {auto_selected_count}명)"
        )

        final_participants_flat.extend(selected_list)

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

    return groups_output, final_participants_flat, reserve_participants_flat


def apply_fallback_score_selection(
    participants_by_group: dict,
    group_info_map: dict,
    ordered_group_names: list,
) -> tuple:
    """
    LLM 실패 시 점수 기반 fallback 선정.

    Returns:
        (groups_output, final_participants_flat, reserve_participants_flat, total_user_selected, total_auto_selected)
    """
    groups_output = []
    final_participants_flat = []
    reserve_participants_flat = []
    total_user_selected = 0
    total_auto_selected = 0

    for group_name in list(dict.fromkeys(ordered_group_names + list(participants_by_group.keys()))):
        candidates = participants_by_group.get(group_name, [])
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

        for p in user_selected:
            p['_selection_status'] = 'user_selected'
        for p in auto_selected:
            p['_selection_status'] = 'auto_selected'
        for p in reserve_list:
            p['_selection_status'] = 'reserve'

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

    return groups_output, final_participants_flat, reserve_participants_flat, total_user_selected, total_auto_selected


def build_finalize_summary(
    groups_output: list,
    final_participants_flat: list,
    reserve_participants_flat: list,
    total_input_participants: int,
    total_user_selected: int = None,
    total_auto_selected: int = None,
) -> dict:
    """groups_output 기반으로 summary 딕셔너리 생성."""
    if total_user_selected is None:
        total_user_selected = len([p for p in final_participants_flat if p.get('_selection_status') == 'user_selected'])
    if total_auto_selected is None:
        total_auto_selected = len([p for p in final_participants_flat if p.get('_selection_status') == 'auto_selected'])

    return {
        'total_input_participants': total_input_participants,
        'total_groups': len(groups_output),
        'total_final_participants': len(final_participants_flat),
        'total_user_selected': total_user_selected,
        'total_auto_selected': total_auto_selected,
        'total_reserve_participants': len(reserve_participants_flat),
        'groups_with_shortage': [
            {'group_name': g['group_name'], 'remaining_slots': g['remaining_slots']}
            for g in groups_output if g.get('remaining_slots')
        ],
        'groups_with_overflow': [
            {'group_name': g['group_name'], 'overflow_count': g['overflow_count']}
            for g in groups_output if g.get('overflow_count')
        ]
    }
