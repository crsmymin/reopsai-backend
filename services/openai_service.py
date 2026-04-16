import os
from pathlib import Path
from openai import OpenAI
from openai import RateLimitError as OpenAIRateLimitError
from dotenv import load_dotenv
import time

from pii_utils import sanitize_prompt_for_llm
from telemetry import log_duration, log_tokens

class OpenAIService:
    """
    OpenAI GPT API와의 통신을 관리하는 서비스 클래스입니다.
    키 로테이션(Key Rotation) 기능 포함.
    """
    def __init__(self):
        """
        클래스 초기화 시, API 키 목록을 설정하고 OpenAI 클라이언트를 생성합니다.
        """
        try:
            # 환경변수 파일 로드
            backend_dir = Path(__file__).resolve().parent.parent  # backend/ 폴더
            env_files = [
                backend_dir / '.env.production',
                backend_dir / '.env',
                backend_dir / '.env.local'
            ]
            
            for env_file in env_files:
                if env_file.exists():
                    load_dotenv(env_file)
                    print(f"✅ OpenAIService: Loaded env from {env_file}")
                    break
            
            # ✅ 여러 API 키 로드 (쉼표로 구분 또는 KEY_1, KEY_2 형식)
            api_keys_str = os.getenv("OPENAI_API_KEYS")
            
            if api_keys_str:
                # 쉼표로 구분된 키 목록 파싱
                self.api_keys = [key.strip() for key in api_keys_str.split(',') if key.strip()]
            else:
                # 개별 환경변수에서 키 찾기 (OPENAI_API_KEY_1, OPENAI_API_KEY_2, ...)
                self.api_keys = []
                i = 1
                while True:
                    key = os.getenv(f"OPENAI_API_KEY_{i}")
                    if key:
                        self.api_keys.append(key)
                        i += 1
                    else:
                        break
                
                # 단일 키도 지원 (하위 호환성)
                if not self.api_keys:
                    single_key = os.getenv("OPENAI_API_KEY")
                    if single_key:
                        self.api_keys = [single_key]
            
            if not self.api_keys:
                raise ValueError("OPENAI_API_KEY 또는 OPENAI_API_KEYS 환경변수가 설정되지 않았습니다.")
            
            # 현재 사용 중인 키 인덱스
            self.current_key_index = 0
            
            # 실패한 키의 쿨다운 시간 추적
            self.key_cooldown = {}
            # 쿨다운 시간을 환경변수로 설정 가능 (기본값: 5분)
            self.cooldown_duration = int(os.getenv("OPENAI_KEY_COOLDOWN_SECONDS", 300))  # 300초 = 5분
            
            # 첫 번째 키로 클라이언트 초기화
            self.client = OpenAI(api_key=self.api_keys[0])
            print(f"✅ OpenAIService 초기화 완료. ({len(self.api_keys)}개의 API 키 로드됨)")

        except Exception as e:
            print(f"OpenAIService 초기화 실패: {e}")
            self.client = None
            self.api_keys = []
            self.current_key_index = 0
            self.key_cooldown = {}
            self.cooldown_duration = int(os.getenv("OPENAI_KEY_COOLDOWN_SECONDS", 300))  # 300초 = 5분

    def _is_rate_limit_error(self, error):
        """
        429 Rate Limit 오류인지 확인합니다.
        """
        # OpenAI의 RateLimitError 확인
        if isinstance(error, OpenAIRateLimitError):
            return True
        
        # 오류 메시지에서도 확인
        error_str = str(error).lower()
        if any(keyword in error_str for keyword in ['rate limit', '429', 'quota', 'requests per minute']):
            return True
        
        return False

    def _switch_to_next_key(self):
        """
        다음 사용 가능한 키로 전환합니다.
        """
        original_index = self.current_key_index
        
        for _ in range(len(self.api_keys)):
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            key = self.api_keys[self.current_key_index]
            
            # 쿨다운 확인
            if key in self.key_cooldown:
                cooldown_end = self.key_cooldown[key] + self.cooldown_duration
                if time.time() < cooldown_end:
                    continue
                else:
                    del self.key_cooldown[key]
            
            # 새로운 키로 클라이언트 재생성
            self.client = OpenAI(api_key=key)
            print(f"🔄 API 키 전환: 인덱스 {self.current_key_index} (총 {len(self.api_keys)}개 중)")
            return True
        
        if original_index == self.current_key_index:
            print(f"⚠️ 모든 API 키가 쿨다운 중입니다.")
            return False
        
        return True

    def _mark_key_failed(self, key):
        """
        실패한 키에 쿨다운을 설정합니다.
        """
        self.key_cooldown[key] = time.time()
        print(f"⏸️ 키 쿨다운 설정: {key[:10]}... ({self.cooldown_duration}초, {self.cooldown_duration//60}분)")

    def generate_response(self, prompt, generation_config=None, model_name=None):
        """
        주어진 프롬프트와 설정에 따라 OpenAI API를 호출하고 응답을 반환합니다.
        Rate Limit 오류 발생 시 자동으로 키를 전환하여 재시도합니다.

        Args:
            prompt (str): LLM에 전달할 프롬프트 문자열.
            generation_config (dict, optional): 
                응답 생성 방식을 제어하는 설정. 
                (예: {"temperature": 0.1}). Defaults to None.
            model_name (str, optional): 사용할 모델명. 
                (예: "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"). 
                Defaults to "gpt-4o".

        Returns:
            dict: 성공 여부와 결과 또는 오류 메시지를 포함한 딕셔너리.
        """
        if not self.client:
            return {'success': False, 'error': 'OpenAI 클라이언트가 초기화되지 않았습니다.'}
        
        if not self.api_keys:
            return {'success': False, 'error': '사용 가능한 API 키가 없습니다.'}

        # 모델명 지정 (기본값: gpt-4o-mini)
        model = model_name or "gpt-4o-mini"

        # --- PII Redaction (LLM 전송 전) ---
        # 기본값: 켬 (OPENAI_LLM_PII_REDACTION=0 이면 비활성화)
        redaction_enabled = os.getenv("OPENAI_LLM_PII_REDACTION", "1") != "0"
        pii_meta = {"pii_redacted": False, "pii_counts": {"email": 0, "phone": 0, "rrn": 0}}
        prompt_to_send = prompt or ""
        if redaction_enabled:
            prompt_to_send, changed, counts = sanitize_prompt_for_llm(prompt_to_send)
            pii_meta = {"pii_redacted": bool(changed), "pii_counts": counts}
        
        # generation_config에서 설정 추출
        temperature = generation_config.get('temperature', 0.2) if generation_config else 0.7
        max_output_tokens = generation_config.get('max_output_tokens', None) if generation_config else None
        response_format = generation_config.get('response_format', None) if generation_config else None

        # 새 모델(gpt-5, o1 등)에서는 max_completion_tokens 사용, 기존 모델에서는 max_tokens 사용
        uses_new_api = model.startswith('gpt-5') or model.startswith('o1') or 'o3' in model

        # 새 모델(gpt-5 등)은 temperature 파라미터를 지원하지 않음 (기본값 1만 지원)
        supports_temperature = not (model.startswith('gpt-5'))

        # API 호출 파라미터 준비
        api_params = {
            'model': model,
            'messages': [
                {"role": "user", "content": prompt_to_send}
            ]
        }

        # temperature 파라미터 추가 (gpt-5 등은 제외)
        if supports_temperature:
            api_params['temperature'] = temperature

        # 토큰 제한 파라미터 추가
        if max_output_tokens:
            if uses_new_api:
                api_params['max_completion_tokens'] = max_output_tokens
            else:
                api_params['max_tokens'] = max_output_tokens

        # response_format 파라미터 추가 (JSON 모드 등)
        if response_format:
            api_params['response_format'] = response_format

        # 모든 키를 시도
        max_retries = len(self.api_keys)
        last_error = None
        
        for attempt in range(max_retries):
            try:
                t0 = time.time()
                # OpenAI API 호출
                response = self.client.chat.completions.create(**api_params)
                duration = time.time() - t0
                
                # 성공 시 쿨다운 초기화
                current_key = self.api_keys[self.current_key_index]
                if current_key in self.key_cooldown:
                    del self.key_cooldown[current_key]
                
                # 응답 텍스트 추출
                content = response.choices[0].message.content

                # 토큰 사용량 (best-effort)
                usage = {}
                try:
                    u = getattr(response, "usage", None)
                    if u is not None:
                        usage = {
                            "prompt_tokens": getattr(u, "prompt_tokens", None),
                            "completion_tokens": getattr(u, "completion_tokens", None),
                            "total_tokens": getattr(u, "total_tokens", None),
                        }
                except Exception:
                    usage = {}

                # 로깅: duration + token usage + model
                log_duration("LLM(OpenAI)", duration, extra=f"model={model}")
                log_tokens("openai", usage, extra=f"model={model}")
                
                # Gemini와 동일한 형식으로 반환
                return {'success': True, 'content': content, "usage": usage, **pii_meta}
            
            except Exception as e:
                last_error = e
                current_key = self.api_keys[self.current_key_index]
                
                # Rate Limit 오류인지 확인
                if self._is_rate_limit_error(e):
                    print(f"⚠️ Rate Limit 오류 감지 (시도 {attempt + 1}/{max_retries}): {str(e)[:100]}")
                    
                    # 실패한 키에 쿨다운 설정
                    self._mark_key_failed(current_key)
                    
                    # 다음 키로 전환
                    if self._switch_to_next_key():
                        continue
                    else:
                        return {
                            'success': False, 
                            'error': f'모든 API 키가 쿨다운 중입니다. 마지막 오류: {str(e)}'
                        }
                else:
                    # Rate Limit이 아닌 다른 오류
                    print(f"❌ 치명적 오류 (재시도 안 함): {str(e)}")
                    return {'success': False, 'error': str(e), **pii_meta}
        
        # 모든 키 시도 실패
        return {
            'success': False, 
            'error': f'모든 API 키의 시도가 실패했습니다. 마지막 오류: {str(last_error)}',
            **pii_meta
        }

# 클래스의 인스턴스를 생성하여 다른 파일에서 쉽게 가져다 쓸 수 있도록 합니다.
# 예: from services.openai_service import openai_service
openai_service = OpenAIService()