"""
screener/schedule_logic.py
optimize_schedule 엔드포인트의 비즈니스 로직
"""
import re

from screener.utils import find_matching_column_name


def parse_availability_data(
    participants_data: list,
    schedule_columns: list,
    name_column: str,
) -> tuple:
    """
    참여자 데이터에서 가용 시간 정보를 파싱.

    Returns:
        (availability_data, required_from_data)
    """
    availability_data = []
    required_from_data = []

    for participant in participants_data:
        if not isinstance(participant, dict):
            continue

        pid = str(
            participant.get('participant_id')
            or participant.get('id')
            or participant.get('participantId')
            or ''
        )
        if not pid:
            continue

        # display_name 결정
        display_name = ''
        if '_display_name' in participant and participant['_display_name']:
            display_name = str(participant['_display_name']).strip()
        elif name_column:
            matched_name_col = find_matching_column_name(name_column, participant)
            if matched_name_col and matched_name_col in participant:
                name_value = str(participant.get(matched_name_col)).strip()
                if name_value:
                    display_name = name_value
                else:
                    display_name = pid
            else:
                display_name = pid
        else:
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

        entry = {
            'participant_id': pid,
            'participant_name': display_name,
            'assigned_group': participant.get('_assigned_group') or participant.get('group_name'),
            'is_user_selected': bool(
                participant.get('_selection_status') == 'user_selected'
                or participant.get('_is_user_selected')
            ),
            'availability': availability
        }
        availability_data.append(entry)

        if entry['is_user_selected']:
            required_from_data.append(display_name)

    return availability_data, required_from_data


def validate_schedule_result(
    optimized_schedule: dict,
    availability_data: list,
) -> dict:
    """
    일정 최적화 결과의 슬롯 수와 참여자 배정을 검증.

    Returns:
        validation_data dict
    """
    schedule_assignments = optimized_schedule.get('schedule_assignments', {})
    unassigned_participants = optimized_schedule.get('unassigned_participants', []) or []
    total_participants = len(availability_data)

    assigned_names = set()
    total_slots = 0
    slot_details = []

    for date_key, day_assignments in schedule_assignments.items():
        if isinstance(day_assignments, dict):
            for time_slot, participants_list in day_assignments.items():
                if time_slot == 'weekday':
                    continue
                if isinstance(participants_list, list):
                    slot_count = len(participants_list)
                    total_slots += slot_count
                    assigned_names.update([str(p) for p in participants_list])
                    if slot_count > 1:
                        slot_details.append(
                            f"{date_key} {time_slot}: {slot_count}명 - {participants_list}"
                        )
                elif isinstance(participants_list, str):
                    total_slots += 1
                    assigned_names.add(participants_list)

    all_participant_names = set(entry.get('participant_name', '') for entry in availability_data)
    missing_participants = all_participant_names - assigned_names - set(unassigned_participants)

    # 검증 결과 로그
    print("=" * 80)
    print("📊 일정 최적화 결과 검증")
    print("=" * 80)
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
        print("⚠️ ❌ 여러 명이 배정된 슬롯 발견:")
        for detail in slot_details:
            print(f"   - {detail}")
    else:
        print("✅ 모든 슬롯에 1명씩 배정됨")

    print("=" * 80)

    if missing_participants:
        print(f"⚠️ 경고: {len(missing_participants)}명의 참여자가 일정에 배정되지 않았습니다: {missing_participants}")
    if unassigned_participants:
        print(f"⚠️ 경고: {len(unassigned_participants)}명의 참여자가 unassigned로 표시되었습니다: {unassigned_participants}")

    return {
        'total_participants': len(all_participant_names),
        'total_slots': total_slots,
        'assigned_count': len(assigned_names),
        'unassigned_count': len(unassigned_participants),
        'missing_count': len(missing_participants),
        'slot_mismatch': total_slots != total_participants,
        'multi_person_slots': slot_details,
        'unassigned_participants': list(unassigned_participants),
        'missing_participants': list(missing_participants)
    }
