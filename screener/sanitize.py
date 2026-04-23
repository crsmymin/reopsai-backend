"""
screener/sanitize.py
PII 마스킹 및 sanitize 헬퍼 함수들 extracted from routes/screener.py
"""
import copy

from screener.utils import normalize_column_name

SENSITIVE_KEYWORDS = [
    'name', 'contact', 'phone', 'tel', 'email', 'mobile', 'id',
    '이름', '성함', '연락', '전화', '휴대', '번호'
]
SENSITIVE_EXCLUDE_KEYS = {'participant_id', '_participant_id'}


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
