from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import json
import traceback
from typing import Any

from reopsai.infrastructure.repositories import ScreenerRepository
from prompts.analysis_prompts import ScreenerPrompts
from reopsai.shared.llm import parse_llm_json_response


@dataclass(frozen=True)
class ScreenerResult:
    status: str
    data: Any = None
    error: str | None = None


class ScreenerService:
    _DEFAULT_SESSION_FACTORY = object()
    _DEFAULT_ADAPTER = object()

    def __init__(
        self,
        *,
        repository=None,
        session_factory=_DEFAULT_SESSION_FACTORY,
        openai_adapter=_DEFAULT_ADAPTER,
        gemini_adapter=_DEFAULT_ADAPTER,
    ):
        if repository is None:
            repository = ScreenerRepository
        if session_factory is self._DEFAULT_SESSION_FACTORY:
            from reopsai.infrastructure.database import session_scope

            session_factory = session_scope
        self.repository = repository
        self.session_factory = session_factory
        self.openai_adapter = openai_adapter
        self.gemini_adapter = gemini_adapter

    def _get_openai_adapter(self):
        if self.openai_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_openai_service

            self.openai_adapter = get_openai_service()
        return self.openai_adapter

    def _get_gemini_adapter(self):
        if self.gemini_adapter is self._DEFAULT_ADAPTER:
            from reopsai.infrastructure.llm import get_gemini_service

            self.gemini_adapter = get_gemini_service()
        return self.gemini_adapter

    def db_ready(self):
        return self.session_factory is not None

    def analyze_plan(self, *, plan_text) -> ScreenerResult:
        prompt = ScreenerPrompts.prompt_analyze_plan(plan_text)
        result = self._get_openai_adapter().generate_response(prompt)
        analysis = parse_llm_json_response(result)
        return ScreenerResult("ok", {"success": True, "analysis": analysis})

    def upload_csv(self, *, csv_content) -> ScreenerResult:
        import pandas as pd
        from screener.csv_profiler import (
            analyze_data_schema,
            attach_original_column_names,
            build_column_metadata,
            build_csv_info,
            detect_identifier_column,
            detect_schedule_columns,
            profile_csv_columns,
        )
        from screener.utils import normalize_column_name

        df = pd.read_csv(io.StringIO(csv_content))
        df = df.fillna('')

        original_columns = df.columns.tolist()
        df.columns = [normalize_column_name(col) for col in df.columns]
        normalized_columns = df.columns.tolist()
        print(f"📌 컬럼명 정규화 완료: {len(df.columns)}개 컬럼")

        column_name_mapping = dict(zip(normalized_columns, original_columns))
        column_schema = profile_csv_columns(df)
        column_schema = attach_original_column_names(column_schema, column_name_mapping)

        openai_adapter = self._get_openai_adapter()
        detected_name_column = detect_identifier_column(column_schema, openai_adapter)
        detected_schedule_columns = detect_schedule_columns(column_schema, openai_adapter)
        schema_analysis = analyze_data_schema(column_schema, openai_adapter)
        column_metadata = build_column_metadata(df, column_schema)
        csv_info = build_csv_info(
            df,
            column_schema,
            column_metadata,
            detected_name_column,
            detected_schedule_columns,
            schema_analysis,
        )

        return ScreenerResult(
            "ok",
            {
                'success': True,
                'csv_info': csv_info,
                'name_column': detected_name_column,
                'schedule_columns': detected_schedule_columns,
                'column_type_map': schema_analysis.get('column_type_map', {}),
                'sincerity_rules': schema_analysis.get('sincerity_rules'),
                'schema_analysis_text': schema_analysis.get('summary_text'),
            },
        )

    def find_optimal_participants_stream(self, *, csv_content, plan_json, csv_info, sincerity_rules):
        from screener.scoring import (
            step1_map_variables,
            step2_create_scoring_criteria,
            step3_build_dataframes,
            step3_build_top_candidates,
            step3_extract_mapped_columns,
            step3_score_participants,
            step4_parse_and_restore,
            step4_run_final_selection,
        )

        try:
            if isinstance(plan_json, str):
                parsed_plan_json = json.loads(plan_json)
            else:
                parsed_plan_json = plan_json

            mapping_analysis, key_variable_mappings, balance_variable_mappings = step1_map_variables(
                parsed_plan_json, csv_info, self._get_openai_adapter()
            )
            yield f"data: {json.dumps({'step': 1, 'mapping_result': mapping_analysis})}\n\n"

            target_groups = parsed_plan_json.get('target_groups', [])
            criteria_analysis = step2_create_scoring_criteria(
                target_groups, key_variable_mappings, csv_info, self._get_gemini_adapter()
            )
            yield f"data: {json.dumps({'step': 2, 'criteria_result': criteria_analysis})}\n\n"

            df, df_original = step3_build_dataframes(csv_content, csv_info)
            key_mapped_columns, balance_mapped_columns, all_mapped_columns = step3_extract_mapped_columns(
                df, key_variable_mappings, balance_variable_mappings
            )

            df_mapped = df[['participant_id'] + all_mapped_columns].copy()
            group_criteria = criteria_analysis.get('scoring_criteria', [])
            df_mapped = step3_score_participants(df, df_mapped, group_criteria, sincerity_rules, csv_info)

            scored_data_for_frontend, sample_data = step3_build_top_candidates(
                df_mapped, target_groups, balance_mapped_columns
            )
            yield f"data: {json.dumps({'step': 3, 'scored_data': scored_data_for_frontend, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"

            final_result = step4_run_final_selection(
                target_groups, sample_data, parsed_plan_json, self._get_gemini_adapter()
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
                        'csv_info': csv_info,
                    }
                    yield f"data: {json.dumps(result_data, ensure_ascii=False)}\n\n"
                except json.JSONDecodeError as exc:
                    print(f"JSON 파싱 오류: {exc}")
                    error_msg = f'JSON 파싱 실패: {str(exc)}'
                    yield f"data: {json.dumps({'step': 4, 'error': error_msg, 'final_selection': {'error': error_msg}, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'step': 4, 'final_selection': final_result, 'csv_info': csv_info}, ensure_ascii=False)}\n\n"

        except Exception as exc:
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(exc), 'step': 'error'})}\n\n"

    def optimize_schedule(self, *, data) -> ScreenerResult:
        from screener.schedule_logic import parse_availability_data, validate_schedule_result

        study_id = data.get('study_id')
        participants_data = data.get('participants_data') or []
        schedule_columns = data.get('schedule_columns') or []
        name_column = data.get('name_column') or ''
        target_groups = data.get('target_groups') or []
        required_participants = data.get('required_participants') or []

        if isinstance(schedule_columns, str):
            schedule_columns = [schedule_columns]

        availability_data, required_from_data = parse_availability_data(
            participants_data, schedule_columns, name_column
        )
        if not availability_data:
            return ScreenerResult("no_availability", error="일정 정보가 있는 참여자를 찾을 수 없습니다.")

        merged_required = []
        for name in list(required_participants) + required_from_data:
            if name and name not in merged_required:
                merged_required.append(name)

        total_participants = len(availability_data)
        context_info = {
            'study_id': study_id,
            'availability_data': availability_data,
            'target_groups': target_groups,
            'required_participants': merged_required,
            'schedule_columns': schedule_columns,
        }
        prompt = ScreenerPrompts.prompt_schedule_optimization_with_context(
            json.dumps(context_info, ensure_ascii=False, indent=2),
            total_participants,
        )
        result = self._get_openai_adapter().generate_response(prompt, {"temperature": 0.1})
        optimized_schedule = parse_llm_json_response(result)

        if not isinstance(optimized_schedule, dict):
            raise ValueError('LLM이 올바른 JSON을 반환하지 않았습니다.')

        validation_data = validate_schedule_result(optimized_schedule, availability_data)
        return ScreenerResult(
            "ok",
            {
                'success': True,
                'optimized_schedule': optimized_schedule,
                'validation': validation_data,
            },
        )

    def finalize_participants(self, *, data) -> ScreenerResult:
        from screener.participant_logic import (
            apply_fallback_score_selection,
            apply_llm_selection,
            build_finalize_summary,
            build_participants_map,
            build_scored_data_sample,
        )

        participants_data = data.get('participants_data') or []
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

        balance_variables = plan_json.get('balance_variables', []) if isinstance(plan_json, dict) else []
        balance_variables_json = json.dumps(balance_variables, ensure_ascii=False, indent=2)

        group_info_map = {
            grp.get('name'): grp for grp in target_groups
            if isinstance(grp, dict) and grp.get('name')
        }
        ordered_group_names = list(group_info_map.keys())
        default_group_name = ordered_group_names[0] if ordered_group_names else 'Unassigned'

        participants_map, participants_by_group, selected_by_group = build_participants_map(
            participants_data,
            group_info_map,
            default_group_name,
            name_column,
            has_name_column,
            selected_ids,
            schedule_columns,
            contact_columns,
        )
        group_targets_and_candidates, scored_data_sample = build_scored_data_sample(
            participants_by_group,
            selected_by_group,
            group_info_map,
            balance_variables,
            schedule_columns,
            contact_columns,
        )

        llm_success = False
        final_selection_payload = None
        try:
            prompt = ScreenerPrompts.prompt_smart_selection_with_selected(
                selected_participants_info=selected_by_group,
                target_groups=target_groups,
                scored_data_sample=scored_data_sample,
                balance_variables_json=balance_variables_json,
                schedule_columns=schedule_columns,
                group_targets_and_candidates=group_targets_and_candidates,
            )
            final_result = self._get_openai_adapter().generate_response(
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
                final_selection_payload,
                participants_map,
                participants_by_group,
                selected_by_group,
                group_info_map,
                default_group_name,
            )
            summary = build_finalize_summary(
                groups_output,
                final_participants_flat,
                reserve_participants_flat,
                len(participants_data),
            )
            return ScreenerResult(
                "ok",
                {
                    'success': True,
                    'name_column': name_column,
                    'schedule_columns': schedule_columns,
                    'groups': groups_output,
                    'final_participants': final_participants_flat,
                    'reserve_participants': reserve_participants_flat,
                    'summary': summary,
                    'final_selection': final_selection_payload,
                },
            )

        groups_output, final_participants_flat, reserve_participants_flat, total_user_selected, total_auto_selected = \
            apply_fallback_score_selection(participants_by_group, group_info_map, ordered_group_names)

        summary = build_finalize_summary(
            groups_output,
            final_participants_flat,
            reserve_participants_flat,
            len(participants_data),
            total_user_selected,
            total_auto_selected,
        )
        return ScreenerResult(
            "ok",
            {
                'success': True,
                'name_column': name_column,
                'schedule_columns': schedule_columns,
                'groups': groups_output,
                'final_participants': final_participants_flat,
                'reserve_participants': reserve_participants_flat,
                'summary': summary,
                'final_selection': None,
            },
        )

    def save_schedule(self, *, data) -> ScreenerResult:
        from screener.builders import build_calendar_snapshot, jsonify_safe

        study_id = data.get('study_id')
        try:
            study_id_int = int(study_id)
        except (TypeError, ValueError):
            return ScreenerResult("invalid_study_id", error="study_id must be an integer")

        optimized_schedule = data.get('optimized_schedule') or {}
        participants_data = data.get('participants_data') or []
        schedule_columns = data.get('schedule_columns') or []
        name_column = data.get('name_column')
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

        if not self.db_ready():
            return ScreenerResult("db_unavailable", error="데이터베이스 연결 실패")

        saved_at_dt = datetime.utcnow().replace(microsecond=0)
        with self.session_factory() as db_session:
            saved_record = self.repository.upsert_study_schedule(
                db_session,
                study_id=study_id_int,
                final_participants=calendar_snapshot,
                saved_at=saved_at_dt,
            )

        return ScreenerResult(
            "ok",
            {
                'success': True,
                'saved_at': saved_at_dt.isoformat() + 'Z',
                'saved_participants_count': total_count,
                'assigned_count': assigned_count,
                'unassigned_count': unassigned_total,
                'validation': {
                    'unassigned_count': unassigned_count,
                    'missing_count': missing_count,
                    'unassigned_participants': unassigned_participants[:10] if unassigned_participants else [],
                    'missing_participants': missing_participants[:10] if missing_participants else [],
                },
                'saved_record': saved_record,
            },
        )


screener_service = ScreenerService()
