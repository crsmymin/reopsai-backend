"""
screener/builders.py
응답 구조체 빌더 함수들 extracted from routes/screener.py
"""
import json
from collections import defaultdict

from screener.sanitize import mask_text
from screener.utils import normalize_column_name


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

    participant_schedules = {}  # {participant_name: [assigned_slots]}

    for date_key, day_assignments in schedule_assignments.items():
        if not isinstance(day_assignments, dict):
            continue

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
                            'weekday': weekday,
                            'time_slot': time_slot
                        })
            elif isinstance(participants_list, str):
                name_str = participants_list.strip()
                if name_str:
                    if name_str not in participant_schedules:
                        participant_schedules[name_str] = []
                    participant_schedules[name_str].append({
                        'date': date_key,
                        'weekday': weekday,
                        'time_slot': time_slot
                    })

    normalized_name_column = normalize_column_name(name_column) if name_column else None
    snapshot = []

    for participant in participants_data:
        if not isinstance(participant, dict):
            continue

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

        pid = str(
            participant.get('participant_id')
            or participant.get('id')
            or participant.get('participantId')
            or ''
        ).strip()

        if not pid:
            continue

        entry = {
            'participant_id': pid,
            'name': mask_text(participant_name),
            'assigned_schedule': participant_schedules.get(participant_name, []),
            'assigned_group': participant.get('_assigned_group') or participant.get('assigned_group'),
            'selection_status': participant.get('_selection_status') or participant.get('selection_status'),
            'has_schedule': participant_name in participant_schedules
        }

        snapshot.append(entry)

    return snapshot
