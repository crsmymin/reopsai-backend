"""Prompt helpers for one-shot expert plan generation."""

from __future__ import annotations


def build_oneshot_combined_input(form_data):
    problem_definition = form_data.get('problemDefinition', '')
    study_name = form_data.get('studyName', '')
    methodologies = form_data.get('methodologies', [])
    target_audience = form_data.get('targetAudience', '')
    participant_count = form_data.get('participantCount', '')
    start_date = form_data.get('startDate', '')
    timeline = form_data.get('timeline', '')
    additional_requirements = form_data.get('additionalRequirements', '')

    return f"""
연구명: {study_name}

문제 정의: {problem_definition}

선택된 방법론: {', '.join(methodologies) if methodologies else '(AI가 추천)'}
조사 대상: {target_audience if target_audience else '(AI가 추천)'}
참여 인원: {str(participant_count) + '명' if participant_count else '(AI가 추천)'}
시작 예정일: {start_date if start_date else '(미정)'}
연구 기간: {timeline if timeline else '(AI가 추천)'}
추가 요청사항: {additional_requirements if additional_requirements else '(없음)'}
"""


def build_methodology_instruction(methodologies):
    if methodologies and len(methodologies) > 0:
        return f"""
**✅ 사용자가 선택한 방법론: {', '.join(methodologies)}**

**⚠️ 매우 중요한 방법론 필터링 규칙:**
1. **오직 선택된 방법론만 사용:** 위에 나열된 방법론만 계획서에 포함하세요.
2. **선택되지 않은 방법론 완전 제외:** 전문가가 추천한 다른 모든 방법론은 언급조차 하지 마세요.
3. **대상자 통합:** 선택된 방법론이 여러 개라도, 대상자는 하나의 통합된 그룹으로 구성하세요.
   - 각 방법론별로 대상자를 따로 구분하지 마세요.
   - 예: "인터뷰용 대상자 그룹 A, 사용성 테스트용 대상자 그룹 B" ❌
   - 예: "대상자 그룹: 인터뷰와 사용성 테스트를 함께 수행할 수 있는 통합 그룹" ✅
4. **일정 통합:** 선택된 방법론이 여러 개라도, 일정은 하나의 통합된 일정으로 작성하세요.
   - 방법론별로 일정을 분리하지 마세요.
   - 예: "2주차: 심층 인터뷰, 3주차: 사용성 테스트" ❌
   - 예: "2-3주차: 심층 인터뷰 및 사용성 테스트 동시 진행" ✅
5. **조사 방법 섹션:** 선택된 방법론들에 해당하는 조사 방법만 기술하세요.
"""
    return """
**⚠️ 방법론 미선택 안내:**
- 사용자가 방법론을 선택하지 않았으므로, 연구 목표에 가장 적합한 단일 방법론만 추천하세요.
- 여러 방법론을 나열하지 말고, 가장 적합한 1개 혹은 함께 수행하기 적절한 2개의 방법론만 선택하여 계획서를 작성하세요.
- 다른 방법론들은 언급하지 마세요.
"""


def build_expert_outputs(successful_experts):
    return "\n\n".join([
        f"### {result['expert']} 분석:\n{result['content']}"
        for result in successful_experts
    ])


def build_input_with_methodology(combined_input, methodology_result_content):
    return f"""{combined_input}

**[방법론 전문가 결과]**
{methodology_result_content}
"""


def one_shot_expert_configs(prompt_factory):
    return [
        ("연구 목표", prompt_factory.prompt_generate_research_goal),
        ("핵심 질문", prompt_factory.prompt_generate_core_questions),
        ("조사 대상", prompt_factory.prompt_generate_target_audience),
        ("참여자 기준", prompt_factory.prompt_generate_participant_criteria),
        ("분석 방법", prompt_factory.prompt_generate_analysis_method),
        ("일정 및 타임라인", prompt_factory.prompt_generate_timeline),
        ("액션 플랜", prompt_factory.prompt_generate_action_plan),
    ]


def build_oneshot_final_prompt(*, methodologies, combined_input, expert_outputs):
    methodology_instruction = build_methodology_instruction(methodologies)
    return f"""
8명의 전문가가 분석한 내용을 하나의 완전한 조사 계획서로 통합하세요.

**중요: 어떠한 서론, 인사말, 확인 메시지 없이 바로 결과물로 시작하세요.**
**절대로 '네,', '알겠습니다', '전문가로서', '~하겠습니다' 같은 응답으로 시작하지 마세요.**

{methodology_instruction}

원본 요청:
{combined_input}

전문가들의 분석:
{expert_outputs}

위 내용을 다음 구조로 **완전한 마크다운 계획서**로 작성하세요:
**중요:**
- 전문가 분석을 최대한 활용하되, 자연스럽게 통합
- 실무진이 바로 실행 가능한 수준으로 구체적으로 작성
- 마크다운 형식 준수
- 숫자 사이에 -를 넣는 경우 마크다운 형식이 잘못 출력되지않도록 주의하세요. (20-30대 의 경우 2030에 줄이 그어져 나오는 오류)
- 표( | )는 절대 사용 금지.

**출력형식**
# [프로젝트 명] 리서치 계획서

## 1. 배경 및 목적
## 2. 연구 질문 및 가설
## 3. 리서치 방법론
## 4. 대상 및 모집 기준
## 5. 일정
## 6. 데이터 수집 및 분석 방법
## 7. 예상 결과 및 활용 방안

"""
