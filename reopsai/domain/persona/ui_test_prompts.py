from __future__ import annotations

import json


def _as_list(value):
    return value if isinstance(value, list) else []


def _read_string(value):
    return value.strip() if isinstance(value, str) else ""


def _good_utterance_guidance():
    return "\n".join(
        [
            "[답변 품질 루브릭]",
            "- 정합성: identity, core_traits, decision_rules, grounding_notes와 모순 없이 답하세요.",
            "- 재현성: 같은 프로필과 질문이면 비슷한 판단 기준과 생활 맥락이 반복되도록 답하세요.",
            "- 경험회고성: experience_memory와 experience_library의 실제 장면을 1인칭 회고로 풀어내세요.",
            "- 내적일관성: 질문 간 답변이 서로 충돌하지 않고 같은 사람의 관점으로 이어지게 하세요.",
            "- 말투재현: communication_style의 tone과 typical_phrases를 그대로 복붙하지 말고 자연스러운 발화 톤으로 녹이며, avoid에 해당하는 말투는 피하세요.",
            "- 니즈표출: 표면적 선호를 넘어 불편, 기대, 우려, 선택 조건을 드러내세요.",
            "- 발산적사고: 무리한 창작은 피하되, 프로필 근거가 있는 기존 서비스/습관과의 비교 관점, 질문에 직접 나오지 않은 사용 상황 확장, 조건부 대안이나 개선 제안 중 하나를 말하세요.",
            "- UX/UI 맥락인식: 화면, 기능, 정보 구조, 온보딩, 탐색, 오류, 알림, 결제 등 서비스 접점을 구체적으로 언급하세요.",
            "- 후속 질문 가능성: 답변에는 리서처가 더 파고들 수 있는 니즈, 갈등, 판단 기준, 개선 아이디어 중 하나가 남아야 합니다.",
            "- 위 루브릭 용어 자체는 답변에 직접 쓰지 말고 자연스러운 참여자 발화로만 반영하세요.",
        ]
    )


def _persona_response_guidance(is_flow_test=False):
    return "\n".join(
        [
            "[응답 방향성]",
            "- 퍼소나 원문에 없는 과거 경험, 가족관계, 직업, 선호를 새로 만들지 마세요.",
            "- 퍼소나의 성격/행동/선호/동기/사회적 맥락을 답변 근거로 쓰되, 확인 가능한 정보와 합리적 추론을 섞어 단정하지 마세요.",
            "- 화면 안에 표시된 이름, 계정명, 전화번호, 주소, 프로필 정보가 퍼소나와 다르더라도 이는 샘플/더미 데이터로 간주하세요. UI와 Flow는 이 퍼소나 본인이 사용하는 상황이라고 가정하고 응답하세요.",
            "- 플로우 반응은 1인칭 task 수행 회고처럼 쓰고, 각 단계에서 목표를 계속 수행할 수 있는지의 판단 맥락, 감정 단서, 니즈를 우선 반영하세요."
            if is_flow_test
            else "- 화면 반응은 1인칭 경험 회고처럼 쓰고, 이 사람이 실제로 겪을 법한 생활 장면, 판단 맥락, 감정 단서, 니즈를 우선 반영하세요.",
            "- 단순 긍정/부정으로 끝내지 말고, 이 사람에게 자연스러운 망설임, 상충 욕구, 조건부 판단, 예외적 사용 상황을 포함하세요.",
            "- 질문에 대한 본인 의견을 먼저 밝히고, 과거 경험 또는 가치관 기반 근거를 이어 붙인 뒤, 현재 판단/니즈/조건부 기대 중 하나로 마무리하세요.",
            "- 사용자에게 보이는 모든 문장은 해요체 또는 합니다/했습니다체로 자연스럽게 작성하세요.",
            "- '~하다', '~함', '~됨' 같은 보고서식/명사형 종결은 쓰지 마세요. 한 답변 안에서는 같은 말투를 유지하세요.",
        ]
    )


def _persona_differentiation_guidance(is_flow_test=False):
    return "\n".join(
        [
            "[퍼소나별 차별화 규칙]",
            "- 코멘트를 쓰기 전에 이 퍼소나가 화면을 판단할 때 가장 강하게 작동할 렌즈를 내부적으로 2~3개 고르세요. 예: 시간 절약, 가격 민감도, 개인정보 우려, 전문성 부족, 익숙한 서비스와의 비교, 가족/업무 맥락, 모바일 숙련도.",
            "- 모든 screenFeedback과 pinComments는 generic UX 평가가 아니라, 그 렌즈 중 최소 하나와 연결된 판단이어야 합니다.",
            "- 같은 화면이라도 퍼소나의 니즈/불안/선택 기준이 다르면 서로 다른 UI 요소나 서로 다른 이유를 지적하세요.",
            "- 차이를 만들기 위해 억지로 다른 말을 지어내지 마세요. 프로필 근거가 약하면 일반 사용성 판단으로 두고, 프로필 근거가 강한 경우에만 강하게 반응하세요.",
            "- 반대로 프로필 근거가 분명한데도 모든 퍼소나가 같은 강도로 반응하게 만들지 마세요. 이 사람에게 특히 중요한 기준이면 망설임, 확신, 중단 조건을 더 구체적으로 쓰세요.",
            "- '버튼이 헷갈린다', '정보가 부족하다', '디자인이 좋다'처럼 누구나 할 수 있는 말로 끝내지 말고, 왜 이 퍼소나에게 특히 그렇게 느껴지는지 붙이세요.",
            "- Flow 코멘트에서는 이 퍼소나가 task를 계속 진행할 동기, 확신, 중단 조건이 무엇인지 단계별로 드러내세요."
            if is_flow_test
            else "- 화면 코멘트에서는 이 퍼소나가 실제 사용 상황에서 무엇을 먼저 확인하고 무엇 때문에 신뢰하거나 망설이는지 드러내세요.",
        ]
    )


def _single_screen_summary_guidance(persona_name):
    return "\n".join(
        [
            "[단일 화면 상단 퍼소나 피드백 작성 방식]",
            "- 이 영역은 개별 발화가 아니라, 이미 생성된 하단 코멘트와 위치 기반 코멘트를 취합한 요약 리포트입니다.",
            f"- 3인칭 요약 보고체로 작성하세요. 첫 문장은 '{persona_name}님은 ...' 또는 '{persona_name}님은 화면N에서 ...'처럼 시작하면 됩니다.",
            "- '저는', '제가', '좋겠어요', '아쉬워요' 같은 1인칭 발화체를 쓰지 마세요.",
            "- 문장마다 '평가했습니다'를 반복하지 말고, '또한', '반면', '전체적으로' 등으로 긍정·부정·종합 인상을 하나의 단락처럼 이어 쓰세요.",
            "- 긍정/부정 의견을 모두 확인해, 부정 평가 근거와 긍정 평가 근거를 균형 있게 정리하세요.",
            "- 상단 요약은 3~4문장으로 작성하세요.",
            "- 하단 코멘트에 없는 새로운 경험, 선호, 가족관계, 직업, 사용 맥락을 만들지 마세요.",
            "- 문장은 해요체가 아니라 요약 보고에 자연스러운 했습니다체로 작성하세요.",
        ]
    )


def _evaluation_criteria(is_flow_test):
    if is_flow_test:
        return "\n".join(
            [
                "[사용자 플로우 평가 기준]",
                "- 이 평가는 단일 화면 감상이 아니라, 사용자가 주어진 task를 화면 순서대로 완료할 수 있는지 판단하는 평가입니다.",
                "- 각 단계에서는 이전 화면에서 기대한 행동과 현재 화면의 결과가 자연스럽게 이어지는지 우선 판단하세요.",
                "- 코멘트는 '화면을 봤을 때'가 아니라 '이 단계에서 목표를 계속 수행하려고 할 때'의 관점으로 작성하세요.",
                "- 명확성은 버튼/콘텐츠 자체의 이해보다, task 수행에 필요한 다음 행동이 분명한지를 기준으로 평가하세요.",
                "- 혼란도는 현재 단계에서 사용자가 무엇을 해야 할지, 이전 행동의 결과가 맞는지 확신하기 어려운 정도입니다.",
                "- 이탈위험은 다음 단계로 계속 진행할 동기나 확신이 약해지는 정도입니다.",
                "- Flow 평가는 마찰을 우선 탐지하되, 명확한 진행 안내, 이전 행동 결과 피드백, 입력 부담 감소처럼 완료 가능성을 높이는 신호가 분명하면 회복/지원 근거로 반영하세요.",
                "- UI/디자인 언급은 task 수행에 영향을 주는 경우에만 보조 근거로 쓰세요.",
            ]
        )
    return "\n".join(
        [
            "[단일 화면 평가 기준]",
            "- 명확성은 직관성(입력 정보, 버튼 기능, 콘텐츠 내용을 이해하기 쉬운가)과 인지 용이성(현재 상태가 즉각적으로 명확한가)을 기준으로 평가하세요.",
            "- 사용성은 유용성(실제 니즈와 task 완료에 필요한 정보/기능을 제공하는가), 유연성(초보자/숙련자와 다양한 환경에 대응하는가), 행동 유도성(다음 액션을 취하고 싶게 만드는가)을 기준으로 평가하세요.",
            "- 디자인 만족도는 UI, 컬러, 콘텐츠 이미지가 이 퍼소나에게 얼마나 매력적으로 느껴지는지를 기준으로 평가하세요.",
            "- 서비스 신뢰도는 개인정보 침해, 사기, 데이터 유출 위험 없이 서비스 제공사를 신뢰할 수 있게 보이는지를 기준으로 평가하되, 별도 점수 필드가 없으므로 feedback과 pinComments에 반영하세요.",
            "- 단일 화면은 현재 화면 안에서 정보 구조, 기능 이해, 다음 액션 유도, 신뢰 단서를 중심으로 평가하세요.",
        ]
    )


def _flow_task_context(*, test_name, test_description=None):
    return "\n".join(
        part
        for part in [
            "[수행해야 할 Task]",
            "사용자는 아래 목표를 화면 순서대로 완료하려고 합니다.",
            f"테스트명: {test_name}",
            f"테스트 설명: {test_description}" if test_description else None,
            "모든 flow 코멘트는 이 task를 계속 수행할 수 있는지 기준으로 작성하세요.",
        ]
        if part
    )


def _flow_comment_guidance():
    return "\n".join(
        [
            "[Flow 코멘트 작성 방식]",
            "- screenFeedbacks와 pinComments는 화면 사용성 평가가 아니라 Flow Task 수행 판단 근거입니다.",
            "- 기본적으로 과업 완료를 방해하는 마찰 포인트를 우선 작성하세요.",
            "- 다만 다음 행동을 명확히 안내하거나, 이전 행동 결과를 확신시켜주거나, 퍼소나의 계속 진행 동기를 높이는 요소가 명확하면 제한적 긍정 근거로 작성할 수 있습니다.",
            "- 같은 화면이라도 퍼소나의 성향, 관심사, 기존 서비스 경험, 불안/선택 기준에 따라 멈칫하는 이유와 계속 진행할 이유가 달라져야 합니다.",
            "- 이탈 위험 근거는 단순 불편함이 아니라 이 퍼소나가 왜 '그만둘 수도 있다'고 느끼는지까지 연결하세요.",
            "- 각 단계마다 가능하면 '이 퍼소나라서 중요하게 보는 기준'을 하나 드러내세요. 예: 가격 비교 습관, 상담 선호, 셀프 해결 선호, 개인정보 경계, 혜택 탐색, 빠른 완료 선호.",
            "- persona pack에 근거가 없는 렌즈를 새로 만들지 말고, 근거가 있는 렌즈가 화면 판단에 실제로 영향을 줄 때만 강한 표현을 쓰세요.",
            "- 각 screenFeedback의 첫 문장은 반드시 화면 목록의 단계명을 넣어 '<단계명> 단계에서는' 또는 '<단계명> 단계에서'로 시작하세요.",
            "- 각 screenFeedback은 '이 단계에서 내가 하려는 일 / 이전 단계와 이어지지 않는 지점 / 다음 행동 판단'을 2문장 이내, 220자 이내로 작성하세요.",
            "- '스크롤했을 때', '화면을 봤을 때', '내용이 많다' 같은 단순 화면 관찰 문장으로 끝내지 마세요.",
            "- 화면 요소를 언급할 때는 반드시 task 수행, 다음 행동 판단, 이전/다음 단계 연결성 중 하나와 연결하세요.",
            "- pinComments는 Flow 판단 근거 영역에 노출됩니다. 각 content는 1~2문장으로, 해당 UI 요소가 목표 수행을 멈칫하게 하는지 또는 계속 진행하도록 돕는지를 구체적으로 써야 합니다.",
            "- pinComments에는 이전 행동 이후 왜 불필요하거나 헷갈리는지, 다음 단계 확신에 어떤 영향을 주는지, 또는 완료 가능성을 어떻게 높이는지 중 최소 하나를 포함하세요.",
            "- '현재 화면의 정보가 부족하다', '버튼이 헷갈린다'처럼 짧은 단정으로 끝내지 말고, 퍼소나의 판단 근거나 사용 맥락을 덧붙이세요.",
            "- 첫 단계는 task 진입 관점에서, 이후 단계는 이전 단계에서 기대한 결과와 현재 단계의 연결 관점에서 작성하세요.",
            "- flowAnalysis의 transitionFromPrevious, expectedNextAction, suggestions는 각각 1문장으로 짧게 작성하되, 가능한 한 단계명을 포함하세요.",
        ]
    )


def build_ui_test_prompt(*, test_name, test_description, scope_type, flow_goal, persona_name, persona_context, screens):
    is_flow = scope_type == "flow" and len(screens) > 1
    parts = [
        "You are evaluating a UI from the perspective of the given persona, not as a generic UX reviewer.",
        "Return only valid JSON with keys: summary, personaGoalFit, scores, feedback, pinComments, flowAnalysis, strengths, risks, recommendations, screenInsights.",
        "반드시 한국어 JSON만 반환하세요. markdown, 설명문, 코드블록은 절대 쓰지 마세요.",
        "Every comment must be grounded in this persona's profile, needs, worries, habits, decision rules, or past context. Do not write generic comments that any user could say.",
        "Use natural Korean. Write screenFeedbacks as persona reactions, and write summary/personaGoalFit as a researcher-style synthesis.",
        "Do not invent hard facts outside the persona. Reason from the persona context when the profile is incomplete.",
        is_flow and "단계별 task 수행 반응(screenFeedbacks)과 task 판단 근거가 되는 위치 기반 코멘트(pinComments)를 만드세요.",
        not is_flow and "화면별로 1인칭 반응(screenFeedbacks)과 구체 위치 기반 코멘트(pinComments)를 만드세요.",
        is_flow and "screenFeedbacks와 flowAnalysis는 간결하게 작성하되, pinComments.content는 판단 근거가 드러나도록 1~2문장으로 작성하세요.",
        not is_flow and "screenFeedbacks는 화면 전체 인상과 판단을 간결하게 작성하고, pinComments.content는 해당 UI 요소에 대한 화면 평가 근거가 드러나도록 1~2문장으로 작성하세요.",
        is_flow and "각 pinComment content에는 가능하면 (1) 지목한 UI 요소, (2) 퍼소나가 멈칫하거나 계속 진행할 수 있는 이유, (3) 이 퍼소나의 관심사/불안/기존 경험에서 나온 판단 중 2가지 이상을 담으세요.",
        not is_flow and "각 pinComment content에는 가능하면 (1) 지목한 UI 요소, (2) 퍼소나가 이해/신뢰/매력/사용성 측면에서 판단한 이유, (3) 화면 전체 인상이나 행동 유도성에 미치는 영향 중 2가지 이상을 담으세요.",
        _persona_response_guidance(is_flow),
        _persona_differentiation_guidance(is_flow),
        _good_utterance_guidance(),
        _evaluation_criteria(is_flow),
        not is_flow and _single_screen_summary_guidance(persona_name),
        "scores must include clarity, usability, appeal, overall as 0-100 integers.",
        "scores must also include screenScores: one item for every screenIndex with screenId, clarity, usability, appeal, satisfaction, overall as 0-100 integers.",
        "feedback must include overallFeedback and screenFeedbacks. screenFeedbacks must include at least one item for every screenIndex.",
        "pinComments must be an array of concrete image markers with screenIndex, x, y, type, content. type must be one of praise, problem, improvement.",
        "pinComments의 x,y는 이미지 안 실제 UI 요소 위치를 0~100 퍼센트로 정확히 찍어야 합니다.",
        "Use praise for positive comments and problem/improvement for negative comments. screenInsights positives/issues must align with the same evidence used in pinComments.",
        "For each screen, provide at least one positive evidence point and one risk/improvement point when possible.",
        "Attached images follow the same order as [Screens]. x and y must point to the actual UI element in the attached image as 0-100 percentage coordinates, where x is left-to-right and y is top-to-bottom.",
        "Do not use generic center coordinates unless the target element is genuinely centered. If exact location is uncertain, choose the most plausible visible element area and name that element in content.",
        is_flow and "This is a flow test. Evaluate whether the persona can keep moving toward the task goal across screens; include flowAnalysis for every screenIndex with confusionScore, dropoffRisk, suggestions, transitionFromPrevious, expectedNextAction, bottleneckRisk.",
        is_flow and "For flow tests, pinComments should focus on friction/improvement evidence for visible UI elements; use praise only when it is genuinely important evidence.",
        is_flow and "type은 praise, problem, improvement 중 하나입니다. Flow에서 praise는 일반 칭찬이 아니라 다음 행동 안내, 결과 피드백, 입력 부담 감소처럼 완료 가능성을 높이는 근거가 명확할 때만 사용하세요.",
        not is_flow and "type은 praise, problem, improvement 중 하나입니다.",
        is_flow and "flowAnalysis에는 각 단계의 confusionScore, dropoffRisk, suggestions, transitionFromPrevious, expectedNextAction, bottleneckRisk, uiClarity, visualHierarchy를 포함하세요.",
        not is_flow and "flowAnalysis는 빈 배열로 반환하세요.",
        is_flow and "flowAnalysis의 confusionScore/dropoffRisk/uiClarity/visualHierarchy는 0~100 정수로 쓰세요. confusionScore/dropoffRisk는 높을수록 부정, 나머지는 높을수록 긍정입니다.",
        is_flow and _flow_comment_guidance(),
        not is_flow and "This is a single-screen test. Return flowAnalysis as an empty array.",
        is_flow and _flow_task_context(test_name=test_name, test_description=test_description),
        f"Test: {test_name}",
        f"Description: {test_description or ''}",
        f"Scope: {scope_type}",
        f"Task/Flow Goal: {flow_goal or ''}",
        "[Persona]",
        persona_context,
        "[Screens]",
        json.dumps(screens, ensure_ascii=False),
    ]
    return "\n".join(part for part in parts if part)


def build_ui_chunk_prompt(
    *,
    test_name,
    test_description,
    scope_type,
    source_type,
    device_type,
    persona_context,
    screens,
    screen_indices,
    repair_mode=False,
):
    is_flow = scope_type == "flow" and len(screens) > 1
    step_context = "\n".join(
        f"- screenIndex {screen_index}: {screens[screen_index].get('name') or f'화면 {screen_index + 1}'} / {screens[screen_index].get('sourceLabel') or screens[screen_index].get('source_type') or ''}"
        for screen_index in screen_indices
        if isinstance(screen_index, int) and 0 <= screen_index < len(screens)
    )
    return "\n".join(
        part
        for part in [
            "당신은 처음 서비스를 접하는 사용자이자 UX 리서처입니다.",
            "프롬프트 버전: persona_test_v2",
            "반드시 한국어 JSON만 반환하세요.",
            "단계별 task 수행 반응(screenFeedbacks)과 task 판단 근거가 되는 위치 기반 코멘트(pinComments)를 만드세요."
            if is_flow
            else "화면별로 1인칭 반응(screenFeedbacks)과 구체 위치 기반 코멘트(pinComments)를 만드세요.",
            "screenFeedbacks와 flowAnalysis는 간결하게 작성하되, pinComments.content는 판단 근거가 드러나도록 1~2문장으로 작성하세요."
            if is_flow
            else "screenFeedbacks는 화면 전체 인상과 판단을 간결하게 작성하고, pinComments.content는 해당 UI 요소에 대한 화면 평가 근거가 드러나도록 1~2문장으로 작성하세요.",
            "각 pinComment content에는 가능하면 (1) 지목한 UI 요소, (2) 퍼소나가 멈칫하거나 계속 진행할 수 있는 이유, (3) 이 퍼소나의 관심사/불안/기존 경험에서 나온 판단 중 2가지 이상을 담으세요."
            if is_flow
            else "각 pinComment content에는 가능하면 (1) 지목한 UI 요소, (2) 퍼소나가 이해/신뢰/매력/사용성 측면에서 판단한 이유, (3) 화면 전체 인상이나 행동 유도성에 미치는 영향 중 2가지 이상을 담으세요.",
            _persona_response_guidance(is_flow),
            _persona_differentiation_guidance(is_flow),
            _good_utterance_guidance(),
            _evaluation_criteria(is_flow),
            "pinComments의 x,y는 이미지 안 실제 UI 요소 위치를 0~100 퍼센트로 정확히 찍어야 합니다.",
            "type은 praise, problem, improvement 중 하나입니다. Flow에서 praise는 일반 칭찬이 아니라 다음 행동 안내, 결과 피드백, 입력 부담 감소처럼 완료 가능성을 높이는 근거가 명확할 때만 사용하세요."
            if is_flow
            else "type은 praise, problem, improvement 중 하나입니다.",
            "이 호출은 누락된 화면 커버리지를 보완하기 위한 것입니다. 지정된 screenIndex를 반드시 모두 커버하세요."
            if repair_mode
            else "지정된 screenIndex를 모두 커버하세요.",
            "flowAnalysis에는 각 단계의 confusionScore, dropoffRisk, suggestions, transitionFromPrevious, expectedNextAction, bottleneckRisk, uiClarity, visualHierarchy를 포함하세요."
            if is_flow
            else "flowAnalysis는 빈 배열로 반환하세요.",
            "flowAnalysis의 confusionScore/dropoffRisk/uiClarity/visualHierarchy는 0~100 정수로 쓰세요. confusionScore/dropoffRisk는 높을수록 부정, 나머지는 높을수록 긍정입니다."
            if is_flow
            else None,
            _flow_comment_guidance() if is_flow else None,
            "",
            f"테스트명: {test_name}",
            f"테스트 설명: {test_description}" if test_description else None,
            f"테스트 범위: {'사용자 플로우' if is_flow else '단일 화면'}",
            f"입력 방식: {source_type}",
            f"기기 유형: {device_type}",
            _flow_task_context(test_name=test_name, test_description=test_description) if is_flow else None,
            "",
            "[퍼소나]",
            persona_context,
            "",
            "[대상 화면]",
            step_context,
            "",
            "[반환 JSON]",
            '{"screenFeedbacks":[{"screenIndex":0,"feedback":"string"}],"pinComments":[{"screenIndex":0,"x":50,"y":50,"type":"problem","content":"string"}],"flowAnalysis":[{"screenIndex":0,"confusionScore":50,"dropoffRisk":50,"suggestions":["string"],"transitionFromPrevious":null,"expectedNextAction":"string","bottleneckRisk":"medium","uiClarity":50,"visualHierarchy":50}]}',
        ]
        if part
    )


def build_generated_feedback_evidence(*, screens, screen_feedbacks, pin_comments, flow_analysis, is_flow):
    screen_feedback_lines = []
    for entry in _as_list(screen_feedbacks):
        if not isinstance(entry, dict):
            continue
        screen_index = entry.get("screenIndex", 0)
        screen_name = screens[screen_index].get("name") if isinstance(screen_index, int) and screen_index < len(screens) else f"화면 {screen_index + 1}"
        feedback = _read_string(entry.get("feedback"))
        if feedback:
            screen_feedback_lines.append(f"- screenIndex {screen_index} ({screen_name}): {feedback}")

    pin_comment_lines = []
    for comment in _as_list(pin_comments):
        if not isinstance(comment, dict):
            continue
        screen_index = comment.get("screenIndex", 0)
        screen_name = screens[screen_index].get("name") if isinstance(screen_index, int) and screen_index < len(screens) else f"화면 {screen_index + 1}"
        content = _read_string(comment.get("content"))
        if content:
            pin_comment_lines.append(f"- screenIndex {screen_index} ({screen_name}) / {comment.get('type')} / x={comment.get('x')}, y={comment.get('y')}: {content}")

    flow_analysis_lines = []
    for item in _as_list(flow_analysis):
        if not isinstance(item, dict):
            continue
        screen_index = item.get("screenIndex", 0)
        screen_name = screens[screen_index].get("name") if isinstance(screen_index, int) and screen_index < len(screens) else f"화면 {screen_index + 1}"
        suggestions = " / ".join(str(point) for point in _as_list(item.get("suggestions")) if str(point).strip()) or "없음"
        flow_analysis_lines.append(
            " / ".join(
                part
                for part in [
                    f"- screenIndex {screen_index} ({screen_name})",
                    f"confusionScore={item.get('confusionScore')}",
                    f"dropoffRisk={item.get('dropoffRisk')}",
                    f"suggestions={suggestions}",
                    f"transitionFromPrevious={item.get('transitionFromPrevious')}" if item.get("transitionFromPrevious") else None,
                    f"expectedNextAction={item.get('expectedNextAction')}" if item.get("expectedNextAction") else None,
                ]
                if part
            )
        )

    if not screen_feedback_lines and not pin_comment_lines and not flow_analysis_lines:
        return None

    flow_first = [
        "[먼저 생성된 단계별 task 수행 분석]",
        "아래 내용은 이미 생성된 단계별 피드백, 위치 기반 근거, 플로우 분석입니다. 종합 반응과 점수는 flowAnalysis를 최우선 근거로 삼고, screenFeedbacks와 pinComments는 보조 근거로만 사용하세요.",
        "[flowAnalysis - 최우선 근거]" if flow_analysis_lines else None,
        *flow_analysis_lines,
        "[screenFeedbacks - 단계별 task 수행 반응]" if screen_feedback_lines else None,
        *screen_feedback_lines,
        "[pinComments - 화면 근거]" if pin_comment_lines else None,
        *pin_comment_lines,
    ]
    screen_first = [
        "[먼저 생성된 화면별 반응과 코멘트]",
        "아래 내용은 이미 생성된 화면별 피드백, 위치 기반 코멘트, 플로우 분석입니다. 종합 반응과 점수는 이 내용을 주요 근거로 삼아야 합니다.",
        "[screenFeedbacks]" if screen_feedback_lines else None,
        *screen_feedback_lines,
        "[pinComments]" if pin_comment_lines else None,
        *pin_comment_lines,
        "[flowAnalysis]" if flow_analysis_lines else None,
        *flow_analysis_lines,
    ]
    return "\n".join(part for part in (flow_first if is_flow else screen_first) if part)


def build_ui_summary_prompt(
    *,
    test_name,
    test_description,
    scope_type,
    source_type,
    device_type,
    persona_name,
    persona_context,
    screens,
    screen_feedbacks,
    pin_comments,
    flow_analysis,
):
    is_flow = scope_type == "flow" and len(screens) > 1
    generated_evidence_context = build_generated_feedback_evidence(
        screens=screens,
        screen_feedbacks=screen_feedbacks,
        pin_comments=pin_comments,
        flow_analysis=flow_analysis,
        is_flow=is_flow,
    )
    screen_manifest = "\n".join(
        f"{index + 1}. {screen.get('name') or f'화면 {index + 1}'} ({screen.get('id') or f'screen-{index + 1}'}) - {screen.get('sourceType') or screen.get('source_type') or ''}"
        for index, screen in enumerate(screens)
    )
    return "\n".join(
        part
        for part in [
            "당신은 처음 서비스를 접하는 사용자이자 UX 리서처입니다.",
            "프롬프트 버전: persona_test_v2",
            "반드시 한국어 JSON만 반환하세요. markdown, 설명문, 코드블록은 절대 쓰지 마세요.",
            "퍼소나 관점에서 전체 task 수행 흐름을 보고 1인칭으로 반응을 정리하세요."
            if is_flow
            else "퍼소나 관점에서 화면 전체 반응을 3인칭 요약 리포트로 정리하세요.",
            "화면 감상보다, 목표를 완료하기 위해 각 단계가 자연스럽게 이어지는지와 어디에서 멈칫하는지를 경험 중심으로 평가하세요."
            if is_flow
            else "개선안을 새로 쓰기보다, 먼저 생성된 하단 코멘트의 긍정/부정 근거를 취합해 요약하세요.",
            "종합 반응은 먼저 생성된 flowAnalysis를 최우선 근거로 삼되, 단계별 내용을 순서대로 다시 쓰지 말고 전체 task를 수행한 뒤 남는 하나의 판단으로 작성하세요."
            if generated_evidence_context and is_flow
            else None,
            "종합 반응은 먼저 생성된 화면별 반응과 코멘트를 바탕으로, 화면별 코멘트와 같은 방향으로 작성하세요."
            if generated_evidence_context and not is_flow
            else None,
            _persona_response_guidance(True) if is_flow else _single_screen_summary_guidance(persona_name),
            _good_utterance_guidance(),
            None if is_flow else _evaluation_criteria(False),
            "overallFeedback와 flowSummary는 각각 3~5문장으로 작성하세요. 단, 단계별 코멘트를 합치거나 첫 번째/두 번째/마지막 단계 순서로 나열하지 말고 전체 플로우에 대한 하나의 총평으로 써야 합니다."
            if is_flow
            else "overallFeedback와 screenSummaries의 summary는 각각 3~4문장으로 작성하세요.",
            "화면 수만큼 screenSummaries 항목을 하나씩 생성하고, summary는 해당 화면 pinComments를 우선 근거로 3인칭 요약하세요."
            if not is_flow
            else None,
            "screenSummaries의 summary는 화면마다 하나의 흐름 있는 단락으로 쓰세요. 문장마다 '평가했습니다'를 반복하지 말고, 첫 문장만 퍼소나명으로 시작한 뒤 '또한/반면/전체적으로'로 이어가세요."
            if not is_flow
            else None,
            "overallFeedback는 반드시 '저는' 또는 '제가'가 자연스럽게 포함된 1인칭 task 수행 총평으로 작성하세요."
            if is_flow
            else f"overallFeedback는 반드시 '{persona_name}님은'으로 시작하는 3인칭 요약 리포트 문장으로 작성하세요.",
            "overallFeedback와 flowSummary에는 screenIndex, 화면 id, 괄호형 내부 참조를 쓰지 마세요. '첫 번째 단계', '두 번째 단계', '마지막 단계'처럼 단계를 순서대로 열거하지 마세요."
            if is_flow
            else "overallFeedback에는 하단 코멘트의 핵심 부정 의견과 긍정 의견을 취합해 쓰고, 개별 발화처럼 따옴표로 말하지 마세요.",
            "반환 JSON 구조:",
            '{"overallFeedback":"string","flowSummary":"string"}'
            if is_flow
            else '{"overallFeedback":"string","screenSummaries":[{"screenIndex":0,"summary":"string"}]}',
            "",
            f"테스트명: {test_name}",
            f"테스트 설명: {test_description}" if test_description else None,
            f"테스트 범위: {'사용자 플로우' if is_flow else '단일 화면'}",
            f"입력 방식: {source_type}",
            f"기기 유형: {device_type}",
            _flow_task_context(test_name=test_name, test_description=test_description) if is_flow else None,
            "",
            "[퍼소나]",
            persona_context,
            "",
            "[화면 목록]",
            screen_manifest,
            "",
            generated_evidence_context,
            "",
            "플로우 전체 반응과 flowSummary는 가장 큰 진행 동기, 가장 큰 망설임, 실행 전 확인하고 싶은 조건을 중심으로 하나의 총평으로 작성하세요."
            if is_flow
            else "단일 화면 기준으로 평가하세요.",
        ]
        if part
    )


def build_ui_scoring_evidence_context(*, screens, screen_feedbacks, pin_comments, flow_analysis):
    screen_feedback_lines = []
    for entry in _as_list(screen_feedbacks):
        if not isinstance(entry, dict):
            continue
        screen_index = entry.get("screenIndex", 0)
        screen_name = screens[screen_index].get("name") if isinstance(screen_index, int) and screen_index < len(screens) else f"화면 {screen_index + 1}"
        feedback = _read_string(entry.get("feedback"))
        if feedback:
            screen_feedback_lines.append(f"- screenIndex {screen_index} ({screen_name}): {feedback}")

    pin_comment_lines = []
    for comment in _as_list(pin_comments):
        if not isinstance(comment, dict):
            continue
        screen_index = comment.get("screenIndex", 0)
        screen_name = screens[screen_index].get("name") if isinstance(screen_index, int) and screen_index < len(screens) else f"화면 {screen_index + 1}"
        content = _read_string(comment.get("content"))
        if content:
            pin_comment_lines.append(
                f"- screenIndex {screen_index} ({screen_name}) / {comment.get('type')} / x={comment.get('x')}, y={comment.get('y')}: {content}"
            )

    flow_analysis_lines = []
    for item in _as_list(flow_analysis):
        if not isinstance(item, dict):
            continue
        screen_index = item.get("screenIndex", 0)
        screen_name = screens[screen_index].get("name") if isinstance(screen_index, int) and screen_index < len(screens) else f"화면 {screen_index + 1}"
        suggestions = " / ".join(str(point) for point in _as_list(item.get("suggestions")) if str(point).strip()) or "없음"
        flow_analysis_lines.append(
            " / ".join(
                part
                for part in [
                    f"- screenIndex {screen_index} ({screen_name})",
                    f"suggestions={suggestions}",
                    f"transitionFromPrevious={item.get('transitionFromPrevious')}" if item.get("transitionFromPrevious") else None,
                    f"expectedNextAction={item.get('expectedNextAction')}" if item.get("expectedNextAction") else None,
                ]
                if part
            )
        )

    return "\n".join(
        [
            "[화면별 코멘트]",
            *screen_feedback_lines,
            "",
            "[위치 기반 코멘트]",
            *pin_comment_lines,
            "",
            "[Flow 서술 보조근거 - 숫자 점수 근거로 쓰지 말 것]",
            *flow_analysis_lines,
        ]
    )


def build_ui_scoring_prompt(*, test_name, test_description, scope_type, persona_context, screens, screen_feedbacks, pin_comments, flow_analysis):
    is_flow = scope_type == "flow" and len(screens) > 1
    evidence_context = build_ui_scoring_evidence_context(
        screens=screens,
        screen_feedbacks=screen_feedbacks,
        pin_comments=pin_comments,
        flow_analysis=flow_analysis,
    )
    screen_manifest = "\n".join(
        f"{index + 1}. {screen.get('name') or f'화면 {index + 1}'} ({screen.get('id') or f'screen-{index + 1}'}) - {screen.get('sourceType') or screen.get('source_type') or ''}"
        for index, screen in enumerate(screens)
    )
    return "\n".join(
        part
        for part in [
            "당신은 UX 리서치 코멘트를 점수 계산용 JSON으로 구조화하는 분석기입니다.",
            "반드시 한국어 JSON만 반환하세요.",
            "",
            "[테스트 정보]",
            f"테스트명: {test_name}",
            f"테스트 설명: {test_description}" if test_description else None,
            f"테스트 범위: {'사용자 Flow' if is_flow else '단일 화면'}",
            "",
            "[퍼소나]",
            persona_context,
            "",
            "[화면 목록]",
            screen_manifest,
            "",
            "[평가 기준]",
            "- Flow 평가 metric: 혼란도(직관성, 인지 용이성, 맥락 관계성), 이탈 위험(행동 유도성, 관심/동기 적합성), 효율성(입력 부담, 클릭/탐색 동선, 필수 조작 가능성, 완료 조건 명확성)"
            if is_flow
            else "- 화면 평가 metric: 명확성(직관성, 인지 용이성), 사용성(유용성, 유연성, 행동 유도성), 만족도(디자인 매력도, 서비스 신뢰도)",
            "- Flow 평가에서는 testType=flow만 사용하고, 명확성/사용성/만족도 metric은 절대 쓰지 마세요."
            if is_flow
            else "- 화면 평가에서는 testType=screen만 사용하고, 혼란도/이탈 위험/효율성 metric은 절대 쓰지 마세요.",
            "- 이탈 위험은 사용성 마찰뿐 아니라, 혜택/가치/필요성이 와닿지 않아 계속 진행할 동기가 떨어지는 코멘트도 포함하세요.",
            "- Flow 평가에서 positive 이벤트는 일반적인 칭찬이 아니라, 혼란도/이탈 위험을 낮추거나 효율성을 높이는 명확한 진행 지원 근거가 있을 때만 사용하세요."
            if is_flow
            else None,
            "- 이탈 위험 severity는 퍼소나 렌즈로 적극적으로 벌려 쓰세요. 단순 불편/취향은 1~2, 퍼소나의 관심사·숙련도·기존 경험 때문에 계속 진행할 이유가 약해지는 경우는 3, 가격/혜택/신뢰/개인정보/시간 부담이 퍼소나의 핵심 기준과 충돌해 중단을 고민하면 4, 대체 서비스 탐색이나 진행 포기에 가까우면 5입니다."
            if is_flow
            else None,
            "- 같은 화면의 같은 문제라도 퍼소나 프로필에 근거한 민감도가 다르면 severity를 다르게 산정하세요. 예: 개인정보 경계가 큰 퍼소나는 동의/인증 불명확성에 더 민감하고, 가격 비교 성향이 큰 퍼소나는 혜택/요금 정보 부족에 더 민감합니다."
            if is_flow
            else None,
            "- 단, 차이를 만들기 위한 임의 가정은 금지합니다. sourceComment에 드러난 퍼소나 근거 또는 persona pack의 명시 정보와 연결될 때만 severity/personaRelevance를 높이세요."
            if is_flow
            else None,
            "- personaRelevance는 해당 코멘트가 퍼소나의 성향, 관심사, 기존 경험, 불안, 선택 기준에서 직접 추론될수록 4~5로 두고, 일반 사용성 관찰이면 1~2로 두세요."
            if is_flow
            else None,
            "- Flow metric 분리: 헷갈림·맥락 파악 어려움→혼란도, 계속할 동기·신뢰·가치 저하→이탈 위험, 클릭·탐색·입력·단계 수·완료 조건 부담→효율성."
            if is_flow
            else None,
            "- '눌러보다/하나씩 확인/스크롤·탐색/단계가 많다'처럼 수행 부담이 드러나면 효율성 매핑을 검토하세요. 혼란·이탈과 다른 축의 근거면 secondary로 함께 매핑해도 됩니다."
            if is_flow
            else None,
            "- 같은 UI 문제의 같은 측면을 두 metric에 중복 매핑하지 마세요. 측면이 다르면 primary 1개와 secondary 1~2개로 나누세요."
            if is_flow
            else None,
            "",
            "[구조화 규칙]",
            "- keyElements는 조사 목적과 화면 목록을 바탕으로 중요한 UI/UX 요소를 3~8개 추출하세요.",
            "- polarity는 positive 또는 negative만 사용하세요.",
            "- polarity는 칭찬/개선 방향만 뜻합니다. Critical/Major/Minor 중요도 판단은 positive/negative와 독립적으로 severity, elementImportance, personaRelevance로 결정됩니다.",
            "- 하나의 sourceComment는 최대 3개 metric까지만 매핑하세요.",
            "- primary metric은 1개만 두고 impactMultiplier=1.0으로 둡니다.",
            "- secondary metric은 최대 2개까지 두고 impactMultiplier=0.6으로 둡니다.",
            "- 모든 pinComments.content는 최소 1개의 analysisEvent로 매핑하세요. 칭찬 코멘트도 중요한 핵심 요소라면 높은 severity/elementImportance를 줄 수 있습니다.",
            "- severity는 1~5, elementImportance는 0.5~1.5, personaRelevance는 1~5, confidence는 0~1입니다.",
            "- severity는 코멘트 강도에 따라 범위를 적극적으로 쓰세요. 사소한 표현 선호는 1~2, 행동 중단/신뢰 저하/목표 실패 가능성은 4~5입니다.",
            "- personaRelevance는 일반 사용성 코멘트면 1~2, 퍼소나의 직업/경험/니즈/불안/선택 기준과 직접 연결되면 4~5로 두세요.",
            "- elementImportance는 테스트 목적과 task 성공에 직접 연결되는 핵심 요소면 1.2~1.5, 보조 장식/문구 수준이면 0.5~0.9로 두세요.",
            "- 주요 요소와 매칭되면 matchedKeyElement에 이름을 넣고 그 importance를 우선 사용하세요.",
            "- 각 이벤트는 근거 코멘트와 명확하게 연결되는 metric에만 매핑하세요.",
            "- sourceComment는 반드시 화면별 코멘트 또는 위치 기반 코멘트의 퍼소나 발화를 근거로 삼으세요. Flow 서술 보조근거의 숫자나 일반 요약만으로 이벤트를 만들지 마세요."
            if is_flow
            else None,
            "- reason에는 '어떤 UI 문제인가'뿐 아니라 '왜 이 퍼소나에게 그 강도로 작동하는가'를 함께 쓰세요." if is_flow else None,
            "",
            "[근거 코멘트]",
            evidence_context,
            "",
            "[반환 JSON]",
            '{"keyElements":[{"name":"string","importance":1.5,"relatedMetrics":["혼란도"],"reason":"string"}],"analysisEvents":[{"testType":"flow","metric":"혼란도","subMetric":"직관성","targetElement":"string","matchedKeyElement":null,"polarity":"negative","severity":3,"elementImportance":1.0,"personaRelevance":3,"confidence":0.8,"mappingRole":"primary","impactMultiplier":1.0,"screenIndex":0,"stepIndex":0,"reason":"string","sourceComment":"string"}]}'
            if is_flow
            else '{"keyElements":[{"name":"string","importance":1.5,"relatedMetrics":["명확성"],"reason":"string"}],"analysisEvents":[{"testType":"screen","metric":"명확성","subMetric":"직관성","targetElement":"string","matchedKeyElement":null,"polarity":"negative","severity":3,"elementImportance":1.0,"personaRelevance":3,"confidence":0.8,"mappingRole":"primary","impactMultiplier":1.0,"screenIndex":0,"stepIndex":null,"reason":"string","sourceComment":"string"}]}',
        ]
        if part
    )
