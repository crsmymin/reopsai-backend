"""Study-helper prompt builders for plan generation."""

from __future__ import annotations


def build_study_helper_prompt(data):
    user_message = data.get('message', '')
    context = data.get('context', {})
    mode = data.get('mode', 'general')
    task = data.get('task')

    current_form = context.get('currentForm', {})
    project_name = context.get('projectName', '프로젝트')
    context_info = build_form_context_info(current_form, project_name)
    category = context.get('category', 'general')

    prompt_functions = study_helper_prompt_functions(
        current_form=current_form,
        context_info=context_info,
        user_message=user_message,
        task=task,
    )
    get_prompt = prompt_functions.get(category, prompt_functions['general'])
    helper_prompt = get_prompt()

    if not user_message:
        legacy_form = data.get('formData') or {}
        if legacy_form:
            user_message = "현재 폼 기반으로 간결 조언을 제공해 주세요."
            current_form = legacy_form
            project_name = context.get('projectName', '프로젝트')
            context_info = build_form_context_info(current_form, project_name)
            prompt_functions = study_helper_prompt_functions(
                current_form=current_form,
                context_info=context_info,
                user_message=user_message,
                task=task,
            )
            get_prompt = prompt_functions.get(category, prompt_functions['general'])
            helper_prompt = get_prompt()

    generation_config = {"temperature": 0.2, "max_output_tokens": 1000, "top_p": 0.9}
    if mode == 'help':
        generation_config = {"temperature": 0.1, "max_output_tokens": 1000, "top_p": 0.8}
    return helper_prompt, generation_config


def build_form_context_info(current_form, project_name):
    return f"""
현재 작성 중인 연구:
- 프로젝트: {project_name}
- 연구명: {current_form.get('studyName', '(미입력)')}
- 문제정의: {current_form.get('problemDefinition', '(미입력)')}
- 선택된 방법론: {', '.join(current_form.get('methodologies', [])) or '(미선택)'}
- 조사대상: {current_form.get('targetAudience', '(미입력)')}
- 희망일정: {current_form.get('timeline', '(미입력)')}
"""


def study_helper_prompt_functions(*, current_form, context_info, user_message, task):
    def get_methodology_prompt():
        concise_policy = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 추천 방법론은 2~3개만 제시하되, 각 방법론당 1~2문장으로 간단히 설명.
- 진행 방식, 장단점 상세 설명 금지. 선택 이유만 간단히 언급.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
- 주의사항은 1~2문장으로 제한.
"""
        return f"""
당신은 UX 리서치 방법론 전문가입니다.

{concise_policy}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 추천 방법론 2~3개 나열 (각 방법론당 1~2문장)
- 각 방법론의 선택 이유 간단히 언급
- 주의사항 1~2문장

답변:
"""

    def get_target_audience_prompt():
        concise_policy = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 2~3문장 또는 불릿(-) 3~5개로 전달.
- 대상자 정의와 모집 전략만 간단히 제시.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
        return f"""
당신은 UX 리서치 대상자 선정 전문가입니다.

{concise_policy}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 대상자 정의와 모집 전략 (2~3문장 또는 불릿 3~5개)
- 구체적인 실행 방안 간단히 제시

답변:
"""

    def get_timeline_prompt():
        concise_policy = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 2~3문장으로 전달.
- 일정 계획과 타임라인만 간단히 제시.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
        return f"""
당신은 UX 리서치 프로젝트 관리 전문가입니다.

{concise_policy}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 일정 계획 (2~3문장)
- 대략적인 타임라인과 주의사항 간단히 제시

답변:
"""

    def get_budget_prompt():
        concise_policy = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 3문장내외로 전달.
- 예산 배분과 핵심 포인트만 간단히 제시.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""
        return f"""
당신은 UX 리서치 예산 계획 전문가입니다.

{concise_policy}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 예산 계획과 핵심 포인트 (3문장내외)
- 비용 배분과 절약 방안 간단히 제시

답변:
"""

    def get_problem_definition_prompt():
        problem_def = current_form.get('problemDefinition', '').strip()
        concise_policy = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 전달.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
"""

        if problem_def and len(problem_def) > 20:
            return f"""
당신은 UX 리서치 문제 정의 전문가입니다.

{concise_policy}

사용자가 작성한 문제 정의:
{problem_def}

현재 상황:
{context_info}

사용자 질문: {user_message}

위 문제 정의를 검토하고 다음과 같이 답변하세요:
입력해주신 내용에 따르면 
- 문제 정의의 잘 된 부분 인정 (1~2문장)
- 구체적으로 보완하거나 명확히 할 부분 제안 (2~3문장)
- 필요시 개선 예시 제시

답변:
"""
        return f"""
당신은 UX 리서치 문제 정의 전문가입니다.

{concise_policy}

현재 상황:
{context_info}

사용자 질문: {user_message}

**답변 형식:**
- 좋은 문제 정의의 핵심 특징 설명 (2문장)
- 구체적인 예시 1~2개 제시

답변:
"""

    def get_general_prompt():
        concise_policy = """
[출력 규칙 - 반드시 준수]
- 인사/형식적 멘트/사과 금지.
- 핵심만 2~3문장 또는 불릿(-) 3~5개로 전달.
- 중복/장황함 금지. 불필요한 설명 금지.
- 요청한 범위 밖으로 확장 금지.
- 한국어 존댓말 일관 유지.
 - 목록이 아닐 경우 불릿(-)을 사용하지 말 것.
 - 표( | )와 헤딩( # )은 사용 금지. 단락/줄바꿈만 사용.
"""
        role_line = f"[도움 작업]: {task}" if task else ""
        return f"""
당신은 UX 리서치 전문가입니다.

{concise_policy}
{role_line}

[컨텍스트]
{context_info}

[사용자 입력]
{user_message}

[정확한 응답만 출력]
"""

    return {
        'methodology': get_methodology_prompt,
        'target': get_target_audience_prompt,
        'timeline': get_timeline_prompt,
        'budget': get_budget_prompt,
        'problem_definition': get_problem_definition_prompt,
        'general': get_general_prompt,
    }
