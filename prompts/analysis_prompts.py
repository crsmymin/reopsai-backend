import json

##조사계획서 진단
class DiagnosisPrompts:

    @staticmethod
    def prompt_diagnose_research_goal(research_plan, rag_context=""):
        """[진단 전문가 1/5] '연구 목표' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '신규 기능 A의 사용성 문제를 파악하여, 3분기 내 개선 방향을 도출한다'와 같이 "
            "배경, 대상, 목표가 명확해야 함.\n"
            "- **BAD:** '사용자 의견을 들어본다', '서비스를 개선한다'와 같이 추상적이면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "research_goal",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'

        return f"""
당신은 '연구 목표'의 명확성과 측정 가능성만 평가하는 QA 감사관입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_target_audience(research_plan, rag_context=""):
        """[진단 전문가 2/5] '조사 대상' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '수도권 거주 25-34세 남녀 중, 주 3회 이상 배달 음식을 주문하고 구독 서비스를 1개 이상 사용하는 자'와 같이 구체적이어야 함.\n"
            "- **BAD:** '젊은 사람들', '대학생', 'Z세대'와 같이 추상적이고 범위가 너무 넓으면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "target_audience",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'

        return f"""
당신은 '조사 대상' 정의의 구체성만 평가하는 리서치 리크루팅 전문가입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_core_questions(research_plan, rag_context=""):
        """[진단 전문가 3/5] '핵심 질문' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '사용자들은 신규 기능 A를 어떤 상황에서 주로 사용하는가?'와 같이 연구자가 '알고 싶은 것'이 질문 형태로 명시되어야 함.\n"
            "- **BAD:** '신규 기능에 대해 어떻게 생각하는가?'와 같이 질문이 너무 포괄적이면 '미흡'으로 판단.\n"
            "- **주의할 함정 (COUNTER-EXAMPLE):** '상품을 직접 찾아보세요'와 같이 사용자에게 시키는 '과업(Task)'이나, '정보탐색 행동 파악'과 같이 과업을 통해 관찰하려는 '목표'는 연구 질문이 아닙니다. 이런 경우는 명백한 '미흡'으로 판단해야 합니다."
        )
        json_example = '{\n  "check_item_key": "core_questions",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'

        return f"""
당신은 '연구 질문'과 '사용자 과업'의 차이를 명확히 구분하는 리서치 설계 전문가입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. '주의할 함정'에 해당하는 경우는 반드시 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_methodology_fit(research_plan, rag_context=""):
        """[진단 전문가 4/5] '조사 방법론' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '신규 기능의 초기 사용성 검증을 위해 UT(Usability Test)를 진행한다'와 같이 목표와 방법론의 연결이 명확해야 함.\n"
            "- **BAD:** 연구 목표와 관련 없이 'FGI를 진행한다'라고만 언급되면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "methodology_fit",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'
        
        return f"""
당신은 '조사 방법론'이 연구 목표에 적합한지만 평가하는 리서치 컨설턴트입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_participant_criteria(research_plan, rag_context=""):
        """[진단 전문가 5/5] '선별/제외 기준' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '최근 1개월 내 A기능 사용 경험 필수', '관련 업계 종사자 제외' 등 구체적인 조건이 명시되어야 함.\n"
            "- **BAD:** 별다른 기준 없이 'A기능 사용자'라고만 하거나, 기준이 아예 없으면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "participant_criteria",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'
        
        return f"""
당신은 '선별/제외 기준'의 구체성만 평가하는 QA 감사관입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_timeline(research_plan, rag_context=""):
        """[진단 전문가 6/7] '일정/타임라인' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '2주간 진행 (1주차: 인터뷰 5명, 2주차: 설문 100명)', '총 3주 소요 예상'과 같이 구체적인 일정이 있어야 함.\n"
            "- **BAD:** '빠르게 진행', '적당한 시간'과 같이 추상적이면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "timeline",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'

        return f"""
당신은 '일정/타임라인'의 구체성만 평가하는 프로젝트 매니저입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_analysis_method(research_plan, rag_context=""):
        """[진단 전문가 7/7] '분석 방법' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '정량 분석: SPSS를 활용한 빈도 분석, 교차 분석', '정성 분석: 테마 분석법을 통한 인사이트 도출'과 같이 구체적인 분석 방법이 있어야 함.\n"
            "- **BAD:** '데이터 분석', '결과 정리'와 같이 추상적이면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "analysis_method",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'

        return f"""
당신은 '분석 방법'의 구체성만 평가하는 데이터 분석 전문가입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_action_plan(research_plan, rag_context=""):
        """[진단 전문가 8/7] '결과 활용 계획' 항목만 전문적으로 진단"""
        guideline = (
            "- **GOOD:** '개발팀에 UX 개선 가이드 전달', '디자인팀에 와이어프레임 수정안 제공', '경영진 보고서 작성'과 같이 구체적인 활용 계획이 있어야 함.\n"
            "- **BAD:** '결과 활용', '개선 방안 제시'와 같이 추상적이면 '미흡'으로 판단."
        )
        json_example = '{\n  "check_item_key": "action_plan",\n  "pass": boolean,\n  "reason": "string",\n  "quote": "string"\n}'

        return f"""
당신은 '결과 활용 계획'의 구체성만 평가하는 비즈니스 전략 전문가입니다.
주어진 문서가 아래 가이드라인을 충족하는지 엄격하게 평가하십시오. 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

참고 원칙:
{rag_context}

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{research_plan}
</document_to_analyze>

<output_instructions>
- guideline을 기준으로 `pass` 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.
- JSON 예시: {json_example}
</output_instructions>
"""


##조사계획서 부분 생성
class GenerationPrompts:
    """생성(Generation) 관련 프롬프트를 담당하는 전문가 팀"""

    @staticmethod
    def prompt_generate_research_goal(research_plan, principles_context="", examples_context=""):
        """[생성 전문가 1/8] '연구 목표' 초안 생성"""
        return f"""
당신은 리서치 프로젝트의 목표를 설정하는 '수석 리서처'입니다.
사용자가 입력한 정보를 바탕으로, 명확하고 실행 가능한 '연구 목표'를 제안하십시오.

**[중요: 방법론 기반 연구 목표]**
- 사용자가 입력한 '문제 정의'를 핵심으로 연구 목표를 설정하세요.
- 해당 방법론으로 달성 가능한 목표만 제안하세요.
- 사용자가 입력한 '문제 정의'를 핵심으로 하되, 아래 '방법론 전문가 결과'에 명시된 방법론에 맞게 구체화하세요.
- '추가 요청사항'이 있다면 반드시 반영하세요.

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[출력 제약 조건]**
- 답변은 2~3 문장 내외로 간결하게 작성하세요.
- 불필요한 서론이나 결론 없이, 제안하는 내용의 핵심만 바로 작성하세요.

**[사용자 입력 정보 및 방법론 전문가 결과]**
{research_plan}
"""

    @staticmethod
    def prompt_generate_target_audience(research_plan, principles_context="", examples_context=""):
        """[생성 전문가 2/8] '조사 대상' 초안 생성"""
        return f"""
**[과업 지시]**
아래의 **[사고 절차]**에 따라 단계적으로 생각한 후, 최종 결과물을 [출력 형식]에 맞춰 생성하십시오.

핵심 요구사항:
- 사용자가 제공한 '조사 대상', '참여 인원', '방법론' 정보를 최우선으로 반영합니다.
- 조사 대상은 **단일 통합 그룹**을 기본으로 하되 필요 시 하위 그룹을 명확히 구분합니다.
- "피드백 제공 가능"처럼 자명하거나 의미가 약한 표현을 금지합니다.
- 총 인원이 주어졌다면 하위 그룹 인원 합이 일치하도록 분배합니다.
- 그 외 항목(선별 기준, 제외 기준, 모집 방법 등)은 다른 전문가가 있으니 절대 포함하지 않습니다.

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[사고 절차]**
1. 사용자 입력에서 대상의 필수 특성, 인원, 방법론 요구사항을 파악합니다.
2. 방법론에 따라 필요한 경험·행동 특성을 정리하되, 조건은 그룹 설명 안에서만 언급합니다.
3. 상호 배타적인 하위 그룹이 필요하면 최소한으로 나누고 각 그룹의 핵심 특성을 명시합니다.
4. 중복·모호한 표현을 제거하고, 총 인원과 그룹 인원이 맞는지 검토합니다.
5. 최종 결과를 **[출력 형식]**에 맞춰 작성합니다.

**[출력 형식]**
- 총 모집 인원: N명
- 대상자 그룹 구성: 번호 리스트
  1. 그룹명 - 모집 인원: N명, 핵심 특성/경험
  2. ...

**[사용자 입력 정보 및 방법론 전문가 결과]**
{research_plan}
"""

    @staticmethod
    def prompt_generate_core_questions(research_plan, principles_context="", examples_context=""):
        """[생성 전문가 3/8] '핵심 질문' 초안 생성"""
        return f"""
당신은 사용자의 숨겨진 니즈를 파악하는 '리서치 질문 설계 전문가'입니다.
사용자가 입력한 '문제 정의'를 해결하기 위해, 연구자가 알아내야 할 가장 중요한 '핵심 질문' 4~5가지를 제안하십시오.

**[중요: 방법론 기반 질문]**
- 아래 '방법론 전문가 결과'에 명시된 방법론으로 답변 가능한 핵심 질문을 제안하세요.
- 해당 방법론의 특성에 맞는 질문만 제안하세요. (예: 심층 인터뷰는 "왜", "어떻게" 같은 질문, 설문조사는 정량적 답변이 가능한 질문)
- 사용자가 입력한 '문제 정의'를 바탕으로 하되, 방법론에 맞게 구체화하세요.

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[출력 제약 조건]**
- 질문은 명확하고 간결한 형태여야 합니다.
- 불필요한 서론이나 결론 없이, 제안하는 질문 목록만 바로 작성하세요.
- **중요**: 실제 사용자에게 물어볼 구체적인 질문들이 아닌 연구자가 알아내야 할 질문(Research Questions)만을 제시하세요.

**[출력 형식]**
**연구질문 (Research Questions):**
- 연구자가 알아내야 할 핵심 질문들 (예: "A서비스를 이용하는 사용자가 B기능을 이용하면서 기대하는 점은 무엇인가")

**[사용자 입력 정보 및 방법론 전문가 결과]**
{research_plan}
"""

    @staticmethod
    def prompt_generate_core_questions_structured(research_plan, principles_context="", examples_context=""):
        """[생성 전문가] 구조화된 리서치 질문/가설 추천 (JSON 형식)"""
        return f"""
당신은 리서치 질문 설계 전문가입니다.
사용자 입력을 바탕으로 리서치 질문과 가설을 추천해주세요.

**[출력 형식 - 반드시 JSON으로 반환]**
다음 형식의 JSON 객체만 반환하세요 (설명이나 추가 텍스트 없이):

{{
  "research_questions": [
    {{
      "id": "q1",
      "content": "질문 내용",
      "reason": "추천 이유 (1-2문장)"
    }},
    {{
      "id": "q2",
      "content": "질문 내용",
      "reason": "추천 이유 (1-2문장)"
    }}
  ],
  "hypotheses": [
    {{
      "id": "h1",
      "content": "가설 내용",
      "reason": "추천 이유 (1-2문장)"
    }},
    {{
      "id": "h2",
      "content": "가설 내용",
      "reason": "추천 이유 (1-2문장)"
    }}
  ]
}}

**요구사항:**
- 리서치 질문 5-7개 추천 (research_questions)
- 가설 3-5개 추천 (hypotheses)
- 각 항목은 명확하고 구체적이어야 함
- 질문은 연구자가 알아내야 할 것(Research Questions)이어야 함
- 가설은 검증 가능한 형태여야 함
- JSON 형식으로만 응답 (설명, 서론, 결론 없이 순수 JSON만)

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[사용자 입력]**
{research_plan}
"""

    @staticmethod
    def prompt_revise_research_item(item_content, user_request, previous_items, research_plan, principles_context=""):
        """특정 리서치 질문/가설 수정 요청"""
        previous_items_json = json.dumps(previous_items, ensure_ascii=False, indent=2) if previous_items else "없음"
        
        return f"""
사용자가 다음 항목을 수정하고 싶어합니다.

**[수정 대상 항목]**
{item_content}

**[사용자 수정 요청]**
{user_request}

**[기존 추천 항목들]**
{previous_items_json}

**[전체 연구 맥락]**
{research_plan}

**요구사항:**
- 해당 항목을 사용자 요청에 맞게 수정하세요
- 기존 항목들의 맥락을 유지하면서 수정하세요
- 수정된 항목만 JSON 형식으로 반환하세요

참고 원칙:
{principles_context}

**[출력 형식 - 반드시 JSON으로 반환]**
{{
  "id": "기존_id 또는 새_id",
  "type": "research_question 또는 hypothesis",
  "content": "수정된 내용",
  "reason": "수정 이유"
}}
"""

    @staticmethod
    def prompt_refine_selected_items(selected_items, user_request, research_plan, principles_context=""):
        """선택된 항목들을 구체화"""
        selected_items_json = json.dumps(selected_items, ensure_ascii=False, indent=2) if selected_items else "없음"
        
        return f"""
사용자가 다음 항목들을 선택하고 추가 요청을 했습니다.

**[선택된 항목들]**
{selected_items_json}

**[사용자 추가 요청]**
{user_request}

**[전체 연구 맥락]**
{research_plan}

**요구사항:**
- 선택된 항목들을 더 구체화하거나 보완하세요
- 추가로 필요한 질문이나 가설이 있다면 제안하세요
- JSON 형식으로 반환하세요

참고 원칙:
{principles_context}

**[출력 형식 - 반드시 JSON으로 반환]**
{{
  "refined_items": [
    {{
      "id": "기존_id",
      "type": "research_question 또는 hypothesis",
      "content": "구체화된 내용",
      "reason": "구체화 이유"
    }}
  ],
  "additional_items": [
    {{
      "id": "새_id",
      "type": "research_question 또는 hypothesis",
      "content": "추가 제안 내용",
      "reason": "추가 이유"
    }}
  ]
}}
"""

    @staticmethod
    def prompt_generate_methodology_fit(research_plan, principles_context="", examples_context=""):
        """[생성 전문가 4/8] '조사 방법론' 초안 생성 - 선택된 방법론 평가 또는 추천"""
        return f"""
당신은 리서치 방법론 전문가 수석 컨설턴트입니다.
사용자가 입력한 정보를 바탕으로, 상황에 적절한 '조사 방법론'을 평가하거나 추천하십시오.

**[중요: 방법론 처리 규칙]**

위 '사용자 입력 정보'에서 "선택된 방법론" 정보를 먼저 확인하세요:

1. **"선택된 방법론"이 명시되어 있고, "(AI가 추천)"이 아닌 경우:**
   - 오직 명시된 방법론만 평가하고 설명하세요.
   - 다른 방법론은 언급하지 마세요.
   - 각 방법론에 대해 이름과 적합한 이유를 명확히 제시하세요.
   - 각 방법론은 다음과 같은 형식으로 작성하세요:
     **방법론명**: 적합한 이유 (1-2문장)

2. **"선택된 방법론"이 "(AI가 추천)"인 경우:**
   - 연구 목표와 문제 정의에 가장 적합한 1개 또는 함께 수행하기 적절한 2개의 방법론만 추천하세요.
   - 여러 방법론을 나열하지 말고, 가장 적합한 방법론(들)만 선택하세요.
   - 각 방법론에 대해 이름과 적합한 이유를 명확히 제시하세요.
   - 각 방법론은 다음과 같은 형식으로 작성하세요:
     **방법론명**: 적합한 이유 (1-2문장)

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[출력 제약 조건]**
- 불필요한 서론이나 결론 없이, 제안하는 방법론만 바로 작성하세요.
- 선택되지 않은 방법론은 언급하지 마세요.

**[사용자 입력 정보]**
{research_plan}
"""

    @staticmethod
    def prompt_generate_participant_criteria(research_plan, principles_context="", examples_context=""):
        """[생성 전문가 5/8] '선별 기준' 초안 생성"""
        return f"""
    당신은 리서치 참여자 스크리닝 전문가입니다. 아래 지침을 엄격히 따르십시오.

    핵심 요구사항:
    - 출력은 오직 참여자 선정 기준만을 명확한 bullet list로 제시합니다.
    - ❌ **절대 금지 항목 (반드시 제외):**
      - "피드백 제공 가능", "피드백 표현능력", "피드백 줄 수 있는 사람", "표현능력", "의사소통 능력", "설명 능력" 등과 같은 일반적인 참여 조건
      - "한국어 의사소통 가능", "인터뷰 참여 태도", "참여 동기" 등 모든 연구에서 공통으로 적용되는 자명한 조건
      - 연구 목적과 직접 관련 없는 일반적 능력이나 태도
    - ✅ **포함해야 할 기준:**
      - 연구 목적과 직접적으로 관련된 구체적인 경험, 행동, 특성만 포함 (예: "최근 1개월 내 A기능 사용 경험", "B서비스 이용 기간 6개월 이상")
    - 모집 방법, 동기, 참여 의지 등 스크리닝과 무관한 내용은 작성하지 않습니다.
    - 방법론별 요구사항을 참고하되, 조건은 중복 없이 구체적으로 작성합니다.

    참고 원칙:
    {principles_context}

    참고 예시:
    {examples_context}

    **[출력 형식]**
    - 선별 기준: bullet list (필수 경험/행동/속성 위주, 중복 금지)

    **[사용자 입력 정보 및 방법론 전문가 결과]**
    {research_plan}
    """

    @staticmethod
    def prompt_generate_timeline(research_plan, principles_context="", examples_context=""):
        """[생성 전문가] '일정 및 타임라인' 초안 생성 - 일정만 담당 (비용 제외)"""
        return f"""
당신은 리서치 프로젝트 관리 전문가입니다.

**[🚨 절대 규칙 - 최우선 순위]**
1. **사용자가 정한 시작일, 종료일, 기간은 절대 변경 불가**
   - 입력에 '시작 예정일', '종료일', '연구 기간'이 명시되어 있다면 그 날짜와 기간을 반드시 준수하세요
   - 사용자가 정한 일정을 임의로 늘리거나 줄이지 마세요
   - 예: 사용자가 "2024-01-01 ~ 2024-01-31 (4주)" 지정 시 → 반드시 이 기간 내에서만 일정 배분
   
2. **일정 배분만 담당**
   - 전체 기간은 이미 정해진 것으로 간주하고, 그 안에서 단계별 업무 배분만 수행하세요
   - 기간이 촉박해 보여도 절대 기간을 연장하지 마세요
   - 대신 동시 진행, 병행 작업 등으로 주어진 기간 내에 맞추세요

3. **주말 및 공휴일 제외 (매우 중요)**
   - 업무 일정은 평일(월~금)만 고려하세요
   - 주말(토, 일)과 공휴일은 업무 일정에 포함하지 마세요
   - 일정을 계산할 때 실제 업무 가능 일수(평일만)를 기준으로 하세요
   - 예: "1주차"는 실제로는 5일(월~금)을 의미합니다
   - 특정 날짜를 언급할 때는 주말/공휴일을 건너뛰세요

4. **추가 제약사항**
   - 입력에 명시된 기간을 초과하는 주차나 단계를 절대 추가하지 마세요
   - 사용자가 "3주"라고 했으면 4주차 이상은 언급 금지
   - 사용자가 "2024-02-28"까지라고 했으면 3월은 언급 금지

**[중요]**
- **일정만 생성하세요. 비용 관련 내용은 절대 포함하지 마세요.**
- 선택된 방법론이 여러 개라도, 일정은 하나의 통합된 일정으로 작성하세요.
- 방법론별로 일정을 분리하지 마세요.
- 최종 결과를 **[출력 형식]**에 맞춰 작성합니다.
- 표( | )와 헤딩( # )은 사용 금지. 단락/줄바꿈만 사용.

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[출력 제약 조건]**
- 주차별 또는 단계별로 구체적인 일정을 제시하세요.
- 불필요한 서론이나 결론 없이, 제안하는 일정만 바로 작성하세요.
- 일정은 단계별로 통합하여 작성하세요 (방법론별 분리 금지).

**[출력 형식]**
  - 1주차: 조사 설계 및 모집 시작
  - 2-3주차: 참여자 모집 및 선별
  - 4-5주차: 본조사 수행 (모든 방법론 통합 진행)
  - 6주차: 분석 및 보고서 작성

  **[사용자 입력 정보 및 방법론 전문가 결과]**
{research_plan}
"""

    @staticmethod
    def prompt_generate_analysis_method(research_plan, methodology_result, principles_context="", examples_context=""):
        """[생성 전문가 7/8] '분석 방법' 초안 생성 - 방법론 결과를 받아서 분석 방법 제시 (텍스트 형태)"""
        return f"""
당신은 리서치 데이터 분석 전문가입니다.
방법론 전문가가 제안한 방법론들을 바탕으로, 각 방법론으로 수집된 데이터를 효과적으로 분석할 수 있는 '분석 방법'을 제안하십시오.

**[중요: 방법론 결과 기반]**
- 아래 '방법론 전문가 결과'에 명시된 방법론들만을 기반으로 분석 방법을 제시하세요.
- 방법론 전문가 결과에 없는 방법론의 분석 방법은 언급하지 마세요.
- 사용자가 입력한 '추가 요청사항'에 분석 방법 관련 요구사항이 있다면 반영하세요.

**[방법론 전문가 결과]**
{methodology_result}

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[출력 제약 조건]**
- 위 '방법론 전문가 결과'에서 제안된 방법론들을 바탕으로 각각에 적합한 분석 방법을 제시하세요.
- 방법론 전문가 결과에 없는 방법론의 분석 방법은 절대 언급하지 마세요.
- 정성/정량 분석 방법을 구체적으로 제시하세요.
- 불필요한 서론이나 결론 없이, 제안하는 분석 방법만 바로 작성하세요.
- 각 분석 방법은 다음과 같은 형식으로 작성하세요:
  **방법론명**: 분석 방법 (구체적인 분석 방법 설명)

**[사용자 입력 정보]**
{research_plan}
"""

    @staticmethod
    def prompt_generate_action_plan(research_plan, principles_context="", examples_context=""):
        """[생성 전문가 8/8] '액션 플랜' 초안 생성"""
        return f"""
당신은 리서치 결과 활용 전문가입니다.
사용자가 입력한 정보를 바탕으로, 조사 결과를 실제 비즈니스에 적용할 수 있는 '액션 플랜'을 제안하십시오.

**[중요: 방법론 기반 액션 플랜]**
- 아래 '방법론 전문가 결과'에 명시된 방법론으로 수집될 결과를 기반으로 액션 플랜을 제안하세요.
- 해당 방법론의 특성에 맞는 결과 활용 방안을 제안하세요.
- 사용자가 입력한 '문제 정의'를 해결하기 위한 액션 플랜을 제안하세요.
- '추가 요청사항'에 결과 활용 관련 요구사항이 있다면 반드시 반영하세요.
- 사용자 입력과 너무 동떨어진 제안은 하지 마세요.

참고 원칙:
{principles_context}

참고 예시:
{examples_context}

**[출력 제약 조건]**
- 우선순위별 개선사항과 실행 방안을 제시하세요.
- 불필요한 서론이나 결론 없이, 제안하는 액션 플랜만 바로 작성하세요.
- 예시:
  - 우선순위 1: 사용성 문제 해결 방안
  - 우선순위 2: 기능 개선 로드맵

**[사용자 입력 정보 및 방법론 전문가 결과]**
{research_plan}
"""
    
    @staticmethod
    def prompt_polish_final_plan(research_plan, confirmed_plan_obj, rules_context_str, examples_context_str, selected_methodologies=None):
        """[생성 전문가] 확정된 계획 항목들을 바탕으로, 완전하고 상세한 실무용 조사계획서를 '창조'하는 수석 리서치 설계자"""

        # 선택된 방법론이 있으면 해당 방법론만 활용하도록 지시 추가
        methodology_instruction = ""
        if selected_methodologies and len(selected_methodologies) > 0:
            methodology_instruction = f"""
**[중요: 선택된 방법론 활용]**
사용자가 선택한 방법론: {', '.join(selected_methodologies)}
- 위에서 선택된 방법론만을 활용하여 조사계획서를 작성하세요.
- 선택되지 않은 방법론은 언급하지 마세요.
- 선택된 방법론에 맞는 구체적인 조사 방법과 절차를 상세히 기술하세요.
"""
        else:
            methodology_instruction = """
**[방법론 선택 안내]**
- 사용자가 특정 방법론을 선택하지 않았으므로, 연구 목표에 가장 적합한 방법론을 추천하여 작성하세요.
- 여러 방법론을 제안할 수 있지만, 각각의 적합성과 이유를 명확히 설명하세요.
"""

        # 모든 확정된 계획 데이터를 JSON으로 취합
        confirmed_plan_json = json.dumps(confirmed_plan_obj, ensure_ascii=False, indent=2)
        output_format_template = """
# [최종 조사계획서]

## 조사 배경
- [현재 상황과 문제의식을 2-3개 불릿포인트로 명확하게 제시]

## 리서치 목표
(1) [첫 번째 핵심 목표를 구체적으로 서술]
* [세부 목표 1]
* [세부 목표 2]

## 리서치 질문
**1. [첫 번째 영역명]**
- [구체적인 질문 1]
- [구체적인 질문 2]

## 조사대상
**표본 크기**: [대화 내용에서 추론한 인원 수. 만약 명시되지 않았다면, 방법론(UT/IDI 등)에 맞춰 '총 6~8명 (권장)'처럼 구체적인 숫자를 제안할 것]
**필수 조건**: 
1. [첫 번째 조건을 구체적으로]
2. [두 번째 조건을 구체적으로]

**대상 구분**:
1. [그룹1명] (모집 인원: [숫자]명)
   - 구분 기준: [기준]
   - 세부 조건: [조건 설명]

2. [그룹2명] (모집 인원: [숫자]명)
   - 구분 기준: [기준]
   - 세부 조건: [조건 설명]

## 조사 일정
* (지시: 아래 3단계를 모두 채우고, 대화 내용에 일정이 없다면 'n주차'로 제안할 것)
* [단계1: 조사 설계 및 모집]: [n주차 또는 날짜 범위]
* [단계2: 본조사 수행]: [n주차 또는 날짜 범위]
* [단계3: 분석 및 보고]: [n주차 또는 날짜 범위]

## 조사 방법
### 1. [첫 번째 조사방법명]
**목적**
- [목적 1]
- [목적 2]

**조사 내용**
#### 주제영역1
- [조사항목1]
- [조사항목2]
#### 주제영역2
- [조사항목1]
"""

        return f"""
당신은 15년 경력의 시니어 UX 리서처이자, 리서치 프로젝트를 총괄하는 리드입니다.
당신의 임무는 아래에 제공된 모든 정보를 바탕으로, 실무진이 바로 실행에 옮길 수 있는 수준의 **상세하고 완전한 조사계획서**를 작성하는 것입니다.

**[원본 계획서의 전체 맥락]**
{research_plan}

**[확정된 계획 데이터 (전체)]**
{confirmed_plan_json}

**[참고 사례 (DB 검색 결과)]**
{examples_context_str}

**[🚨 절대 규칙 - 최우선 순위]**
1. **사용자가 정한 일정 관련 정보는 절대 변경 불가**
   - 시작 예정일, 종료일, 연구 기간이 원본 계획서나 확정 데이터에 명시되어 있다면 그대로 사용하세요
   - 일정 기간을 임의로 늘리거나 줄이지 마세요
   - 명시된 일정 범위를 초과하는 주차나 날짜를 추가하지 마세요
   
2. **조사 규모 변경 금지**
   - 기존 계획서 내에 정의된 참여자 수, 그룹 구성 등 조사 규모는 변경하지 마세요
   
3. **주말 및 공휴일 제외**
   - 업무 일정은 평일(월~금)만 고려하세요
   - 주말(토, 일)과 공휴일은 업무 일정에 포함하지 마세요
   - 특정 날짜를 제시할 때는 주말/공휴일을 건너뛰세요
   
4. **일정 배분만 수행**
   - 사용자가 정한 전체 기간 내에서 단계별 업무 배분만 수행하세요
   - 일정이 명시되어 있으면 그 일정을 그대로 따르고, 없으면 n주차로 구분하여 작성하세요

**[과업 지시]**
1.  위 '핵심 정보'를 바탕으로 내용을 확장하고, **정보가 부족한 부분(조사 배경, 세부 조사 내용 등)은 가장 논리적이고 현실적인 내용으로 추론하여 채워 넣으십시오.**
2.  출력물은 반드시 아래의 **[출력 포맷]**을 엄격하게 준수해야 합니다.
3.  **절대 규칙: 마크다운 테이블 형식(| 파이프 문자를 사용한 표)은 절대 사용하지 마십시오. 모든 정보는 리스트나 단락 형식으로 표현하십시오.**
4.  불필요한 서론이나 결론 없이, 완성된 계획서 본문만 생성하십시오.

**[출력 포맷]**
{output_format_template}
"""

##조사계획서 생성
class PlanGeneratorPrompts:
    
    @staticmethod
    def prompt_summarize_conversation(stage_title, conversation_history, selected_methodologies=None):
        """[요약 전문가] 한 단계의 대화 기록을 간결하게 요약 (방법론 정보 포함)"""
        
        methodology_info = ""
        if selected_methodologies and len(selected_methodologies) > 0:
            methodology_info = f"\n\n**선택된 방법론:** {', '.join(selected_methodologies)}"
        
        return f"""
당신은 대화의 핵심 내용을 요약하는 전문 에디터입니다.
아래의 대화 기록을 바탕으로, '{stage_title}' 단계에서 사용자가 최종적으로 결정한 핵심 내용을 3개의 불릿포인트로 간결하게 요약해주십시오.

<대화 기록>
{conversation_history}
</대화 기록>
{methodology_info}

**[요약 결과 (핵심 내용만)]**
- **중요**: 사용자가 선택한 방법론이 있으면 그 방법론을 우선적으로 반영하세요. 하지만 AI가 제공한 조사 대상 분석과 참가자 기준 설계도 함께 포함하세요.
- 방법론이 선택된 경우 반드시 "선택된 방법론: [방법론명들]"을 포함하세요.
- AI가 제공한 조사 대상 분석과 참가자 기준 설계 내용도 요약에 포함하세요.
- 사용자 입력과 AI 분석을 종합하여 완성된 요약을 작성하세요.
"""


##스크리닝 설문 생성
class SurveyBuilderPrompts:
    """설문조사 생성을 담당하는 전문가 팀"""
    @staticmethod
    def prompt_generate_survey_structure(
        research_plan_content, 
        key_variables_json, 
        balance_variables_json, 
        screening_criteria_json,
        rules_context_str,
        examples_context_str
    ):
        json_example = (
        "    \n"
        "    {\n"
        '      "form_elements": [\n'
        '        {\n'
        '          "id": "q1",\n'
        '          "element": "RadioButtons",\n'
        '          "text": "이 설문은 OOO 서비스 인터뷰 참여 적합성을 확인하기 위한 사전 질문입니다. 연구 목적으로 응답이 활용되는 것에 동의하시나요?",\n'
        '          "required": true,\n'
        '          "options": []\n'
        '        },\n'
        '        {\n'
        '          "id": "q2",\n'
        '          "element": "Checkboxes",\n'
        '          "text": "최근 6개월 내 OOO 서비스를 통해 수행한 주요 활동을 모두 선택해주세요.",\n'
        '          "required": true,\n'
        '          "options": []\n'
        '        },\n'
        '        {\n'
        '          "id": "q3",\n'
        '          "element": "RadioButtons",\n'
        '          "text": "OOO 서비스는 평소 얼마나 이용하시나요?",\n'
        '          "required": true,\n'
        '          "options": []\n'
        '        },\n'
        '        {\n'
        '          "id": "q4",\n'
        '          "element": "Checkboxes",\n'
        '          "text": "추후 원활한 일정 조율을 위해 아래 일정 중 조사 참여를 희망하시는 날짜 및 시간대를 모두 선택해주세요.",\n'
        '          "required": true,\n'
        '          "options": []\n'
        '        },\n'
        '        {\n'
        '          "id": "q5",\n'
        '          "element": "TextArea",\n'
        '          "text": "응답자 확인 및 인터뷰 일정 조율을 위해 성함과 연락 가능하신 휴대전화 번호를 입력해 주세요. (입력 예시 : 홍길동/01012345678) 설문 응답 내용을 검토한 뒤, 인터뷰 대상자로 선정되신 분들께는 7/28(월) 전에 따로 연락드리겠습니다.",\n'
        '          "required": true\n'
        '        }\n'
        '      ],\n'
        '      "target_groups": [\n'
        '        {"name": "그룹 1", "targetCount": 10, "description": "그룹 설명"}\n'
        '      ],\n'
        '      "key_variables": [\n'
        '        {"variable_name": "변수명", "description": "변수 설명"}\n'
        '      ],\n'
        '      "balance_variables": [\n'
        '        {"variable_name": "변수명", "description": "변수 설명"}\n'
        '      ]\n'
        '    }\n'
        "    ```"
    )
        return f"""
당신은 구조화된 스크리닝 설문을 설계하는 전문가입니다. 아래 자료를 토대로 **블록(의미 단위) 기반** 설문 구조를 JSON으로 완성하세요.

**입력 데이터**
1. `research_plan_content`: 연구 배경, 대상, 주요 니즈 요약
2. `key_variables_json`: 포함·제외 기준, 필수 스크리닝 변수 목록
3. `balance_variables_json`: 균형 확보를 위해 모니터링해야 할 변수 목록
4. `screening_criteria_json`: (가장 중요) 필수 조건을 **객관적 행동 지표로 정규화**한 스크리닝 기준 목록
5. `rules_context_str` / `examples_context_str`: 최신 RAG 검색 결과(준수 원칙, 모범 사례)

**출력 형식 (절대 준수)**
- 반드시 `{{ "blocks": [...], "form_elements": [...], "target_groups": [...], "key_variables": [...], "balance_variables": [...] }}` 형태의 순수 JSON만 출력합니다.
- 추가 설명, 머리말, 번호 목록, 마크다운 코드 블록은 절대 금지입니다. 순수 JSON만 출력하세요.
- `form_elements` 배열 길이는 최소 8개 이상이어야 하며, 변수와 조건 수에 따라 필요한 만큼 자유롭게 조정하세요.
- 불필요한 질문을 넣어 숫자를 채우지 말고, 각 핵심 변수를 정확히 커버하는지를 우선 검토하세요.

**blocks 필수 필드 (블록 단위 설계)**
- `blocks`는 화면에 표시될 의미 단위 섹션 정의 배열입니다.
- 각 블록 객체는 다음 필드를 반드시 포함해야 합니다:
  - `id`: 블록 식별자 (예: "A_qualification", "B_demographics", "C_open_ended", "D_ops", "intro")
  - `title`: 블록 제목 (예: "Block A: 필수 자격 (Qualification)")
  - `kind`: 다음 중 하나: `"qualification" | "demographics" | "open_ended" | "ops" | "intro"`
  - `ai_comment`: 블록 역할을 설명하는 짧은 코멘트
- `blocks`의 순서가 곧 화면 표시 순서입니다.

**form_elements 필수 필드**
각 문항 객체는 다음 필드를 반드시 포함해야 합니다:
- `id`: 고유 식별자. "q1", "q2", "q3" 형식으로 소문자로 시작하며 배열 순서에 맞춰 연속 번호를 부여합니다.
- `element`: 문항 타입. 다음 중 하나여야 합니다:
  * `"RadioButtons"`: 객관식 (단일 선택)
  * `"Checkboxes"`: 다중 선택
  * `"TextInput"`: 단답형 주관식 (짧은 답변 예상)
  * `"TextArea"`: 서술형 주관식 (긴 답변 예상)
- `block_id`: 이 문항이 속한 블록의 id. 반드시 `blocks[].id` 중 하나여야 합니다.
- `text`: 문항 텍스트. 명확하고 중립적인 표현을 사용합니다.
- `required`: 항상 `true`입니다.
- `options`: RadioButtons 또는 Checkboxes인 경우에만 포함하며, 빈 배열 `[]`로 설정합니다. (선택지는 이후 단계에서 생성됩니다)
  TextInput 또는 TextArea인 경우 `options` 필드는 포함하지 않습니다.

**element 타입 선택 가이드**
- 단일 선택이 필요한 객관식 → `"RadioButtons"`
- 다중 선택이 필요한 객관식 → `"Checkboxes"`
- 짧은 답변이 예상되는 주관식 (예: 이름, 연락처, 숫자) → `"TextInput"`
- 긴 답변이 예상되는 주관식 (예: 의견, 설명, 경험) → `"TextArea"`

**문항 배치/블록 구성 규칙 (매우 중요)**
1. `blocks`는 아래 순서를 기본으로 사용합니다(필요 시 일부 생략 가능):
   - intro: 인사/안내/동의
   - A_qualification (qualification): 필수 자격(통과/탈락 기준 측정)
   - B_demographics (demographics): 배경 정보(쿼터/분포 변수)
   - C_open_ended (open_ended): 심층 질문(적합성 판단을 위한 서술형)
   - D_ops (ops): 운영 정보(가능 시간/연락 등)
2. **핵심은 screening_criteria_json**입니다. `qualification` 블록의 문항들은 `screening_criteria_json`의 기준을 빠짐없이 측정하도록 설계합니다.
3. `key_variables_json`는 참고로만 사용하되, 추상/태도 표현은 절대 그대로 쓰지 말고 행동 지표로 재표현합니다.
4. `demographics` 블록은 `balance_variables_json`를 커버합니다.
5. TextArea(서술형) 문항은 최대 1개까지만 허용하며 반드시 `C_open_ended` 블록에 배치합니다.

**문항 작성 규칙**
- 포함·제외 기준 변수는 예/아니오 대신 실제 행동·경험을 구분하는 객관식으로 측정합니다.
- 특정 기능·채널·기간을 확인할 때는 "최근 △개월 내 OOO 기능을 이용한 경험을 선택하세요"처럼 구체적 지시와 측정 단위를 명시합니다.
- 한 문항에는 하나의 측정 목적만 담고, 중립적 표현으로 편향을 피하세요.
- 동의 문항을 통과한 응답자에게만 노출되어야 하는 개인정보·연락처 수집 문항이 필요한 경우 마지막에 배치합니다.
- TextArea(서술형) 문항은 최대 1개까지만 허용하며 반드시 `C_open_ended` 블록에 배치합니다.
- 서비스명은 "OOO"이라고 출력하지 않고 요청에 따라 적절한 내용을 작성하세요.

**질문 품질 기준**
- 명확성: 모호한 표현을 피하고 측정 대상과 기간·조건을 구체적으로 제시합니다.
- 간결성: 한 문항에 하나의 목적만 담고, 불필요한 수식어를 제거합니다.
- 편향 방지: 중립적 문장을 사용하며 유도형 질문을 금지합니다.
- 측정 목적 명시: 각 문항이 어떤 변수(`key_variables` 또는 `balance_variables`)를 측정하는지 내부적으로 일관되게 대응시킵니다.
- RAG 학습: `rules_context_str`, `examples_context_str`의 최신 원칙을 반영하여 어투와 구조를 정제합니다.

**금지 항목**
- 장애 여부, 한국어 인터뷰 진행 가능 여부, UX 개선에 대한 의견 제시 가능 여부 등 지나치게 일반적이거나 불필요한 관리 항목
- 사용자 경험과 무관한 서비스 만족도·추상적 의견 질문

**[분석 자료]**
1. **조사 계획서:**
{research_plan_content}

2. **핵심 선별 변수 (Inclusion/Exclusion):**
{key_variables_json}

3. **핵심 균형 변수 (Balance):**
{balance_variables_json}

4. **정규화된 스크리닝 기준 (객관적 행동 지표):**
{screening_criteria_json}

**[필수 준수 원칙 (DB 검색 결과)]**
{rules_context_str}

**[참고 사례 (DB 검색 결과)]**
{examples_context_str}

**출력 검증 체크리스트**
- JSON 이외의 텍스트가 포함되어 있지 않은가?
- `blocks`, `form_elements`, `target_groups`, `key_variables`, `balance_variables` 필드가 모두 포함되어 있는가?
- 모든 문항에 필수 필드(`id`, `element`, `block_id`, `text`, `required`)가 빠짐없이 포함되어 있는가?
- `id`가 배열 순서와 맞춰 "q1"부터 연속 번호로 부여되었는가?
- `element`가 "RadioButtons", "Checkboxes", "TextInput", "TextArea" 중 하나인가?
- RadioButtons/Checkboxes인 경우 `options` 필드가 빈 배열 `[]`로 포함되어 있는가?
- TextInput/TextArea인 경우 `options` 필드가 없는가?
- TextArea 문항이 1개 이하이며 `C_open_ended` 블록에 배치되었는가?
- 각 문항이 금지 항목을 회피하며 명확한 측정 목적을 갖추었는가?
- qualification 블록 문항이 demographics(균형 변수) 블록 문항보다 앞에 배치되었는가?

**[출력 예시]**
{json_example}
"""

    @staticmethod
    def prompt_generate_all_answer_options(questions_json_chunk, relevant_examples_str):
        """설문 문항 목록과 실무 예시를 분석하여, 모든 객관식 문항에 대한 현실적이고 논리적인 선택지를 생성하는 AI 전문가"""
        json_output_example = (
            "```json\n"
            "{\n"
            '  "options": {\n'
            '    "q1": [\n'
            '      {"value": "opt1", "text": "20대"},\n'
            '      {"value": "opt2", "text": "30대"},\n'
            '      {"value": "opt3", "text": "40대"},\n'
            '      {"value": "opt4", "text": "50대 이상"}\n'
            '    ],\n'
            '    "q3": [\n'
            '      {"value": "opt1", "text": "남성"},\n'
            '      {"value": "opt2", "text": "여성"}\n'
            '    ]\n'
            '  }\n'
            '}\n'
            "```"
        )
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 설문 문항 목록과 실무 예시를 분석하여, 모든 객관식 문항에 대한 현실적이고 논리적인 선택지를 생성하는 AI 전문가입니다.

**[참고 자료 (Vector DB 검색 결과 - 설문 원칙 및 모범 예시)]**
{relevant_examples_str}

**[선택지를 생성할 질문 목록 (JSON 형식)]**
{questions_json_chunk}

**[과업 지시 및 매우 중요한 규칙]**
1. **분석:** 위에 주어진 '질문 목록 (JSON 형식)'에 있는 모든 문항 객체를 분석합니다. `element`가 "TextInput" 또는 "TextArea"인 문항은 무시합니다.
2. **생성:** 각 객관식 문항(`element`가 "RadioButtons" 또는 "Checkboxes")에 대해, **참고 자료들을 학습하여** 가장 적절하고 실무적인 선택지를 3~5개 생성합니다.
3. **출력 형식 (필수):** 최종 결과물은 `options`라는 키를 가진 단일 JSON 객체여야 합니다.
4. **`options` 객체 내용:** 
   - Key는 **문항 객체의 `id`** (예: "q1", "q2") 이어야 합니다. (소문자로 시작)
   - Value는 선택지 객체들의 배열이어야 합니다: `[{{"value": "...", "text": "..."}}, ...]`
   - 각 선택지 객체는 `value`와 `text` 필드를 포함해야 합니다.
   - `value`는 고유한 식별자 (예: "opt1", "opt2")이며, `text`는 사용자에게 표시될 선택지 텍스트입니다.
5. 특정 서비스·앱·채널 사용 여부를 확인할 때는 구체적인 선택지 리스트를 제시하고, 관련 내용을 모르는 경우에는 "서비스A", "서비스B"와 같이 사용자가 채워넣을 수 있도록 제시해야 합니다.

**[출력 예시]**
{json_output_example}
"""

##참여자 선정
class ScreenerPrompts:
    """스크리닝 및 최종 선별 관련 프롬프트를 담당하는 전문가 팀"""
    @staticmethod
    def prompt_analyze_plan(research_plan):
        # """...""" 대신 문자열 결합을 사용하여 마크다운 충돌 문제를 원천적으로 해결합니다.
        json_example = (
            "    ```json\n"
            "    {\n"
            '      "key_variables": [\n'
            '        {"variable_name": "핵심 변수 1(Inclusion/Exclusion)", "description": "변수 1에 대한 설명 (예: 최근 1개월 내 A 기능 사용자)"}\n'
            '      ],\n'
            '      "target_groups": [\n'
            '        {"name": "그룹 1 명칭", "targetCount": 10, "description": "그룹 1의 핵심 특징 (예: 2-30대 고관여 사용자)"}\n'
            '      ],\n'
            '      "balance_variables": [\n'
            '        {"variable_name": "투자 규모", "description": "고액/소액 투자자 분포 균등 배분 필요"},\n'
            '        {"variable_name": "앱 이용 빈도", "description": "헤비 유저와 라이트 유저 분포 균등 배분 필요"}\n'
            '      ]\n'
            '    }\n'
            "    ```"
        )

        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

    당신은 리서치 계획 분석 전문가입니다. 주어진 리서치 계획서를 분석하여, 참가자 선별에 필요한 핵심 변수와 타겟 그룹 구성을 JSON 형식으로 추출해주세요.

    **리서치 계획서:**
    {research_plan}

    **추출 항목:**
    1.  `key_variables`: **연구 목적과 직접적으로 관련된 선별 변수만** 추출 (리스트 형태). 각 변수는 `variable_name` (이름), `description` (변수 설명 혹은 왜 선정되었는지 간단한 이유)을 포함해야 합니다.
       - ✅ **포함해야 할 변수**: 연구 주제/서비스와 직접 관련된 사용 경험, 행동, 특성 (예: "앱 사용 기간", "카드 앱 사용 개수", "특정 기능 사용 경험")
       - ❌ **제외해야 할 변수**:
         - 일반적인 참여 조건: 인터뷰/테스트 참여 태도, 개인 정보 제공 동의 여부, 한국어 의사소통 능력, 만 19세 이상, 참여 동기
         - 제외 기준만을 위한 변수: 임직원 여부, 유사 리서치 참여 경험, 업종 종사 여부, 극단적 편향
         - 설명 능력/답변 품질 관련: 답변 설명 능력, 서술 능력
         - 법적/행정적 요건: 개인정보 동의, 법적 동의 능력
       - ⚠️ **주의**: 타겟 그룹 구분에 **필수적인** 변수만 포함하세요. 모든 연구에서 공통으로 적용되는 일반적 조건은 제외하세요.

    2.  `target_groups`: 모집할 타겟 그룹들 (리스트 형태). 각 그룹은 `name` (그룹명), `targetCount` (목표 인원), `description` (핵심 특징)을 포함해야 합니다.

    3.  `balance_variables`: 선발/제외 조건은 아니지만, **최종 선발 시 고르게 분포되어야 하는 변수** (예: 연령대별, 투자 규모별, 이용 빈도별 균등 배분 등).

    **[핵심 변수 선정 기준 (매우 중요)]**
    - `key_variables`에는 **이 특정 연구를 위해 반드시 필요한 변수만** 포함하세요.
    - 예: "SOL Pay 앱 사용 시기" → ✅ 포함 (타겟 그룹 구분에 필수)
    - 예: "인터뷰 참여 태도" → ❌ 제외 (모든 연구 공통)
    - 예: "한국어 의사소통 능력" → ❌ 제외 (기본 요건)
    - 예: "임직원 여부" → ❌ 제외 (제외 기준일 뿐, 핵심 변수가 아님)

    **[응답 형식 및 규칙 (매우 중요)]**
    1.  당신의 응답은 반드시 마크다운 JSON 코드 블록으로만 감싸야 합니다.
    2.  JSON 객체 외에 어떠한 설명이나 추가 텍스트도 포함해서는 안 됩니다.

    **[출력 예시]**
    {json_example}
"""

    @staticmethod
    def prompt_map_variables(key_variables, balance_variables, csv_columns, csv_schema=None, column_metadata=None):
        key_variables_text = json.dumps(key_variables, indent=2, ensure_ascii=False)
        balance_variables_text = json.dumps(balance_variables, indent=2, ensure_ascii=False)
        csv_columns_text = ", ".join(csv_columns)
        formatted_key_vars_block = f"```json\n{key_variables_text}\n```"
        formatted_balance_vars_block = f"```json\n{balance_variables_text}\n```"
        
        # CSV 스키마 정보 추가
        csv_schema_block = ""
        if csv_schema:
            csv_schema_text = json.dumps(csv_schema, indent=2, ensure_ascii=False)
            csv_schema_block = f"""

**중요: CSV 컬럼 정보**
다음은 각 컬럼의 실제 내용 샘플입니다. `values_sample`을 보고 해당 컬럼이 실제 질문인지 소개 문구인지 판단하세요.
```json
{csv_schema_text}
```
"""
        
        # ✨ 메타데이터 정보 추가 (매핑 단계: 상위 10개만)
        metadata_block = ""
        if column_metadata:
            summary_list = []
            for col_name, meta in column_metadata.items():
                # 매핑 단계에서는 top_for_mapping 사용 (상위 10개)
                if 'top_for_mapping' in meta and meta['top_for_mapping']:
                    top_for_mapping = meta['top_for_mapping']
                    summary_str = f"  - '{col_name}': " + ", ".join(
                        [f"{item['value']} ({item['percentage']}%)" for item in top_for_mapping[:5]]  # 상위 5개만 표시
                    )
                    if len(top_for_mapping) > 5:
                        summary_str += f" ... (상위 {len(top_for_mapping)}개)"
                    summary_list.append(summary_str)
            
            if summary_list:
                metadata_block = f"""

**✨ 컬럼별 응답 분포 (상위 응답 샘플)**
다음은 각 컬럼의 주요 응답 분포입니다. 이를 참고하여 변수를 매핑하세요.
(전체 보기는 스코어링 단계에서 제공됩니다)

{chr(10).join(summary_list[:20])}  
{'... (더 많은 컬럼 생략)' if len(summary_list) > 20 else ''}
"""
        
        json_example = """
    ```json
    {
      "key_variable_mappings": [
        {
          "key_variable": "핵심 변수명",
          "description": "핵심 변수 설명",
          "mapped_column": "Q1_사용경험",
          "confidence": "High",
          "notes": "변수명과 설명이 컬럼 내용과 직접적으로 일치함."
        },
        {
          "key_variable": "신규 기능 사용 의향",
          "description": "새로운 기능에 대한 사용 의향",
          "mapped_column": null,
          "confidence": "None",
          "notes": "CSV 데이터에 해당 질문이 존재하지 않음."
        }
      ],
      "balance_variable_mappings": [
        {
          "balance_variable": "균형 변수명",
          "description": "균형 변수 설명",
          "mapped_column": "Q4_연령대",
          "confidence": "Medium",
          "notes": "연령대 분포 균형을 위해 사용"
        }
      ]
    }
    ```
"""
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 데이터 분석가입니다. 리서치 계획의 '핵심 변수'.'균등분포 변수'와 설문 데이터의 'CSV 컬럼'을 논리적으로 맵핑하는 작업을 수행해야 합니다.

**중요:** 위의 CSV 컬럼 정보를 참고하여, "아래 조사 개요를 확인 후..."와 같은 소개/안내 문구 컬럼은 제외하고, 실제 질문에 대한 답변이 들어있는 컬럼만 매핑하세요.
{csv_schema_block}
{metadata_block}

**1. 리서치 핵심 변수:**
{formatted_key_vars_block}

**2. 리서치 균형 변수:**
{formatted_balance_vars_block}

**3. CSV 파일의 사용 가능한 컬럼:**
`[{csv_columns_text}]`

**지시사항:**
1. 각 '핵심 변수'에 가장 적합한 'CSV 컬럼'을 하나씩 맵핑해주세요. 맵핑의 정확도(Confidence)를 'High', 'Medium', 'Low'로 표기하고, 만약 적절한 컬럼이 없다면 `mapped_column`을 `null`로 지정해주세요.
2. 동일한 방식으로 모든 '균형 변수'에 대해서도 가장 적합한 CSV 컬럼을 매핑해주세요. 균형 변수가 여러 컬럼과 관련 있을 경우 가장 대표적인 컬럼을 선택하고, 추가 설명이 필요하면 `notes`에 기록하세요.

**응답 형식 (JSON만 응답):**
{json_example}
"""

    @staticmethod
    def prompt_normalize_screening_criteria(research_plan, key_variables_json):
        """
        [NEW] 계획서의 '필수 조건'을 측정 가능한 행동 지표(스크리닝 기준)로 정규화합니다.

        목적:
        - 추상 조건(예: 관심 있음, 잘 설명함)을 금지하고,
          행동/경험/기간/빈도/상황 기반으로 재표현한 기준을 생성합니다.
        - 이후 설문 구조 생성 단계에서 이 기준을 그대로 문항으로 변환할 수 있게 합니다.
        """
        json_example = (
            "{\n"
            '  "screening_criteria": [\n'
            "    {\n"
            '      "criterion_id": "c1",\n'
            '      "original_variable_name": "최근 1개월 내 A 기능 사용 경험",\n'
            '      "behavioral_metric": "최근 30일 내 A 기능을 1회 이상 실제로 사용",\n'
            '      "timeframe": "최근 30일",\n'
            '      "threshold_or_frequency": "1회 이상",\n'
            '      "recommended_element": "RadioButtons",\n'
            '      "disqualify_when": "사용 경험 없음(0회) 선택 시",\n'
            '      "notes": "예/아니오 대신 구체적 빈도로 측정"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 리서치 스크리닝 설계 전문가입니다.
아래 '리서치 계획서'와 '핵심 선별 변수(key_variables)'를 보고, 각 변수를 **측정 가능한 행동 지표**로 정규화하세요.

**핵심 원칙(반드시 준수):**
1. **객관적 행동 지표로 변환**: '관심 있음', '잘 설명함' 같은 추상/태도/능력 조건은 금지. 반드시 행동/경험/기간/빈도/상황으로 표현.
2. **기간/단위 명시**: 가능한 한 "최근 △일/△주/△개월", "주 △회", "월 △회"처럼 측정 단위를 명시.
3. **문항 설계 가능 형태**: 각 기준은 설문 문항으로 바로 옮길 수 있어야 하며, `recommended_element`를 아래 중 하나로 지정:
   - "RadioButtons" (단일 선택)
   - "Checkboxes" (다중 선택)
   - "TextInput" (짧은 수치/기간 입력 등)
4. **탈락 기준은 서술형으로만**: 여기서는 실제 로직을 구현하지 않고, `disqualify_when`에 “어떤 응답이면 부적합인지”를 자연어로 명시.
5. **중복 제거/정리**: 서로 같은 의미의 기준은 합치고, 너무 광범위하면 최소 2개로 쪼개되(최소화), 각 항목은 한 가지 목적만.

**입력**
1) 리서치 계획서:
{research_plan}

2) 핵심 선별 변수(key_variables):
{key_variables_json}

**출력 형식**
- 반드시 아래 예시와 동일한 키를 가진 순수 JSON만 반환.
- `screening_criteria`는 배열이며, `criterion_id`는 "c1", "c2" ... 연속 번호.

출력 예시:
{json_example}
"""

    @staticmethod
    def prompt_create_scoring_criteria(target_groups, variable_mappings, csv_info_schema=None, column_metadata=None):
        """스코어링 기준 생성 프롬프트 - 개선 버전"""
        target_groups_text = json.dumps(target_groups, indent=2, ensure_ascii=False)
        mappings_text = json.dumps(variable_mappings, indent=2, ensure_ascii=False)
        formatted_groups_block = f"```json\n{target_groups_text}\n```"
        formatted_mappings_block = f"```json\n{mappings_text}\n```"
        
        # CSV 스키마 정보도 함께 제공
        csv_schema_block = ""
        if csv_info_schema:
            csv_schema_text = json.dumps(csv_info_schema, indent=2, ensure_ascii=False)
            csv_schema_block = f"""
**3. CSV 컬럼 정보:**
```json
{csv_schema_text}
```
"""
        
        # ✨ 메타데이터 정보 추가 (스코어링 단계: 매핑된 컬럼만 전체 보기)
        metadata_block = ""
        if column_metadata and variable_mappings:
            # 매핑된 컬럼만 추출
            mapped_columns = set()
            for mapping in variable_mappings:
                mapped_col = mapping.get('mapped_column')
                if mapped_col:
                    mapped_columns.add(mapped_col)
            
            summary_list = []
            for col_name in mapped_columns:
                if col_name not in column_metadata:
                    continue
                    
                meta = column_metadata[col_name]
                # ✨ 매핑된 컬럼에 대해서는 all_responses 전체 제공
                if 'all_responses' in meta and meta['all_responses']:
                    all_responses = meta['all_responses']
                    has_full = meta.get('has_full_list', True)
                    truncated = meta.get('truncated_note', '')
                    
                    summary_str = f"  • {col_name}: (총 {len(all_responses)}개 보기"
                    if truncated:
                        summary_str += f", ⚠️ {truncated}"
                    summary_str += ")\n"
                    
                    for item in all_responses:
                        summary_str += f"    - {item['value']}: {item['count']}명 ({item['percentage']}%)\n"
                    summary_list.append(summary_str)
            
            if summary_list:
                metadata_block = f"""

**✨ 매핑된 컬럼의 주요 보기 및 응답 분포**

🚨🚨🚨 **절대 규칙 (반드시 준수!)**

**1. 다중응답 처리 전략 (매우 중요! 절대 준수!):**
- **❌ 절대 금지**: 쉼표로 구분된 조합 전체를 규칙으로 만들지 마세요
  - 예: "국내 증시, 해외 증시" / "국내 증시, 시장지표" / "해외 증시, 시장지표" → 이렇게 하면 규칙이 폭발적으로 증가!
  - 5개 항목이면 조합이 2^5 = 32개, 10개 항목이면 2^10 = 1024개 규칙 생성!
- **✅ 올바른 방법**: 개별 항목만 규칙으로 생성
  - 예: "국내 증시", "해외 증시", "시장지표" 각각만 규칙 생성 (3개 규칙)
  - `match_mode: "contains"`를 사용하면 자동으로 조합도 매칭됨
  - "국내 증시, 해외 증시" 응답은 "국내 증시" 규칙과 "해외 증시" 규칙 모두 매칭됨
  - **핵심**: 조합은 규칙으로 만들지 않고, 개별 항목만 규칙으로 만들기!

**2. 단일 선택 vs 다중응답 구분:**
- **단일 선택**: 각 보기마다 규칙 생성 (예: "20대", "30대", "40대" 각각 규칙)
- **다중응답**: 개별 항목만 규칙 생성, 조합은 생성하지 않음
  - 예: "국내 증시, 해외 증시" → "국내 증시", "해외 증시" 각각만 규칙
  - 조합 "국내 증시, 해외 증시"는 규칙으로 만들지 않음

**3. 규칙 생성 우선순위:**
- **1순위**: 타겟 그룹에 핵심적인 개별 항목 (높은 점수 30-50점)
- **2순위**: 관련은 있지만 핵심은 아닌 개별 항목 (중간 점수 10-20점)
- **3순위**: 타겟 그룹과 무관하거나 제외할 개별 항목 (0점 또는 낮은 점수)
- **생략 가능**: 빈도가 매우 낮은 항목(1-2명)은 생략해도 됨

**4. 점수 부여 전략:**
- 높은 점수 (30-50점): 타겟 그룹에 핵심적인 항목
- 중간 점수 (10-20점): 관련은 있지만 핵심은 아닌 항목
- 낮은 점수 (0-5점): 타겟 그룹과 무관하거나 제외할 항목
- **default_points 설정**: 명시하지 않은 항목은 이 값으로 처리 (보통 0점)

**5. 규칙 수 제한:**
- 각 컬럼당 **최대 10-15개 규칙만** 생성하세요
- **개별 항목만** 규칙으로 생성 (조합은 생성하지 않음)
- 다중응답의 경우 개별 항목을 추출하여 각각 규칙 생성

{chr(10).join(summary_list)}
"""
        
        json_example = """
```json
{
  "scoring_criteria": [
    {
      "group_name": "그룹 1 명칭",
      "exclusive_traits": ["이 그룹만의 핵심 특징1", "이 그룹만의 핵심 특징2"],
      "logic": [
        {
          "column_name": "Q2_연령",
          "description": "Q2_연령: 귀하의 연령대는?",
          "type": "numerical",
          "rules": [
            {
              "range": [25, 35],
              "points": 20,
              "pandas_expression": "(df['Q2_연령'] >= 25) & (df['Q2_연령'] <= 35)"
            }
          ],
          "default_points": 0
        },
        {
          "column_name": "Q5_주사용앱",
          "description": "Q5_주사용앱: 주로 사용하는 금융 앱은?",
          "type": "categorical",
          "rules": [
            {
              "value": "삼성카드",
              "match_mode": "contains",
              "points": 30,
              "pandas_expression": "df['Q5_주사용앱'].astype(str).str.contains('삼성카드', na=False, case=False)"
            },
            {
              "value": "KB국민카드",
              "match_mode": "contains",
              "points": 30,
              "pandas_expression": "df['Q5_주사용앱'].astype(str).str.contains('KB국민카드', na=False, case=False)"
            },
            {
              "value": "현대카드",
              "match_mode": "contains",
              "points": 30,
              "pandas_expression": "df['Q5_주사용앱'].astype(str).str.contains('현대카드', na=False, case=False)"
            },
            {
              "value": "신한카드",
              "match_mode": "contains",
              "points": 0,
              "pandas_expression": "df['Q5_주사용앱'].astype(str).str.contains('신한카드', na=False, case=False)"
            }
          ],
          "default_points": 0
        },
        {
          "column_name": "Q6_신한금융서비스",
          "description": "Q6_신한금융서비스: 이용 중인 신한 금융 서비스는? (다중응답)",
          "type": "categorical",
          "rules": [
            {
              "value": "신한 SOL뱅크",
              "match_mode": "contains",
              "points": 20,
              "pandas_expression": "df['Q6_신한금융서비스'].astype(str).str.contains('신한 SOL뱅크', na=False, case=False)"
            },
            {
              "value": "신한 SOL증권",
              "match_mode": "contains",
              "points": 15,
              "pandas_expression": "df['Q6_신한금융서비스'].astype(str).str.contains('신한 SOL증권', na=False, case=False)"
            },
            {
              "value": "신한 SOL라이프",
              "match_mode": "contains",
              "points": 15,
              "pandas_expression": "df['Q6_신한금융서비스'].astype(str).str.contains('신한 SOL라이프', na=False, case=False)"
            }
          ],
          "default_points": 0
        },
        {
          "column_name": "Q8_사용이유",
          "description": "Q8_사용이유: 해당 앱을 사용하는 이유는? (주관식/다중응답)",
          "type": "categorical",
          "rules": [
            {
              "value": "디자인",
              "match_mode": "contains",
              "points": 10,
              "pandas_expression": "df['Q8_사용이유'].astype(str).str.contains('디자인', na=False, case=False)"
            },
            {
              "value": "편리",
              "match_mode": "contains",
              "points": 5,
              "pandas_expression": "df['Q8_사용이유'].astype(str).str.contains('편리', na=False, case=False)"
            }
          ],
          "default_points": 0
        }
      ]
    }
  ]
}
```
"""
        
        return f"""
🚨 **절대 규칙: JSON만 반환하세요**
- 마크다운 헤더(##), 설명, 서론, 결론 절대 금지
- 코드 블록(```) 사용 금지
- 순수 JSON 객체만 출력하세요
- 예시 형식은 참고용이며, 설명 없이 JSON만 반환하세요

당신은 참가자 선별을 위한 정량적 스코어링 모델을 설계하는 리서치 매니저입니다.

**🎯 핵심 목표:**
1. 각 그룹의 **배타적 특성** 명확히 정의
2. **검증 가능한** pandas 표현식 생성
3. **일관성 있는** 스코어링 로직
4. **효율적인 규칙 생성** - 상위 보기만 다루기 (과도한 규칙 생성 금지)

**1. 타겟 그룹 정의:**
{formatted_groups_block}

**2. 변수 맵핑 결과:**
{formatted_mappings_block}
{csv_schema_block}
{metadata_block}

**작업 순서:**

**Step 1: 그룹 간 차별화 분석 (매우 중요!)**

🚨 **필수 검증 프로세스:**
1. 모든 그룹의 `exclusive_traits`를 먼저 나열
2. 각 trait가 **정확히 1개 그룹**에만 속하는지 확인
3. 중복 발견 시 → 더 구체적인 trait로 재정의
4. 각 그룹의 핵심 차별점 2-3개 추출

**중복 예방 규칙:**
- ❌ 나쁜 예: 그룹1 ["모바일뱅킹"], 그룹2 ["모바일뱅킹"] → 중복!
- ✅ 좋은 예: 그룹1 ["일 5회 이상 모바일뱅킹"], 그룹2 ["주 1-2회 모바일뱅킹"]
- ❌ 나쁜 예: 그룹1 ["20-30대"], 그룹2 ["30-40대"] → 30대 중복!
- ✅ 좋은 예: 그룹1 ["20대"], 그룹2 ["30-40대"]

**Step 2: 배타적 특성 기반 점수 배분**
- 배타적 특성: **높은 점수(30-50점)**
- 공통 조건: **낮은 점수(10-20점)**
- 각 그룹의 총점 범위가 겹치지 않도록
- **목표: 각 그룹마다 명확히 구분되는 점수 분포 (예: 그룹1=80-100점, 그룹2=60-80점)**

**Step 3: Pandas 표현식 & 매칭 전략 (매우 중요)**

**🚨 규칙 생성 제한 (절대 준수!):**
- **각 컬럼당 최대 10-15개 규칙만 생성하세요**
- **상위 5-10개 보기만** 규칙으로 생성 (빈도가 높은 보기 우선)
- **모든 보기를 다룰 필요 없습니다** - 나머지는 default_points로 처리
- 예: 컬럼에 30개 보기가 있어도 → 상위 5-10개만 규칙 생성, 나머지는 default_points(0점)로 처리

**🎯 match_mode 판단 기준 (categorical 타입에만 적용):**

**핵심 원칙**: all_responses의 **상위 5-10개 샘플만** 보고 → 실제 전체 응답의 **다양성과 패턴을 유추** → 핵심 **키워드를 추출**해서 유연하게 매칭

**1) `match_mode: "contains"` 기본 전략 (권장):**

   ✅ **샘플에서 핵심 키워드 추출하기:**
   
   - 예: ["삼성카드 (삼성카드, monimo)", "KB국민카드 (KB Pay)"]
     → "삼성카드", "국민카드" 와 같은 키워드 추출
     → `match_mode: "contains"` + `value: "삼성카드"`
   
   - 예: ["신한은행 이용 중", "신한카드 사용함", "신한증권"]
     → **공통 키워드**: "신한" 추출
     → `match_mode: "contains"` + `value: "신한"`
   
   ✅ **쉼표로 구분된 다중응답:**
   
   - 예: ["[IMAGE] 신한 SOL증권, [IMAGE] 신한 SOL뱅크", "신한 SOL뱅크", "신한 SOL증권, 신한 SOL라이프"]
     → 개별 항목 분해: "신한 SOL뱅크", "신한 SOL증권", "신한 SOL라이프"
     → 각각 `match_mode: "contains"` 규칙 생성
     → **[IMAGE] 태그 제거 필수**
   
   ✅ **긴 문장/서술형:**
   
   - 예: ["사용하기 편리해서", "배송이 빠르고 가격이 저렴"]
     → 핵심 키워드: "사용하기 편리", "배송이 빠르고" 등
     → `match_mode: "contains"`

**2) `match_mode: "exact"` 예외 케이스 (제한적):**

   - **매우 짧고 명확한 단일 선택지**만 해당
   - 예: ["남성", "여성"], ["20대", "30대"], ["예", "아니오"]
   - 조건: 
     * 3-5글자 단답
     * 쉼표 없음
     * 샘플들이 모두 동일한 패턴
     → 이 경우만 exact 고려

**3) 기본값: 대부분의 경우 "contains" 사용 (안전하고 유연)**

**Type별 표현식 규칙:**

🚨 **description 필드 작성 규칙 (매우 중요!):**
- **형식**: "컬럼명: 질문 텍스트" 형태로 작성
- **예시**:
  - ✅ 좋음: "Q3_SOL_Pay_사용기간: SOL Pay를 얼마나 사용하셨나요?"
  - ✅ 좋음: "Q5_주사용앱: 주로 사용하는 금융 앱은 무엇인가요?"
  - ❌ 나쁨: "SOL Pay 사용 기간을 기준으로 신규 유저를 식별합니다." (컬럼명 없음)
  - ❌ 나쁨: "주사용 앱이 SOL Pay가 아닌 사용자를 식별합니다." (컬럼명 없음)
- **필수**: 반드시 컬럼명을 맨 앞에 명시해야 합니다
- **목적**: 사용자가 어떤 컬럼으로 점수를 매기는지 명확히 알 수 있어야 함

🚨 **value 선택 규칙 (categorical 타입, 매우 중요!):**

**케이스 1: 일반 단일 선택 (키워드 추출)**

샘플: ["삼성카드 (삼성카드, monimo)", "KB국민카드 (KB Pay)", "현대카드 (현대카드, 현대카드M몰)"]

✅ **올바른 처리**:
- 괄호나 설명은 무시하고 핵심 키워드만 추출
- value: "삼성카드", "KB국민카드", "현대카드"
- match_mode: "contains"

**케이스 2: 쉼표로 구분된 다중응답 (개별 항목만 규칙 생성 - 매우 중요!)**

샘플: [
  "국내 증시, 해외 증시, 시장지표",
  "국내 증시, 해외 증시",
  "시장지표, 뉴스",
  "국내 증시, 해외 증시, 시장지표, 뉴스, 증권사 리포트"
]

✅ **올바른 처리 (개별 항목만 규칙 생성)**:
1. 모든 조합에서 unique한 개별 항목만 추출
   - 추출된 개별 항목: "국내 증시", "해외 증시", "시장지표", "뉴스", "증권사 리포트"
2. 각 개별 항목별로만 contains 규칙 생성 (조합은 생성하지 않음!)
   - value: "국내 증시", match_mode: "contains", points: 20
   - value: "해외 증시", match_mode: "contains", points: 20
   - value: "시장지표", match_mode: "contains", points: 15
   - value: "뉴스", match_mode: "contains", points: 10
   - value: "증권사 리포트", match_mode: "contains", points: 10
3. **contains 모드이므로 자동으로 조합도 매칭됨**
   - "국내 증시, 해외 증시" 응답 → "국내 증시" 규칙과 "해외 증시" 규칙 모두 매칭
   - "국내 증시, 해외 증시, 시장지표" 응답 → 3개 규칙 모두 매칭

❌ **절대 금지 (조합별 규칙 생성 → 폭발!)**:
- ❌ value: "국내 증시, 해외 증시" → 규칙 생성 금지!
- ❌ value: "국내 증시, 시장지표" → 규칙 생성 금지!
- ❌ value: "해외 증시, 시장지표" → 규칙 생성 금지!
- ❌ 모든 조합을 규칙으로 만들면 → 수십~수백 개 규칙 생성!

**자동 감지 규칙:**
- all_responses의 값 중 3개 이상이 쉼표(,)로 구분된 경우 → 다중응답 문항
- 다중응답이면 → **개별 항목만 추출** + 각각 contains 규칙 생성
- **조합은 절대 규칙으로 만들지 않음** (contains 모드로 자동 매칭됨)
- 개별 항목마다 타겟 그룹 관련성에 따라 차등 점수 부여

🚨 **pandas_expression 생성 필수 규칙:**
1. **컬럼명**: CSV에 표시된 **정확한 이름** 사용 (공백, 특수문자 그대로)
2. **문자열 비교**: 반드시 `.astype(str).str.strip()` 사용 (앞뒤 공백 제거)
3. **대소문자**: 항상 `.str.lower()` 또는 `case=False` 사용
4. **NaN 처리**: `na=False` 또는 `.fillna('')` 필수
5. **⚠️ 정규식 사용 금지**: `.str.replace()`, `regex=True`, `r'\s+'` 같은 정규식 패턴 절대 사용 금지
6. **⚠️ JSON 이스케이프**: pandas_expression 내 백슬래시는 JSON에서 `\\`로 이스케이프 필요 (하지만 정규식 자체를 사용하지 마세요!)

**1. numerical**: 반드시 `&` 연산자 사용
   ```
   "(df['컬럼명'] >= 최소값) & (df['컬럼명'] <= 최대값)"
   ```

**2. categorical - exact 모드**: (단일 선택)
   - ❌ 잘못된 예: `"df['Q1_연령대'] == '20대'"`
   - ✅ 올바른 예: `"df['Q1 연령대'].astype(str).str.strip().str.lower() == '20대'.lower()"`
   - **필수 요소**: `.astype(str).str.strip().str.lower()`

**3. categorical - contains 모드**: (다중 응답, 주관식)
   - ❌ 잘못된 예: `"df['Q8_사용이유'].str.contains('디자인')"`
   - ✅ 올바른 예: `"df['Q8 사용이유'].astype(str).str.contains('디자인', na=False, case=False)"`
   - **필수 요소**: `.astype(str).str.contains(..., na=False, case=False)`

**4. opentext**: (주관식 응답 - categorical의 contains와 동일)
   ```
   "df['컬럼명'].astype(str).str.contains('키워드', na=False, case=False)"
   ```

**표현식 검증 체크리스트:**
- [ ] 컬럼명이 CSV와 100% 일치 (공백 포함)
- [ ] `.astype(str)` 포함
- [ ] `.str.strip()` 포함 (exact 모드)
- [ ] `.str.lower()` 또는 `case=False` 포함
- [ ] `na=False` 포함 (contains 모드)

**⚠️ 절대 규칙:**

1. **배타성**: 각 그룹의 `exclusive_traits`가 서로 겹치지 않아야 함
2. **표현식**: 모든 `pandas_expression`은 단일 조건만, 괄호/연산자 정확히
3. **0점 보장**: 조건 불만족 시 `default_points: 0`
4. **일관성**: 같은 컬럼/조건이면 항상 같은 점수
5. 각 규칙을 만들 때 values_sample에 있는 실제 값을 그대로 사용하고, ‘경쟁사’처럼 추상적인 표현은 금지한다. 가능하면 두 번째·세 번째 샘플도 전부 확인해 가장 대표적인 값 세트를 만든 후 활용할 것

**중요:**
- 각 variable에 'description' 필드 추가
- 소개/안내 문구 컬럼 제외
- `mapped_column`이 null이거나 confidence가 Low인 변수 제외

**🚨 응답 형식 (절대 규칙):**
- 마크다운 헤더(##), 설명, 서론, 결론 절대 금지
- 코드 블록(```) 사용 금지
- 순수 JSON 객체만 출력하세요
- 아래 예시는 참고용이며, 설명 없이 JSON만 반환하세요

{json_example}
"""

    @staticmethod
    def prompt_final_selection(target_groups, scored_data_sample, balance_variables_json, group_targets_and_candidates=None):
        """[MODIFIED] 점수 기반 최종 선별 (동적 균형변수 적용)"""
        
        target_groups_text = json.dumps(target_groups, indent=2, ensure_ascii=False)
        scored_data_text = json.dumps(scored_data_sample, indent=2, ensure_ascii=False)
        total_target = sum(g.get('targetCount', 0) for g in target_groups)
        
        # [NEW] 지시사항에 동적 균형변수 목록 추가
        formatted_groups_block = f"```json\n{target_groups_text}\n```"
        formatted_data_block = f"```json\n{scored_data_text}\n```"
        formatted_balance_vars_block = f"```json\n{balance_variables_json}\n```"
        
        return f"""
당신은 데이터 기반으로 최종 리서치 참가자를 선별하는 리서치 매니저입니다. 각 그룹별 적합도 점수가 계산된 데이터를 바탕으로 최종 참가자를 선별해주세요.

**🚨 절대 규칙(우선순위 1)**  
1. 참여자 선발 순서는 반드시 라운드 로빈으로 그룹별로 한명씩 진행합니다.  
   - 1라운드: 모든 그룹에서 최고 점수자 1명씩 선발  
   - 2라운드: 모든 그룹에서 2번째 고득점자 선발  
   - 이런 방식으로 목표 2배수까지 반복  
   - 어느 그룹이든 순번에서 선발할 사람이 없다면 즉시 다른 그룹도 중단하고 “충족 불가”를 reason에 명시하세요.  
2. 동일 참가자가 여러 그룹 후보에 있으면, 그룹별 후보수/목표수 비율이 낮은 그룹을 우선 배정하고, 비율이 같으면 더 높은 점수를 가진 그룹에 배정합니다.  
3. 절대 한 그룹에 참여자가 모두 쏠리는 일이 없도록 결과물을 도출하세요.

**🚨 필수 검사(우선순위 2)**  
- 이름, 연락처 없는 참가자는 제외  
- 최종 추천이 끝난 뒤, 각 그룹의 인원수가 목표의 2배수인지 다시 검증하고, 하나라도 어긋나면 재선발을 반복하거나 오류 반환

**1. 그룹별 목표 구성 (Target Groups):**
{formatted_groups_block}

**2. [NEW] 핵심 균형 변수 (Balance Variables):**
(이 변수들은 최종 선발 인원 내에서 최대한 고르게 분포되어야 합니다.)
{formatted_balance_vars_block}

**3. 그룹별 후보 리스트:**
{f"각 그룹별로 준비된 후보 수: {json.dumps(group_targets_and_candidates, indent=2, ensure_ascii=False) if group_targets_and_candidates else '정보 없음'}"}

각 그룹명을 키로 하여 해당 그룹의 후보 리스트가 배열로 포함됩니다.
각 참가자 데이터의 키:
  - `id`: 참가자 ID (필수 - 이것을 사용하여 추천해야 함!)
  - `score`: 해당 그룹 점수 (숫자)
  - 나머지: 설문 답변 데이터

**중요:** 
- **모든 그룹의 후보 리스트를 한번에 전체적으로 검토**하세요.
- 같은 참가자 ID가 여러 그룹의 후보에 있을 수 있으니, 각 그룹의 점수를 비교하여 가장 적합한 그룹에만 추천하세요.
- `id` 필드의 값을 그대로 사용하여 추천해야 합니다.
- **인적사항 확인 필수: 이름이나 연락처가 없는 참여자는 절대 선정하지 마세요.**

{formatted_data_block}

**[절대 준수 사항 - 매우 중요]:**

**1. 전체 맥락 파악 후 배정:**
   - 먼저 모든 그룹의 후보 리스트를 전체적으로 검토하세요.
   - 각 참가자가 여러 그룹에 있을 수 있으므로, 모든 그룹에서의 점수를 비교하여 **최적의 그룹 하나에만** 배정하세요.

**2. 중복 완전 제거 (절대 규칙):**
   - 한 참가자는 반드시 하나의 그룹에만 속해야 합니다.
   - 예: "ID_123"이 그룹A에서 100점, 그룹B에서 80점을 받았다면 → **그룹A에만 추천**, 그룹B는 제외
   - 이 로직을 모든 참가자에게 일관되게 적용하세요.

**3. 그룹별 선발 규칙 (매우 중요 - 이 규칙을 정확히 따르세요):**
   - 각 그룹당 최소 1명은 반드시 추천하세요 (빈 그룹 절대 불가).
   - **각 그룹마다 목표 인원의 2배수를 추천해야 합니다. 예: 목표 3명 → 6명 추천**
   - 각 그룹의 점수가 가장 높은 사람을 우선적으로 선택하되, 중복을 피하세요.
   - 예시: 목표가 [3명, 3명, 3명, 3명]이면, 각 그룹당 6명씩 추천 (총 24명 추천)
   
**4. 균형 변수 고려:**
   - 최종 선발 인원이 균형 변수들에 대해 최대한 균등하게 분포되도록 신경써주세요.
   - 점수가 동점일 경우, 균형 변수에 도움이 되는 참가자를 선택하세요.

**🚨 응답 형식 (절대 규칙):**

**1. JSON만 반환:**
   - 코드 블록(```)도 사용하지 마세요
   - 설명이나 주석 절대 금지
   - 순수 JSON 객체만 출력

**2. 목표 인원 필수 준수:**
   - 각 그룹마다 **정확히 목표의 2배수**를 선정해야 합니다
   - 예: 목표 3명 → **반드시 6명** 선정
   - 부족하거나 초과하면 안 됩니다

**3. 중복 절대 금지:**
   - 한 참가자는 정확히 하나의 그룹에만 속해야 합니다
   - 여러 그룹에 중복 추천 절대 불가

**4. 선정 이유 작성 규칙:**
   - **균형 변수 우선 언급**: 해당 참여자가 어떤 균형 변수 분포에 기여하는지 명시
   - **추상적 표현 금지**: "그룹 내 2번째 고득점자", "고득점자", "다음 순위자" 같은 순번 표현 절대 사용 금지
   - **구체적 특성 명시**: 참여자의 실제 답변 내용이나 특성을 언급 (50자 이내)

예시:
- ✅ 좋음: "30대 여성, 지역 균형 기여"
- ✅ 좋음: "저빈도 사용자, 사용 패턴 다양화"
- ✅ 좋음: "남성 40대, 연령/성별 균형"
- ✅ 좋음: "비수도권 거주, 지역 분포 확보"
- ❌ 나쁨: "그룹 내 최고 점수" (추상적, 균형 변수 언급 없음)
- ❌ 나쁨: "그룹 내 2번째 고득점자" (순번 표현, 의미 없음)
- ❌ 나쁨: "핵심 변수 모두 충족" (너무 추상적)

**출력 예시 (이 형식만 사용):**

{{
  "recommendations": [
    {{
      "group_name": "그룹명",
      "participants": [
        {{"id": "참가자 ID", "score": 85.5, "reason": "30대 여성이며 지역 변수 균형 기여"}}
      ]
    }}
  ]
}}
"""

    @staticmethod
    def prompt_smart_selection_with_selected(selected_participants_info, target_groups, scored_data_sample, balance_variables_json, schedule_columns=None, group_targets_and_candidates=None):
        """스마트 선정: 이미 선택된 참여자를 포함하여 부족한 인원만 자동 선정"""
        
        selected_participants_text = json.dumps(selected_participants_info, indent=2, ensure_ascii=False)
        target_groups_text = json.dumps(target_groups, indent=2, ensure_ascii=False)
        scored_data_text = json.dumps(scored_data_sample, indent=2, ensure_ascii=False)
        formatted_balance_vars_block = f"```json\n{balance_variables_json}\n```"
        
        # 일정 컬럼 정보 추가
        schedule_info = ""
        schedule_validation_block = ""
        if schedule_columns:
            schedule_info = f"""
        **일정 컬럼 정보 (Schedule Columns):**
        일정 최적화를 위해 다음 컬럼들을 참고하세요. 가능한 시간대가 적은 참여자를 우선 선정하면 일정 조율이 용이합니다.
        {json.dumps(schedule_columns, indent=2, ensure_ascii=False)}
        """
            schedule_validation_block = """
**⚠️ 일정 생성 가능성 필수 검증:**
- 선정 전에 반드시 확인: 선택된 참여자(이미 선택 + 새로 선정)들의 일정 데이터를 분석하여, 일정 최적화로 모든 참여자를 배정할 수 있는지 체크하세요.
- 각 참여자의 schedule_columns에 해당하는 필드 값을 확인하고, 날짜/시간대가 충분히 분산되어 있는지 검토하세요.
- 만약 선정 후 일정 생성이 불가능할 것으로 판단되면, 일정 가용성이 더 좋은 다른 참여자를 선정하세요.
- 목표 인원 수를 채우는 것도 중요하지만, 반드시 일정 생성이 가능한 참여자 조합을 선택해야 합니다.
- 일정 생성 불가능한 조합을 선정하지 마세요. 일정 가용성이 부족한 참여자보다는 가용성이 풍부한 참여자를 우선 선택하세요.
"""
        # 그룹별 목표 인원수 정보를 미리 생성 (f-string 밖에서)
        group_targets_info = '정보 없음'
        if group_targets_and_candidates:
            group_targets_dict = {}
            for k, v in group_targets_and_candidates.items():
                if isinstance(v, dict):
                    group_targets_dict[k] = {
                        "목표": v.get('target_count', 0),
                        "이미_선택": v.get('selected_count', 0),
                        "추가_선정_필요": v.get('remaining_target', 0),
                        "후보_수": v.get('candidate_count', 0)
                    }
            group_targets_info = json.dumps(group_targets_dict, indent=2, ensure_ascii=False)
        
        # 일정 컬럼 없는 경우 선정 기준 블록 생성
        selection_criteria_block = ""
        if schedule_columns:
            selection_criteria_block = """**3. 선정 기준 (우선순위):**
   - 점수가 높은 순서대로 선정
   - **일정 생성 가능성이 최우선**: 일정 데이터가 충분히 있고, 일정 최적화로 모든 참여자를 배정할 수 있는 참여자만 선정
   - 일정 가용성이 다양하고 풍부한 참여자 우선 (일정 조율 용이)
   - 균형 변수에 도움이 되는 참여자 우선
   - 중복 제거: 한 참가자는 하나의 그룹에만 속해야 함

**4. 일정 생성 가능성 필수 검증 (절대 규칙):**
   - 선정 전 필수 확인: 각 참여자(이미 선택 + 새로 선정 예정)의 schedule_columns 필드 값을 분석하세요.
   - 날짜와 시간대가 충분히 분산되어 있어야 합니다. 일정이 집중되어 있거나 부족하면 일정 생성이 불가능합니다.
   - 이미 선택된 참여자들의 일정 패턴을 고려하여, 추가 선정 참여자의 일정과 겹치지 않거나 보완적인 시간대를 가진 참여자를 선택하세요.
   - ⚠️ **중요: 목표 인원 수를 채우는 것이 최우선입니다.** 일정 생성이 약간 어려워 보여도, 각 그룹의 목표 인원을 정확히 채운 후 일정을 조율할 방법을 찾아야 합니다.
   - 일정 조율이 불가능한 경우에만 다른 참여자를 고려하세요.
"""
        else:
            selection_criteria_block = """**3. 선정 기준 (우선순위):**
   - 점수가 높은 순서대로 선정
   - 균형 변수에 도움이 되는 참여자 우선
   - 중복 제거: 한 참가자는 하나의 그룹에만 속해야 함

**4. 주의사항:**
   - ⚠️ **중요: 목표 인원 수를 채우는 것이 최우선입니다.** 각 그룹의 목표 인원을 정확히 채운 후 점수와 균형 변수를 고려하여 선정해야 합니다.
"""

        return f"""
당신은 이미 선택된 참여자를 포함하여 부족한 인원만 자동으로 선정하는 리서치 매니저입니다.

🚨 **절대 규칙: 각 그룹의 목표 인원수를 정확히 만족해야 합니다.**
- 목표 인원수 = 이미 선택된 참여자 수 + 새로 선정할 참여자 수
- 부족하거나 초과하면 실패입니다. 정확히 목표 인원수만큼만 선정하세요.

**1. 이미 선택된 참여자 (필수 포함):**
```json
{selected_participants_text}
```
형식: {{
  "그룹명": [
    {{"id": "참가자 ID", "name": "이름"}}
  ]
}}
각 그룹별로 이미 선택된 참여자 수가 표시됩니다.

**2. 그룹별 목표 구성 (Target Groups):**
```json
{target_groups_text}
```

**3. 핵심 균형 변수 (Balance Variables):**
{formatted_balance_vars_block}
{schedule_info}
{schedule_validation_block}

**4. 각 그룹별 목표 인원수 및 현재 상황:**
{group_targets_info}

**5. 그룹별 후보 리스트:**
{scored_data_text}

**[선정 규칙]:**

1. **이미 선택된 참여자 필수 포함:**
   - 각 그룹의 "이미_선택" 수만큼 `is_selected: true`로 포함

2. **추가 선정:**
   - 각 그룹의 "추가_선정_필요" 수치만큼 `is_selected: false`로 추가 선정
   - 이미 선택된 참여자 ID는 제외하고 후보 리스트에서 선정

3. **최종 검증:**
   - 각 그룹: participants 배열 길이 = "목표" 인원수 (정확히 일치)
   - 각 그룹: `is_selected: true` 수 = "이미_선택" 수
   - 각 그룹: `is_selected: false` 수 = "추가_선정_필요" 수
   - 중복 참여자 없음 (한 참여자는 하나의 그룹에만 속함)

{selection_criteria_block}

**[응답 형식]:**

1. **JSON만 반환** (코드 블록, 설명, 주석 금지)

2. **각 그룹의 participants 배열:**
   - `is_selected: true` = 이미 선택된 참여자 (reason: "사용자 직접 선택")
   - `is_selected: false` = 새로 선정한 참여자 (reason: 균형 변수 기반, 구체적 특성 명시)
   - 배열 길이 = "목표" 인원수 (정확히 일치)

3. **선정 이유 작성:**
   - 균형 변수 기여 내용 명시 (예: "30대 여성, 지역 균형 기여")
   - 추상적 표현 금지 (예: "고득점자", "2번째 순위" 등)

4. **중복 금지:** 한 참여자는 하나의 그룹에만 속함

**[출력 예시]:**
예시: 그룹A 목표 10명(이미 선택 2명, 추가 필요 8명), 그룹B 목표 10명(이미 선택 0명, 추가 필요 10명)

{{
  "recommendations": [
    {{
      "group_name": "그룹A",
      "participants": [
        {{"id": "ROW_1", "score": 85.5, "is_selected": true, "reason": "사용자 직접 선택"}},
        {{"id": "ROW_2", "score": 84.0, "is_selected": true, "reason": "사용자 직접 선택"}},
        {{"id": "ROW_10", "score": 82.0, "is_selected": false, "reason": "30대 여성, 지역 균형 기여"}},
        {{"id": "ROW_15", "score": 80.5, "is_selected": false, "reason": "비수도권 거주, 지역 분포 확보"}},
        {{"id": "ROW_20", "score": 79.0, "is_selected": false, "reason": "저빈도 사용자, 사용 패턴 다양화"}},
        {{"id": "ROW_25", "score": 78.5, "is_selected": false, "reason": "50대 남성, 연령대 균형"}},
        {{"id": "ROW_30", "score": 77.0, "is_selected": false, "reason": "중빈도 사용자, 사용 경험 다양화"}},
        {{"id": "ROW_35", "score": 76.5, "is_selected": false, "reason": "40대 여성, 연령/성별 균형"}},
        {{"id": "ROW_40", "score": 75.0, "is_selected": false, "reason": "30대 남성, 성별 균형 기여"}},
        {{"id": "ROW_45", "score": 74.5, "is_selected": false, "reason": "고빈도 사용자, 사용 패턴 다양화"}}
      ]
    }},
    {{
      "group_name": "그룹B",
      "participants": [
        {{"id": "ROW_5", "score": 88.0, "is_selected": false, "reason": "남성 40대, 연령/성별 균형"}},
        {{"id": "ROW_8", "score": 86.5, "is_selected": false, "reason": "고빈도 사용자, 사용 경험 다양화"}},
        {{"id": "ROW_12", "score": 85.0, "is_selected": false, "reason": "30대 여성, 성별 균형 기여"}},
        {{"id": "ROW_18", "score": 84.5, "is_selected": false, "reason": "50대 여성, 연령/성별 균형"}},
        {{"id": "ROW_22", "score": 83.0, "is_selected": false, "reason": "비수도권 거주, 지역 분포 확보"}},
        {{"id": "ROW_28", "score": 82.5, "is_selected": false, "reason": "저빈도 사용자, 사용 패턴 다양화"}},
        {{"id": "ROW_32", "score": 81.0, "is_selected": false, "reason": "40대 남성, 연령대 균형"}},
        {{"id": "ROW_38", "score": 80.5, "is_selected": false, "reason": "중빈도 사용자, 사용 경험 다양화"}},
        {{"id": "ROW_42", "score": 79.5, "is_selected": false, "reason": "30대 남성, 연령/성별 균형"}},
        {{"id": "ROW_48", "score": 78.0, "is_selected": false, "reason": "50대 남성, 연령대 균형 기여"}}
      ]
    }}
  ]
}}

⚠️ **응답 전 최종 확인:**
- 각 그룹의 participants 배열 길이 = 목표 인원수 (정확히 일치)
- 불일치 시 재계산 후 응답
"""


    @staticmethod
    def prompt_detect_name_column_only(data_schema_json):
        """
        [1단계] 참여자 식별 컬럼 찾기 (이름, 전화번호, 사번 등)
        """
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 참여자를 식별할 수 있는 컬럼을 찾는 전문가입니다.

[입력 데이터: 데이터 스키마]
{data_schema_json}

[임무]
각 컬럼의 values_sample을 분석하여 참여자를 식별하거나 연락할 수 있는 컬럼 **하나만** 찾으세요.

**찾아야 할 정보 (우선순위 순서):**
1. 이름 (한글 이름, 영문 이름)
2. 전화번호 (휴대폰 번호, 연락처)
3. 사번/직원번호 (Employee ID)
4. 이메일 (Email)

**핵심 규칙:**
1. ✅ values_sample의 **모든 값**을 하나하나 검사하세요
2. ❌ "네, 신청합니다", "예", "Yes", "아니요", "No" 같은 설문 응답이 **하나라도** 있으면 → 즉시 제외
3. ✅ 컬럼명 확인 (원본과 정규화된 컬럼명 모두 확인):
   - **원본 컬럼명(`original_column_name`)을 우선적으로 확인하세요**
   - 정규화된 컬럼명(`column_name`)도 함께 확인하세요
   - 이름: '이름', '성명', 'name', '성함'
   - 전화번호: '전화', '휴대폰', '연락처', 'phone', 'mobile'
   - 사번: '사번', '직원번호', 'ID', 'employee_id', '사원번호'
   - 이메일: '이메일', 'email', 'e-mail'
   - **⚠️ 중요**: 원본 컬럼명에 줄바꿈이나 특수문자가 있어도, 의미가 이름/연락처와 관련되면 선택하세요
4. ✅ 패턴 확인:
   - 이름: 70% 이상이 한국 이름 패턴(2-4자 한글, 성씨 포함) 또는 영문 이름
   - 전화번호: 010-XXXX-XXXX, 10-XXXX-XXXX, 010XXXXXXXX 등의 패턴
   - 사번: 숫자 또는 알파벳+숫자 조합 (예: E12345, 2024001)
   - 이메일: @ 포함 패턴
5. 📌 여러 개가 있으면 **가장 확실한 것 하나만** 선택 (이름 > 전화번호 > 사번 > 이메일 순)
6. 📌 없으면 null 반환
7. **⚠️ 반환 시 주의사항**: 
   - `original_column_name`이 있으면 **원본 컬럼명을 반환**하세요
   - `original_column_name`이 없으면 `column_name`(정규화된 컬럼명)을 반환하세요
   - 원본 컬럼명을 우선적으로 사용하면 정규화 과정에서 발생하는 문제를 방지할 수 있습니다

**검증 예시:**
✅ 올바른 예 1 - 이름:
- column_name: "Q1_이름"
- values_sample: ["홍길동", "김철수", "이영희", "최민수", "정다은"]
→ 선택 O

✅ 올바른 예 2 - 전화번호:
- column_name: "휴대폰 번호"
- values_sample: ["010-1234-5678", "010-9876-5432", "010-1111-2222"]
→ 선택 O (이름이 없을 경우)

✅ 올바른 예 3 - 사번:
- column_name: "사번"
- values_sample: ["E2024001", "E2024002", "E2024003"]
→ 선택 O (이름, 전화번호가 없을 경우)

❌ 잘못된 예 1:
- column_name: "Q1_이름" 
- values_sample: ["네, 신청합니다", "동의합니다", "김철수"]
→ 이름 응답이 1~2개 정도만 섞여있음, 선택 X

❌ 잘못된 예 2:
- column_name: "신청여부"
- values_sample: ["네, 신청합니다", "아니요"]
→ 설문 응답이므로 선택 X

**출력 형식 (JSON만):**
- name_column 값은 **정확한 컬럼명만** 반환하세요
- 줄바꿈 문자(\n, \r)나 추가 텍스트를 포함하지 마세요
- 컬럼명이 여러 줄로 나뉘어 있으면 한 줄로 합쳐서 반환하세요

출력 예시:
{{"name_column": "Q1_이름"}}

또는 찾지 못한 경우:
{{"name_column": null}}
"""


    @staticmethod
    def prompt_detect_schedule_columns_only(data_schema_json):
        """
        [2단계] 일정 컬럼만 찾는 프롬프트
        """
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 참여자 일정 컬럼을 찾는 전문가입니다.

[입력 데이터: 데이터 스키마]
{data_schema_json}

[임무]
각 컬럼을 분석하여 **참여자가 희망하는 일정(날짜/시간)**을 입력하는 컬럼만 찾으세요.

**핵심 규칙:**
1. ✅ 컬럼명에 '일정', '시간', '가능', '가능한', '참여', 'available', 'time', 'schedule', '스케줄' 등이 포함
2. ✅ values_sample에 다음 키워드 중 하나 이상 포함:
   - 날짜 패턴: "7/14", "6/2" 같은 형식
   - 요일: "(월)", "(화)", "(수)" 등
   - 시간: "오전", "오후", "시", "분"
3. ❌ 절대 제외: "이름", "성함", "연락처", "전화번호" 등 참여자 식별 정보
4. ❌ 절대 제외: 날짜/시간 패턴이 전혀 없는 컬럼

**검증 예시:**
✅ 올바른 예:
- column_name: "희망 일정"
- values_sample: ["7/14 (월) 오전 10시 30분", "7/15 (화) 오후 2시"]
→ 일정 컬럼 O

❌ 잘못된 예:
- column_name: "이름 및 연락처"
- values_sample: ["홍길동/01012345678"]
→ 날짜 패턴 없음, 일정 컬럼 X

출력 (JSON만):
{{"schedule_columns": ["컬럼명1", "컬럼명2"]}}

또는 찾지 못한 경우:
{{"schedule_columns": []}}
"""

    @staticmethod
    def prompt_schedule_optimization_with_context(context_data, total_participants):
        """
        [스크리너 일정 최적화 - 단순화 버전]
        참여자 수만큼 정확히 세션 배정
        
        Args:
            context_data: JSON 문자열 (availability_data, target_groups 등 포함)
            total_participants: 총 참여자 수 (availability_data의 길이)
        """
        
        json_output_example = """
```json
{
  "schedule_assignments": {
    "07-13": {
      "weekday": "일",
      "오전 10시 30분": ["김철수"],
      "오후 2시 30분": ["박민수"]
    },
    "07-14": {
      "weekday": "월",
      "오전 10시 30분": ["최동호"]
    }
  },
  "unassigned_participants": []
}
```
"""
        
        return f"""당신은 일정 최적화 전문가입니다.

[🚨 절대 규칙 - 위반 시 전체 응답 무효!]
**총 참여자 수: {total_participants}명**
**→ 배정할 총 슬롯 수: 정확히 {total_participants}개 (1개도 초과/미달 불가!)**

⚠️ 중요: 배열 형식 ["이름"]은 단순 표기법일 뿐, 한 슬롯에는 무조건 1명만 들어갑니다!
예시: "오전 10시": ["A", "B"] ← 이것은 2개 슬롯(잘못된 형식)
올바른 예시: "오전 10시": ["A"], "오후 2시": ["B"] ← 2개 슬롯
한명의 사용자는 무조건 한개의 세션만 가집니다.

[입력 데이터]
{context_data}

[배정 규칙]
1. **총 슬롯 수 제약 (필수!)**
   - 모든 날짜/시간 슬롯을 세면 정확히 {total_participants}개여야 함
   - 각 슬롯은 정확히 1명만 포함 (배열 길이 = 1)
   - {total_participants}개 초과 → 즉시 거부! 재작성 필요!
   - {total_participants}개 미달 → 즉시 거부! 재작성 필요!

2. **중복 배정 금지 (필수!)**
   - 각 참여자(participant_name)는 전체 schedule에서 정확히 1번만 등장
   - 같은 이름이 2번 이상 나오면 → 즉시 거부! 재작성 필요!

3. **슬롯당 1명 제약 (필수!)**
   - 각 날짜/시간 슬롯의 배열에는 무조건 1명만
   - 예: "오전 10시": ["A", "B"] → ❌ 절대 금지!
   - 예: "오전 10시": ["A"] → ✅ 올바름

4. **배정 우선순위**
   - Step 1: required_participants 우선 배정
   - Step 2: availability가 적은 참여자부터 배정 (충돌 최소화)
   - Step 3: 나머지 참여자 순차 배정

5. **날짜/시간 형식**
   - 입력: "7/14 (월) 오전 10시" → 출력 키: "07-14", 값: "오전 10시"
   - weekday는 별도 필드로 추출 (있는 경우만)

6. **응답 전 자체 검증 (필수!)**
   아래 3가지를 반드시 확인하고 통과해야만 응답:
   
   ✅ 체크리스트:
   - [ ] 총 슬롯 수 = {total_participants}개? (모든 날짜/시간 슬롯 카운트)
   - [ ] 각 참여자가 정확히 1번만 등장? (이름 중복 체크)
   - [ ] 각 슬롯의 배열 길이 = 1? (여러 명 배정 체크)
   
   ⚠️ 하나라도 실패하면 다시 배정하세요!

[출력 형식]
{json_output_example}

⚠️ 최종 주의사항:
- 배정 불가능한 경우만 unassigned_participants에 포함
- 코드 블록 없이 순수 JSON만 반환하세요
"""
    
    @staticmethod
    def prompt_analyze_data_schema(data_schema_json):
        """
        [스크리너 AI Agent 2/3]
        Python Pandas로 프로파일링된 '데이터 스키마(JSON)'를 입력받아,
        1. 각 컬럼의 실제 유형(주관식 산문, 다중선택 범주형 등)을 판단합니다.
        2. '주관식 산문' 컬럼에 대한 5단계 성실도 측정 규칙을 생성합니다.
        3. 프론트엔드에 표시할 간단한 요약 텍스트를 생성합니다.
        """
        
        json_output_example = """
```json
{
  "summary_text": "AI 분석 완료: 단일선택 12개, 다중선택 4개, 주관식 산문 3개",
  "sincerity_rules": {
    "prose_columns": ["Q10_장점", "Q11_단점", "Q12_기타의견"],
    "scoring_levels": [
      { "level": 1, "min_length": 0, "max_length": 10, "label": "매우 불성실 (10자 미만)" },
      { "level": 2, "min_length": 11, "max_length": 30, "label": "불성실 (30자 미만)" },
      { "level": 3, "min_length": 31, "max_length": 100, "label": "보통 (100자 미만)" },
      { "level": 4, "min_length": 101, "max_length": 200, "label": "성실 (200자 미만)" },
      { "level": 5, "min_length": 201, "max_length": 9999, "label": "매우 성실 (200자 초과)" }
    ],
    "filter_threshold": 3
  },
  "column_type_map": {
    "Q1_Gender": "Categorical_Single",
    "Q2_Age": "Numerical",
    "Q5_Used_Devices": "Categorical_Multi_Select_Comma",
    "Q10_장점": "Prose_OpenText"
  }
}
"""
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 설문 데이터 스키마 분석 전문가입니다. Python(Pandas)으로 1차 분석된 데이터 스키마 JSON이 주어집니다.
당신의 임무는 이 스키마(특히 'values_sample')를 분석하여, 각 컬럼의 실제 유형을 판단하고, '성실도 측정'에 필요한 규칙을 생성하는 것입니다.

[입력 데이터: 데이터 스키마]
{data_schema_json}

[분석 지침]

컬럼 유형 판단: 스키마의 'type', 'unique_count', 'values_sample'을 종합적으로 분석하여 각 컬럼의 실제 유형을 판단합니다.

핵심 구분 (중요):

values_sample이 ["a", "b", "c"] 형태이면 'Categorical_Single' 입니다.

values_sample이 ["a,b,c", "a,b", "c,d"] 처럼 쉼표(,) 등으로 구분된 조합이면 'Categorical_Multi_Select_Comma' 입니다. (이것은 성실도 측정 대상이 아닙니다!)

values_sample이 ["너무 좋아요", "사용하기 편리합니다", "개선이 필요합니다"] 처럼 완전한 문장/단어 형태의 자연어이면 'Prose_OpenText' (주관식 산문) 입니다. 이것이 성실도 측정 대상입니다.

성실도 규칙 생성: 당신이 'Prose_OpenText'로 분류한 모든 컬럼에 대해, 글자 수(length) 기반의 1~5단계 점수 시스템을 생성하십시오. 필터 기준(filter_threshold)은 3 (보통)으로 설정합니다.

요약 텍스트 생성: 프론트엔드에 보여줄 간단한 요약 텍스트를 생성합니다. (예: 단일선택 X개, 다중선택 Y개, 주관식 Z개 확인)

출력: 반드시 아래 예시와 동일한 JSON 형식 하나만 반환해야 합니다.

[출력 예시 JSON]
{json_output_example}
"""

##스크리닝 설문 진단
class SurveyDiagnosisPrompts:
    """[신규] 설문조사 진단을 담당하는 전문가 팀"""

    @staticmethod
    def _create_prompt(guideline, json_example, survey_text, principles): # 'principles' 인자 유지
        """진단 프롬프트 생성 템플릿"""
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 설문조사 품질(QA) 전문가입니다.
주어진 <survey_principles>를 '규칙서'로 삼고, <document_to_analyze>의 전체적인 품질을 <evaluation_guideline>에 명시된 단 하나의 기준에 대해서만 엄격하게 평가하십시오.
규칙서의 내용을 참고하여 애매한 경우는 항상 '미흡'으로 판단해야 합니다.

<survey_principles>
(Vector DB에서 검색된 설문조사 원칙들)
{principles}
</survey_principles>

<evaluation_guideline>
{guideline}
</evaluation_guideline>

<document_to_analyze>
{survey_text}
</document_to_analyze>

<output_instructions>

guideline을 기준으로 pass 값을 결정하고, 구체적인 이유와 근거 문장을 포함하여 아래 형식의 JSON 객체 하나만 반환하십시오.

JSON 예시: {json_example}
</output_instructions>
"""

    @staticmethod
    def prompt_diagnose_clarity(survey_text, principles):
        """[설문 진단 1/5] '명확성/간결성' 진단 (원칙 1.1)"""
        guideline = "- **GOOD:** 질문이 간결하고(원칙 1.1), 이중 질문(Double-barreled)이 아니어야 함.\n- **BAD:** 질문이 너무 길거나 복잡함(원칙 1.1), 하나의 질문에 두 가지를 물어봄(원칙 1.1)."
        # --- [수정] "boolean", "string" 대신 실제 값 예시로 변경 ---
        json_example = '{\n  "check_item_key": "clarity",\n  "pass": false,\n  "reason": "Q10 문항이 너무 길고, 제품과 서비스를 동시에 질문하고 있습니다.",\n  "quote": "Q10. 우리 회사의 신규 제품과 서비스의 품질에 만족하십니까?"\n}'
        return SurveyDiagnosisPrompts._create_prompt(guideline, json_example, survey_text, principles)

    @staticmethod
    def prompt_diagnose_terminology(survey_text, principles):
        """[설문 진단 2/5] '용어 사용' 진단 (원칙 1.2)"""
        guideline = "- **GOOD:** 응답자가 쉽게 이해할 수 있는 일상적인 표현을 사용함.\n- **BAD:** 모호하거나 어려운 전문 용어를 정의 없이 사용함. (예: '인지적 부하')"
        # --- [수정] "boolean", "string" 대신 실제 값 예시로 변경 ---
        json_example = '{\n  "check_item_key": "terminology",\n  "pass": false,\n  "reason": "Q5의 \'인지적 부하\'라는 용어는 응답자가 이해하기 어렵습니다.",\n  "quote": "Q5. 본 제품의 UX/UI에 대한 인지적 부하(Cognitive Load)는 어느 정도입니까?"\n}'
        return SurveyDiagnosisPrompts._create_prompt(guideline, json_example, survey_text, principles)

    @staticmethod
    def prompt_diagnose_leading_questions(survey_text, principles):
        """[설문 진단 3/5] '유도 질문' 진단 (원칙 1.3)"""
        guideline = "- **GOOD:** 질문이 중립적임.\n- **BAD:** 특정 응답을 암시하거나(예: '환경 보호를 위해...'), 가치판단이 포함된 질문."
        # --- [수정] "boolean", "string" 대신 실제 값 예시로 변경 ---
        json_example = '{\n  "check_item_key": "leading_questions",\n  "pass": false,\n  "reason": "Q3에서 \'환경 보호를 위해\'라는 표현이 \'예\' 응답을 유도합니다.",\n  "quote": "Q3. 환경 보호를 위해 재활용 제품을 구매하시겠습니까?"\n}'
        return SurveyDiagnosisPrompts._create_prompt(guideline, json_example, survey_text, principles)

    @staticmethod
    def prompt_diagnose_options_mec(survey_text, principles):
        """[설문 진단 4/5] '보기의 상호배타성/포괄성' 진단 (원칙 2.1)"""
        guideline = "- **GOOD:** 보기 항목이 서로 겹치지 않고(Mutually exclusive), '기타'/'해당 없음' 등을 제공하여 포괄적임(Exhaustive).\n- **BAD:** 보기의 범위가 겹침(예: 20-25세, 25-30세), '기타' 항목이 없음."
        # --- [수정] "boolean", "string" 대신 실제 값 예시로 변경 ---
        json_example = '{\n  "check_item_key": "options_mec",\n  "pass": false,\n  "reason": "Q2의 연령 보기에서 \'25세\'가 겹칩니다.",\n  "quote": "Q2. 연령대: 20~25세, 25~30세"\n}'
        return SurveyDiagnosisPrompts._create_prompt(guideline, json_example, survey_text, principles)

    @staticmethod
    def prompt_diagnose_flow(survey_text, principles):
        """[설문 진단 5/5] '논리적 순서 / 스크리너 배치' 진단 (원칙 3.1, 4.1)"""
        guideline = "- **GOOD:** 포괄적 질문에서 구체적 질문 순으로 진행함. 스크리너 설문일 경우, 자격 관련 핵심 질문이 앞부분에 배치됨.\n- **BAD:** 질문 순서가 논리적이지 않음. 스크리너 설문인데 자격 질문이 인구통계 질문보다 뒤에 있음."
        # --- [수정] "boolean", "string" 대신 실제 값 예시로 변경 ---
        json_example = '{\n  "check_item_key": "flow",\n  "pass": false,\n  "reason": "스크리너 설문임에도 불구하고 핵심 자격 질문(Q5. 서비스 사용 여부)이 인구통계 질문(Q1, Q2)보다 뒤에 배치되었습니다.",\n  "quote": "Q1. 성별... Q5. 서비스 사용 여부..."\n}'
        return SurveyDiagnosisPrompts._create_prompt(guideline, json_example, survey_text, principles)

##스크리닝 설문 진단 후 생성
class SurveyGenerationPrompts:
    """[신규] 설문조사 수정 및 최종 생성을 담당하는 전문가 팀"""

    @staticmethod
    def prompt_generate_survey_draft(survey_text, item_to_fix, principles):
        """[설문 개선] 특정 진단 항목('미흡')에 대한 문항별 개선안 생성"""
        
        item_map = {
            "clarity": "명확성/간결성 (원칙 1.1)",
            "terminology": "용어 사용 (원칙 1.2)",
            "leading_questions": "유도 질문 (원칙 1.3)",
            "options_mec": "보기의 상호배타성/포괄성 (원칙 2.1)",
            "flow": "논리적 순서 / 스크리너 배치 (원칙 3.1, 4.1)"
        }
        fix_item_name = item_map.get(item_to_fix, item_to_fix)
        json_output_example = """
```json
{
  "draft_suggestions": [
    {
      "question_id": "Q3",
      "reason": "이 질문은 '환경 보호'라는 가치 판단을 포함하여 '예'로 응답을 유도합니다. (원칙 1.3)",
      "original": {
        "text": "Q3. 환경 보호를 위해 재활용 제품을 구매하시겠습니까?",
        "options": "- 예\n- 아니오"
      },
      "suggested": {
        "text": "Q3. 재활용 제품을 구매하십니까?",
        "options": "- 예\n- 아니오"
      }
    },
    {
      "question_id": "Q5",
      "reason": "보기 '20-25세'와 '25-30세'가 25세에서 겹칩니다. (원칙 2.1)",
      "original": {
        "text": "Q5. 귀하의 연령대는?",
        "options": "- 20-25세\n- 25-30세\n- 30-35세"
      },
      "suggested": {
        "text": "Q5. 귀하의 연령대는?",
        "options": "- 20~25세\n- 26~30세\n- 31~35세"
      }
    }
  ]
}
"""

        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 설문 개선 전문가입니다. 주어진 <survey_principles>를 규칙서로 삼아, <survey_text> 원본에서 '{fix_item_name}' 원칙을 위반한 문항들을 찾아 수정하십시오.

<survey_principles>
{principles}
</survey_principles>

<survey_text>
{survey_text}
</survey_text>

**[과업 지시]**
1.  `<survey_text>`에서 '{fix_item_name}' 원칙을 위반한 **모든 문항**을 찾으십시오.
2.  각 문항별로 '원본'과 '수정 제안'을 아래 [출력 형식]과 동일한 Markdown 목록으로 생성하십시오.
3.  만약 '흐름(flow)' 문제처럼 특정 문항을 수정하는 것이 아니라 순서 변경이나 전반적인 조언이 필요하다면, Markdown으로 상세한 조언을 작성하십시오.

[출력 예시]
{json_output_example}
"""

    @staticmethod
    def prompt_polish_survey(original_survey_text, confirmed_fixes_json):
        """[설문 최종 생성] 원본 설문에 사용자가 승인한 '문항별 개선안'들을 적용하여 최종본 생성"""
        json_output_example = """
        {
  "questions": [
    {
      "id": "Q1",
      "type": "단일 선택",
      "text": "귀하의 성별은 무엇인가요?"
    },
    {
      "id": "Q2",
      "type": "단일 선택",
      "text": "귀하의 연령대는?"
    }
  ],
  "options": {
    "Q1": "- 남성\n- 여성",
    "Q2": "- 20~25세\n- 26~30세\n- 31~35세"
  }
}
"""
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 여러 개의 수정안(Fixes)을 원본 문서에 취합하는 '마스터 에디터'입니다.
당신의 임무는 `<original_survey_text>`를 기준으로, `<confirmed_fixes>`에 담긴 Markdown 수정안들을 적용하여 **하나의 완성된 최종 설문조사 텍스트**를 만드는 것입니다.

<original_survey_text>
{original_survey_text}
</original_survey_text>

<confirmed_fixes>
{confirmed_fixes_json}
</confirmed_fixes>

**[과업 지시 (매우 중요)]**
1.  `<original_survey_text>`의 문항(Q1, Q2...)을 처음부터 끝까지 하나씩 검토합니다.
2.  검토 중인 문항(예: 'Q3')이 `<confirmed_fixes>`의 '수정 제안' 목록에 **존재하는 경우**, 원본 대신 '수정 제안' 버전의 텍스트를 사용합니다.
3.  '수정 제안' 목록에 **존재하지 않는 문항**은 원본 텍스트를 그대로 사용합니다.
4.  '흐름(flow)'과 관련된 조언이 있다면, 문항 순서를 조절하거나 안내 문구를 추가하는 등 조언을 최종본에 반영하십시오.
5.  최종 결과물은 반드시 [출력 예시]에 명시된 **JSON 형식**이어야 합니다. 불필요한 설명 없이 JSON 객체 하나만 생성하십시오.


[출력 예시]
{json_output_example}
"""

##가이드라인 생성
class GuidelineGeneratorPrompts:
    """[신규] 사용자 인터뷰/테스트 가이드라인(스크립트) 생성을 담당"""
    
    @staticmethod
    def prompt_generate_guideline(research_plan, options_json, rules_context_str, examples_context_str): # [수정] 인자 추가
        """
        조사 계획서와 선택된 옵션, 그리고 RAG 검색 결과를 바탕으로
        실제 현장에서 사용할 수 있는 상세한 가이드라인(스크립트)을 생성합니다.
        """
        output_format_template = """
# [진행 가이드라인: {선택된 방법론}]

※ 본 가이드라인은 계획서에서 추출된 방법론 내용을 기반으로 하며, 그 외 절차 안내는 포함하지 않습니다.

## 1. 도입 (Introduction) - 5분
- 참여자 환영 및 긴장 완화
- 조사 목적, 비밀 보장, 녹음/녹화 안내, 사례비 안내 등
- 예시 멘트:
  - 안녕하세요, OOO님. 오늘 시간 내주셔서 감사합니다.

## 2. 웜업 질문 (Warm-up) - 10분
- 참여자의 서비스 이용 행태 등 일반적인 질문
- 질문
  - 평소 OOO 서비스를 얼마나 자주 이용하시나요?
  - 주로 어떤 기능 때문에 이 서비스를 사용하게 되셨나요?
> **모더레이터 메모**
> - 기준선 파악: 정보 우선순위/용어 친숙도/주요 확인 항목

## 3. 핵심 과업/질문 (Main Tasks/Questions) - 40분
- 조사 계획서의 '핵심 질문'과 '선택된 방법론'을 바탕으로 이 섹션을 상세히 구성
### 진행 공통 안내
- [ ] 각 과업은 가능한 한 도움 없이 평소처럼 진행해 주세요.
- [ ] 찾기 어려우면 “어렵다/못 찾겠다”라고 편하게 말씀해 주세요.
- [ ] 각 과업 종료 후 난이도를 1점(매우 어려움)~7점(매우 쉬움)으로 말씀해 주세요.

### (예: UT) 과업 단위 구성
#### 과업 1: [과업명] - [분]
- 시나리오: ...
- 성공 기준: ...
- 질문(Think-aloud/Follow-up)
  - ...
> **모더레이터 메모**
> - 관찰: ...
> - 위험 신호: ...

### (예: IDI) 주제 단위 구성
#### 주제 1: [주제명] - [분]
- 질문
  - ...
> **모더레이터 메모**
> - 딥다이브 포인트: ...

## 4. 마무리 (Wrap-up) - 5분
- 오늘 준비한 질문은 여기까지입니다. 혹시 추가로 하시고 싶은 말씀이 있으신가요?
- 다시 한번 감사 인사
        """

        return f"""
당신은 15년 경력의 시니어 UX 리서처이자, 사용자 인터뷰 및 UT(사용성 테스트)를 전문으로 수행하는 모더레이터입니다.
당신의 임무는 주어진 <조사 계획서>, <조사 옵션>, 그리고 **<참고 자료>**를 바탕으로, 신입 리서처도 바로 현장에서 사용할 수 있을 만큼 상세하고 친절한 **"실제 발화형 스크립트"**를 작성하는 것입니다.

<조사 계획서>
{research_plan}
</조사 계획서>

<조사 옵션 (JSON)>
{options_json}
</조사 옵션>

**[필수 준수 원칙 (DB 검색 결과)]**
{rules_context_str}

**[참고 사례 (DB 검색 결과)]**
{examples_context_str}

</참고 자료>

**[과업 지시]**
1.  <조사 계획서>의 '연구 목표'와 '핵심 질문'을 분석하여, <조사 옵션>의 'methodology'에 맞는 스크립트를 생성하십시오.
2.  사용자에게 **"실제로 말하는 어투"**로 스크립트를 작성해야 합니다. (예: "...해주시겠어요?", "...라고 생각하시나요?")
3.  **출력은 반드시 Markdown으로만 작성**하십시오. 아래의 [Markdown 출력 형식 규칙]을 절대 위반하지 마십시오.
4.  위 [출력 포맷 예시]를 참고하고, **<참고 자료>의 좋은 예시들을 적극 활용**하여 '도입', '웜업', '핵심 질문', '마무리'의 4단 구조를 갖춘 상세한 Markdown 문서를 생성하십시오.
4.  '핵심 질문' 섹션은 <조사 옵션>에 명시된 방법론(예: UT, IDI)에 가장 적합한 형태로 구성하십시오.
5.  불필요한 서론이나 결론 없이, 완성된 Markdown 가이드라인만 생성하십시오.
6.  출력물에는 반드시 "계획서에서 추출된 방법론 내용만을 포함하며 추가 과정 안내는 없다"는 취지의 안내 문장을 명시하고, 실제 콘텐츠에서도 해당 방법론 범위를 벗어난 절차 설명을 추가하지 마십시오.

[Markdown 출력 형식 규칙 (매우 중요)]
- 큰 섹션은 H2(##), 하위 섹션은 H3(###), 더 하위는 H4(####)를 사용하십시오.
- 섹션 제목은 반드시 다음 형식을 따르십시오: `## 2. 웜업 질문 (Warm-up) - 10분`
- 질문 문장은 **따옴표(")** 로 감싸지 말고, 반드시 불릿 리스트로 작성하십시오.
- “모더레이터 메모”는 반드시 인용 블록으로 작성하십시오:
  > **모더레이터 메모**
  > - ...
- “진행 공통 안내”는 반드시 체크리스트로 작성하십시오:
  - [ ] ...
- 빈 줄은 문단 구분에만 사용하고, **연속 2줄 이상의 빈 줄을 만들지 마십시오.**
- 코드블록, JSON, 설명문(“아래는…”, “다음과 같습니다”)을 출력에 섞지 말고 **최종 문서만** 출력하십시오.

[출력 포맷 예시]
{output_format_template}
"""

    @staticmethod
    def prompt_extract_methodologies(research_plan):
        """조사 계획서에서 '조사 방법론'만 JSON 배열로 추출합니다."""
        json_example = '{\n  "methodologies": ["UT (사용성 테스트)", "FGI (포커스 그룹 인터뷰)", "IDI (심층 인터뷰)"]\n}'
        return f"""🚨 절대 규칙: JSON만 반환하세요. 마크다운 헤더, 설명, 코드 블록 금지.

당신은 조사 계획서에서 '조사 방법론' 섹션만 정확히 찾아내는 AI 분석기입니다.
<조사 계획서>
{research_plan}
</조사 계획서>

**[과업 지시]**
1. 계획서 전체를 읽고, '조사 방법', '방법론', '데이터 수집 방법' 등 섹션에서 언급된 모든 리서치 방법론을 찾으십시오.

2. **중복 제거 및 정규화 규칙**:
   - 비슷한 방법론은 하나로 통합하세요 (예: "심층 인터뷰", "In-depth Interview", "IDI" → "심층 인터뷰")
   - 워딩이 약간 다른 동일한 방법론은 표준 명칭으로 통일하세요
   - 영어와 한국어가 섞인 경우 한국어로 통일하세요

3. **표준 방법론 명칭**:
   - 인터뷰 관련: "심층 인터뷰", "포커스 그룹 인터뷰", "구조화된 인터뷰"
   - 사용성 테스트: "사용성 테스트", "형성적 사용성 테스트", "총괄적 사용성 테스트"
   - 관찰 연구: "컨텍스추얼 인쿼리", "에스노그라피", "참여 관찰"
   - 설문 관련: "설문조사", "온라인 설문", "오프라인 설문"
   - 기타: "아이트래킹", "다이어리 스터디", "카드 소팅", "휴리스틱 평가"

4. **제외할 항목**:
   - "베타 미지원" 같은 상태 메시지
   - 너무 구체적인 세부 방법 (예: "5점 척도 설문" → "설문조사")
   - 중복되거나 유사한 방법론

5. 추출한 방법론 목록을 아래 [출력 예시]와 동일한 JSON 형식으로 반환하십시오.
6. 만약 방법론을 찾을 수 없거나 계획서가 비어있다면, "methodologies" 키에 빈 배열 `[]`을 반환하십시오.
7. JSON 객체 외에 어떠한 설명도 추가하지 마십시오.

[출력 예시]
{json_example}
"""

class KeywordExtractionPrompts:
    """키워드 추출 프롬프트 클래스"""
    
    @staticmethod
    def extract_contextual_keywords_prompt(text):
        return f"""
다음 텍스트에서 **RAG 검색에 실질적으로 도움이 될 핵심 키워드만** 추출해주세요.

텍스트: "{text}"

지켜야 할 원칙:
- 조사/연구의 **도메인(산업, 서비스, 브랜드)**, **주요 사용자/고객 세그먼트**, **연구 대상 제품·경험·기능**, **조사 목적/핵심 문제**, **필요한 방법론** 같은 구체적이고 차별화된 표현만 선택합니다.
- "연구", "사용자", "분석", "조사", "목표"처럼 거의 모든 입력에 공통적으로 등장하는 일반 단어는 제외합니다.
- 동일하거나 중복 의미의 단어는 한 번만 남기고, 가능하면 1~3단어짜리 구체적인 표현을 유지합니다.
- 출력은 키워드만 콤마로 구분한 한 줄로 작성합니다. 설명이나 번호는 금지합니다.

예시 출력 형식: 키워드1, 키워드2, 키워드3, 키워드4
"""












