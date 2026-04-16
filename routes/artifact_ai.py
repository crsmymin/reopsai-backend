"""
Artifact AI 수정 관련 API 라우트
텍스트 선택 수정 기능을 위한 엔드포인트
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt
from services.openai_service import openai_service
from services.gemini_service import gemini_service
from routes.auth import tier_required
from utils.b2b_access import get_owner_ids_for_request
from rag_system.improved.improved_vector_db_service import VectorDBServiceWrapper
from db.engine import session_scope
from db.models.core import Artifact, ArtifactEditHistory
from sqlalchemy import select
import json
import re
import traceback

artifact_ai_bp = Blueprint('artifact_ai', __name__, url_prefix='/api')

# VectorDB 서비스 초기화 (RAG 검색용)
try:
    _vector_service = VectorDBServiceWrapper(
        db_path="./chroma_db",
        collection_name="ux_rag"
    )
except Exception as e:
    print(f"[WARN] artifact_ai: VectorDB 초기화 실패 (RAG 검색 비활성화): {e}")
    _vector_service = None


def _get_owner_ids_for_request(user_id_int):
    owner_ids, _team_id = get_owner_ids_for_request(user_id_int)
    return owner_ids


def _require_artifact_access(artifact_id, owner_ids):
    """아티팩트 존재/권한 확인 후 artifact row 반환."""
    if session_scope is None:
        return None, (jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500)
    with session_scope() as db_session:
        artifact_obj = db_session.execute(
            select(Artifact).where(Artifact.id == artifact_id).limit(1)
        ).scalar_one_or_none()
    if not artifact_obj:
        return None, (jsonify({'success': False, 'error': '아티팩트를 찾을 수 없습니다.'}), 404)

    artifact = {
        'id': artifact_obj.id,
        'content': artifact_obj.content,
        'owner_id': artifact_obj.owner_id,
        'study_id': artifact_obj.study_id,
    }
    artifact_owner_id = str(artifact.get('owner_id', ''))
    if artifact_owner_id not in owner_ids:
        return None, (jsonify({'success': False, 'error': '접근 권한이 없습니다.'}), 403)

    return artifact, None


@artifact_ai_bp.route('/artifacts/<int:artifact_id>/edit_history', methods=['GET'])
@tier_required(['free'])
def list_artifact_edit_history(artifact_id):
    """아티팩트 수정 히스토리(적용 전/후 스냅샷) 목록 조회"""
    try:
        if session_scope is None:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        user_id_header = request.headers.get('X-User-ID')
        if not user_id_header:
            return jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401

        try:
            user_id_int = int(user_id_header)
        except Exception:
            user_id_int = user_id_header

        owner_ids = _get_owner_ids_for_request(user_id_int)
        _, err_resp = _require_artifact_access(artifact_id, owner_ids)
        if err_resp:
            return err_resp

        limit = request.args.get('limit', '50')
        try:
            limit = max(1, min(200, int(limit)))
        except Exception:
            limit = 50

        with session_scope() as db_session:
            rows = db_session.execute(
                select(ArtifactEditHistory)
                .where(ArtifactEditHistory.artifact_id == artifact_id)
                .order_by(ArtifactEditHistory.created_at.desc())
                .limit(limit)
            ).scalars().all()

        history = [
            {
                'id': str(row.id),
                'artifact_id': row.artifact_id,
                'user_id': row.user_id,
                'prompt': row.prompt,
                'source': row.source,
                'before_markdown': row.before_markdown,
                'after_markdown': row.after_markdown,
                'selection_from': row.selection_from,
                'selection_to': row.selection_to,
                'created_at': row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        return jsonify({'success': True, 'history': history}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@artifact_ai_bp.route('/artifacts/<int:artifact_id>/edit_history', methods=['POST'])
@tier_required(['free'])
def create_artifact_edit_history(artifact_id):
    """아티팩트 수정 히스토리(적용 전/후 스냅샷) 저장"""
    try:
        if session_scope is None:
            return jsonify({'success': False, 'error': '데이터베이스 연결 실패'}), 500

        data = request.json or {}
        before_markdown = (data.get('before_markdown') or '').strip()
        after_markdown = (data.get('after_markdown') or '').strip()
        prompt = (data.get('prompt') or '').strip()
        source = (data.get('source') or '').strip()
        selection_from = data.get('selection_from')
        selection_to = data.get('selection_to')

        if before_markdown == "" or after_markdown == "":
            return jsonify({'success': False, 'error': 'before_markdown / after_markdown가 필요합니다.'}), 400

        user_id_header = request.headers.get('X-User-ID')
        if not user_id_header:
            return jsonify({'success': False, 'error': '사용자 인증이 필요합니다.'}), 401

        try:
            user_id_int = int(user_id_header)
        except Exception:
            user_id_int = user_id_header

        owner_ids = _get_owner_ids_for_request(user_id_int)
        _, err_resp = _require_artifact_access(artifact_id, owner_ids)
        if err_resp:
            return err_resp

        with session_scope() as db_session:
            row = ArtifactEditHistory(
                artifact_id=artifact_id,
                user_id=user_id_int if isinstance(user_id_int, int) else None,
                prompt=prompt,
                source=source or None,
                before_markdown=before_markdown,
                after_markdown=after_markdown,
                selection_from=selection_from if isinstance(selection_from, int) else None,
                selection_to=selection_to if isinstance(selection_to, int) else None,
            )
            db_session.add(row)
            db_session.flush()
            db_session.refresh(row)
            created = {
                'id': str(row.id),
                'artifact_id': row.artifact_id,
                'user_id': row.user_id,
                'prompt': row.prompt,
                'source': row.source,
                'before_markdown': row.before_markdown,
                'after_markdown': row.after_markdown,
                'selection_from': row.selection_from,
                'selection_to': row.selection_to,
                'created_at': row.created_at.isoformat() if row.created_at else None,
            }

        return jsonify({'success': True, 'history': created}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@artifact_ai_bp.route('/artifacts/<int:artifact_id>/modify', methods=['POST'])
@tier_required(['free'])
def modify_artifact_text(artifact_id):
    """
    선택된 텍스트를 AI로 수정하는 엔드포인트
    
    요청 Body:
    {
        "selected_text": "수정할 원본 텍스트",
        "modification_prompt": "사용자 요청 (예: 더 정중하게 바꿔줘)",
        "full_context": "문서 전체 내용 (옵션, 문맥 파악용)"
    }
    
    Returns:
    {
        "success": true,
        "modified_text": "수정된 텍스트",
        "message": "텍스트가 성공적으로 수정되었습니다."
    }
    """
    try:
        if session_scope is None:
            return jsonify({
                'success': False,
                'error': '데이터베이스 연결 실패'
            }), 500
        
        # 요청 데이터 검증
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'error': '요청 데이터가 필요합니다.'
            }), 400
        
        selected_text = data.get('selected_text', '').strip()
        # 신규(user_prompt) + 기존(modification_prompt) 모두 지원
        user_prompt = (data.get('user_prompt') or data.get('modification_prompt') or '').strip()
        full_context = data.get('full_context', '').strip()
        selected_markdown_hint = (data.get('selected_markdown_hint') or '').strip()
        
        if not selected_text:
            return jsonify({
                'success': False,
                'error': 'selected_text가 필요합니다.'
            }), 400
        
        if not user_prompt:
            return jsonify({
                'success': False,
                'error': 'user_prompt가 필요합니다.'
            }), 400
        
        # 사용자 인증 확인
        user_id_header = request.headers.get('X-User-ID')
        if not user_id_header:
            return jsonify({
                'success': False,
                'error': '사용자 인증이 필요합니다.'
            }), 401
        
        try:
            user_id_int = int(user_id_header)
        except Exception:
            user_id_int = user_id_header
        
        # JWT에서 tier / team_id 정보 확인 (B2B 모드 지원)
        owner_ids = _get_owner_ids_for_request(user_id_int)
        
        # 아티팩트 존재 및 권한 확인
        artifact, err_resp = _require_artifact_access(artifact_id, owner_ids)
        if err_resp:
            return err_resp
        
        # RAG 검색: user_prompt + selected_text로 관련 원칙/예시 검색 (도메인 지식 보강)
        rag_principles = ""
        rag_examples = ""
        if _vector_service:
            try:
                # 검색 쿼리: user_prompt의 핵심 키워드 + selected_text의 주제 추출
                # 간단한 키워드 추출: user_prompt에서 명사/동사 추출 (한글 기준)
                query_parts = []
                # user_prompt에서 핵심 키워드 추출 (간단 버전: 2글자 이상 단어)
                prompt_words = re.findall(r'[가-힣]{2,}', user_prompt)
                query_parts.extend(prompt_words[:5])  # 최대 5개
                # selected_text에서도 주제 추출 (헤딩이나 첫 문장)
                selected_lines = selected_text.split('\n')[:3]
                for line in selected_lines:
                    words = re.findall(r'[가-힣]{2,}', line)
                    query_parts.extend(words[:3])
                
                rag_query = ' '.join(set(query_parts))[:200]  # 중복 제거, 길이 제한
                
                if rag_query:
                    rag_results = _vector_service.improved_service.hybrid_search(
                        query_text=rag_query,
                        principles_n=2,  # 원칙 2개
                        examples_n=2,    # 예시 2개
                        topics=["계획서", "리서치", "조사", "연구", "가설", "방법론", "대상자"],
                    )
                    rag_principles = _vector_service.improved_service.context_optimization(
                        rag_results.get("principles", ""), max_length=800
                    )
                    rag_examples = _vector_service.improved_service.context_optimization(
                        rag_results.get("examples", ""), max_length=600
                    )
            except Exception as e:
                print(f"[WARN] artifact_ai: RAG 검색 실패 (계속 진행): {e}")
                rag_principles = ""
                rag_examples = ""

        # LLM System Prompt (리서치 전문가 수준으로 강화 + RAG 컨텍스트 포함)
        # 중요: JSON 모드를 사용하려면 프롬프트에 "JSON"이라는 단어가 반드시 포함되어야 함
        system_prompt = (
            "You must output JSON. 당신은 리서치 계획서를 작성하고 검토하는 '수석 리서처'입니다. "
            "전문가 수준의 논리적 일관성, 전문 용어 사용, 그리고 전체 문서 맥락을 깊이 이해한 상태에서 수정해야 합니다.\n\n"
            "**[핵심 원칙]**\n"
            "1. **전체 맥락 활용**: full_context에 담긴 연구 목표, 조사 대상, 방법론, 일정 등 전체 구조를 먼저 파악하고, "
            "selected_text가 그 맥락 안에서 어떤 역할을 하는지 이해한 뒤 수정하세요.\n"
            "2. **논리적 일관성**: 수정한 부분이 문서의 다른 섹션(연구 목표, 방법론, 일정 등)과 논리적으로 모순되지 않아야 합니다.\n"
            "3. **전문 용어 유지**: 리서치 전문 용어(예: FGI, UT, 정성/정량, 타겟 그룹, 선별 기준 등)를 정확하게 사용하고, "
            "일반인 수준의 표현으로 격하하지 마세요.\n"
            "4. **원문 구조 최대한 유지**: selected_markdown_hint가 제공되면, 그 구조(헤딩/리스트/표/강조/인용 등)를 가능한 한 유지하세요.\n"
            "5. **요청만 정확히 반영**: user_prompt에서 요청한 수정만 정확하게 반영하고, 요청하지 않은 부분은 원문을 최대한 보존하세요.\n"
            "6. **전체 텍스트 유지(매우 중요)**: modified에는 selected_text의 '일부'만 반환하면 안 됩니다. "
            "반드시 selected_text 전체를 반환하되, user_prompt가 요구하는 부분만 수정하고 나머지는 가능한 한 원문을 그대로 유지하세요.\n"
            "6. **예시 기반 학습**: user_prompt에 '~처럼', '~스타일로', '~참고해서' 같은 표현이 있으면, "
            "full_context에서 해당 패턴/예시를 찾아서 그 스타일을 적용하세요.\n"
            "7. **도메인 지식 활용**: 아래 '참고 원칙/예시'를 활용하여 전문가 수준의 추론을 수행하세요. "
            "도메인 지식이 부족해도, full_context의 다른 섹션과 참고 자료를 종합해서 논리적으로 추론하세요.\n"
            "8. **추론을 통한 풍부한 작성**: user_prompt의 요청을 단순히 반영하는 것을 넘어서, 전체 맥락과 도메인 지식을 종합적으로 추론하여 "
            "selected_text를 더 풍부하고 완성도 높게 작성하세요. 관련 근거, 구체적인 예시, 논리적 연결고리 등을 자연스럽게 보강하되, "
            "원문의 핵심 의도와 구조는 유지하세요.\n\n"
            "**[출력 형식]**\n"
            "- 오직 JSON만 출력한다. 다른 텍스트는 절대 포함하지 않는다.\n"
            "- 설명, 주석, 따옴표로 감싼 추가 텍스트, 마크다운 코드블록(```)을 절대 포함하지 않는다.\n"
            "- 출력 JSON 형식은 반드시 {\"original\": \"...\", \"modified\": \"...\"} 이어야 한다.\n"
            "- original은 반드시 입력으로 받은 selected_text와 동일한 문자열이어야 한다.\n"
            "- modified는 가능한 한 Markdown 형식으로 작성한다.\n"
            "- JSON을 반드시 완전하게 출력해야 한다. 중간에 잘리면 안 된다.\n"
            "- modified 필드의 값이 길어도 반드시 완전한 JSON으로 출력해야 한다.\n"
        )

        # 프롬프트 구성 (RAG 컨텍스트 포함)
        rag_section = ""
        if rag_principles or rag_examples:
            rag_section = "\n**[참고 원칙 및 예시 (RAG 검색 결과)]**\n"
            if rag_principles:
                rag_section += f"원칙:\n{rag_principles}\n\n"
            if rag_examples:
                rag_section += f"예시:\n{rag_examples}\n\n"
            rag_section += "위 원칙과 예시를 참고하여 전문가 수준으로 수정하세요.\n\n"

        llm_prompt = (
            f"{system_prompt}\n\n"
            f"{rag_section}"
            f"**[전체 문서 맥락 (full_context)]**\n{full_context}\n\n"
            f"**[수정할 부분 (selected_text)]**\n{selected_text}\n\n"
            f"**[원문 구조 힌트 (selected_markdown_hint)]**\n{selected_markdown_hint}\n\n"
            f"**[사용자 요청 (user_prompt)]**\n{user_prompt}\n\n"
            "위 정보를 바탕으로, 전체 맥락과 참고 원칙/예시를 활용하여 "
            "selected_text 전체를(원문 유지 + 요청 부분만 수정) 전문가 수준으로 수정한 후 JSON으로만 출력하세요."
        )
        
        # Gemini 2.5 Flash로 AI 수정 호출
        # 긴 텍스트 수정을 위해 토큰 제한을 충분히 설정
        # Gemini는 JSON 출력 시 truncation 문제가 있어서 충분히 큰 값 설정
        estimated_tokens = max(8192, min(16384, len(selected_text) * 4))
        
        generation_config = {
            'temperature': 0.3,
            'max_output_tokens': estimated_tokens,
        }
        
        response = gemini_service.generate_response(
            prompt=llm_prompt,
            generation_config=generation_config,
            model_name="gemini-2.5-flash"
        )
        
        if not response.get('success'):
            return jsonify({
                'success': False,
                'error': f"AI 수정 실패: {response.get('error', '알 수 없는 오류')}"
            }), 500
        
        raw = (response.get('content') or '').strip()
        
        # 응답 전처리: 마크다운 코드 블록 제거
        # ```json ... ``` 또는 ``` ... ``` 형태 제거
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
        raw = re.sub(r"\n?\s*```$", "", raw, flags=re.MULTILINE).strip()
        
        # 여러 줄에 걸친 코드 블록 제거
        raw = re.sub(r"```(?:json)?\s*\n", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\n\s*```", "", raw)
        
        # 전처리 후 JSON이 완전하지 않은 경우 체크 (잘림 감지)
        if raw:
            raw_stripped = raw.strip()
            # JSON이 시작되었는데 끝나지 않은 경우 (잘림 의심)
            if '{' in raw_stripped and not raw_stripped.endswith('}'):
                # 중괄호 균형 체크
                open_braces = raw_stripped.count('{')
                close_braces = raw_stripped.count('}')
                if open_braces > close_braces:
                    print(f"[WARN] Gemini 응답이 잘렸을 가능성: 중괄호 불균형 ({open_braces}개 열림, {close_braces}개 닫힘)")
                    print(f"[WARN] 응답 길이: {len(raw)}, 마지막 200자: {raw[-200:]}")
                    return jsonify({
                        'success': False,
                        'error': f"AI 응답이 완전하지 않습니다. 응답이 중간에 잘렸을 수 있습니다. 다시 시도해주세요."
                    }), 500

        # JSON 파싱 (강화된 로직: 여러 단계 시도)
        parsed = None
        parse_error = None
        
        # Step 1: 직접 파싱 시도
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            parse_error = str(e)
            # Step 2: 첫 '{'부터 마지막 '}'까지 추출 후 재시도
            try:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    json_str = raw[start:end + 1]
                    parsed = json.loads(json_str)
            except (json.JSONDecodeError, ValueError) as e2:
                parse_error = f"{parse_error}; {str(e2)}"
                # Step 3: 중첩된 중괄호 처리 (가장 바깥쪽 중괄호 쌍 찾기)
                try:
                    brace_count = 0
                    start = -1
                    for i, char in enumerate(raw):
                        if char == '{':
                            if start == -1:
                                start = i
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0 and start != -1:
                                json_str = raw[start:i + 1]
                                parsed = json.loads(json_str)
                                break
                except (json.JSONDecodeError, ValueError) as e3:
                    parse_error = f"{parse_error}; {str(e3)}"
                    parsed = None

        # Fallback: 파싱 실패 시에도 프론트엔드가 죽지 않도록 처리
        if not isinstance(parsed, dict):
            print(f"[WARN] JSON 파싱 실패. 원본 응답 (처음 500자): {raw[:500]}")
            print(f"[WARN] 파싱 오류: {parse_error}")
            
            # Fallback: raw_response를 그대로 modified로 사용
            # original은 selected_text로 설정
            return jsonify({
                'success': True,
                'original': selected_text,
                'modified': raw,  # 파싱 실패 시 원본 응답을 그대로 사용
                'message': 'AI 수정 제안을 생성했습니다. (JSON 파싱 경고: 응답이 완전한 JSON 형식이 아닐 수 있습니다.)'
            })

        original_out = (parsed.get('original') or '').strip()
        modified_out = (parsed.get('modified') or '').strip()

        # original은 반드시 selected_text와 동일해야 함
        if not original_out:
            original_out = selected_text
        if original_out != selected_text:
            original_out = selected_text

        # modified가 비어있으면 Fallback
        if not modified_out:
            print(f"[WARN] modified 필드가 비어있음. raw_response를 사용합니다.")
            modified_out = raw if raw else selected_text

        # --- 안전장치: "전체 selected_text" 대신 일부만 반환하는 오류 감지 ---
        # 툴바 AI 수정(전체 재작성)은 selected_text가 문서 전체인 경우가 많아,
        # 모델이 요청한 섹션만 반환하면 프론트에서 그대로 덮어써져 문서가 잘리는 사고가 발생할 수 있음.
        def _looks_partial(full_text: str, candidate: str) -> bool:
            try:
                if not full_text or not candidate:
                    return False
                # 짧은 선택(드래그 수정 등)에는 적용하지 않음
                if len(full_text) < 1200:
                    return False
                return len(candidate) < int(len(full_text) * 0.7)
            except Exception:
                return False

        if _looks_partial(selected_text, modified_out):
            print(
                f"[WARN] modified가 부분만 반환된 것으로 의심됨. "
                f"selected_text_len={len(selected_text)}, modified_len={len(modified_out)}"
            )

            # 1회 재시도: "selected_text 전체 반환"을 더 강하게 강제
            harden_prompt = (
                llm_prompt
                + "\n\n🚨 재요청: 방금 응답은 selected_text의 일부만 반환한 것으로 보입니다.\n"
                + "반드시 selected_text 전체를 modified에 반환하세요. (요청한 부분만 수정 + 나머지는 원문 유지)\n"
                + "형식은 동일하게 {\"original\": \"...\", \"modified\": \"...\"} JSON만 출력하세요."
            )
            retry = gemini_service.generate_response(
                prompt=harden_prompt,
                generation_config=generation_config,
                model_name="gemini-2.5-flash",
            )
            if retry.get("success"):
                raw2 = (retry.get("content") or "").strip()
                raw2 = re.sub(r"^```(?:json)?\s*\n?", "", raw2, flags=re.IGNORECASE | re.MULTILINE).strip()
                raw2 = re.sub(r"\n?\s*```$", "", raw2, flags=re.MULTILINE).strip()
                raw2 = re.sub(r"```(?:json)?\s*\n", "", raw2, flags=re.IGNORECASE)
                raw2 = re.sub(r"\n\s*```", "", raw2)

                parsed2 = None
                try:
                    parsed2 = json.loads(raw2)
                except Exception:
                    # Step 2: 첫 '{'부터 마지막 '}'까지 추출 후 재시도
                    try:
                        start2 = raw2.find("{")
                        end2 = raw2.rfind("}")
                        if start2 != -1 and end2 != -1 and end2 > start2:
                            parsed2 = json.loads(raw2[start2:end2 + 1])
                    except Exception:
                        parsed2 = None

                if isinstance(parsed2, dict):
                    modified2 = (parsed2.get("modified") or "").strip()
                    if modified2 and not _looks_partial(selected_text, modified2):
                        modified_out = modified2

        # 재시도 후에도 여전히 부분 응답이면, 적용 사고를 막기 위해 실패로 반환
        if _looks_partial(selected_text, modified_out):
            return jsonify({
                "success": False,
                "error": "AI가 문서 전체가 아니라 일부만 반환했습니다. 다시 생성해 주세요. (문서 전체 반환 강제 중)",
            }), 200
        
        # Review & Apply: 여기서는 DB를 수정하지 않고 diff만 반환
        return jsonify({
            'success': True,
            'original': original_out,
            'modified': modified_out,
            'message': 'AI 수정 제안을 생성했습니다.'
        })
        
    except Exception as e:
        print(f"[ERROR] modify_artifact_text 예외 발생: artifact_id={artifact_id}, error={str(e)}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'서버 오류가 발생했습니다: {str(e)}'
        }), 500
