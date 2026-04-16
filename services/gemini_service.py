import os
import base64
from pathlib import Path
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from dotenv import load_dotenv
import time

from pii_utils import sanitize_prompt_for_llm
from telemetry import log_duration, log_tokens

class GeminiService:
    """
    Google Gemini API와의 통신을 관리하는 서비스 클래스입니다.
    키 로테이션(Key Rotation) 기능 포함.
    """
    def __init__(self):
        """
        클래스 초기화 시, API 키 목록을 설정하고 Gemini 모델을 로드합니다.
        """
        try:
            # ✅ 환경변수 파일 로드 추가
            backend_dir = Path(__file__).resolve().parent.parent  # backend/ 폴더
            env_files = [
                backend_dir / '.env.production',
                backend_dir / '.env',
                backend_dir / '.env.local'
            ]
            
            for env_file in env_files:
                if env_file.exists():
                    load_dotenv(env_file)
                    print(f"✅ GeminiService: Loaded env from {env_file}")
                    break
            
            # ✅ 여러 API 키 로드 (쉼표로 구분 또는 KEY_1, KEY_2 형식)
            # 방법 1: GOOGLE_API_KEYS="key1,key2,key3" 형식
            api_keys_str = os.getenv("GOOGLE_API_KEYS") or os.getenv("GEMINI_API_KEYS")
            
            if api_keys_str:
                # 쉼표로 구분된 키 목록 파싱
                self.api_keys = [key.strip() for key in api_keys_str.split(',') if key.strip()]
            else:
                # 방법 2: 개별 환경변수에서 키 찾기 (GOOGLE_API_KEY_1, GOOGLE_API_KEY_2, ...)
                self.api_keys = []
                i = 1
                while True:
                    key = os.getenv(f"GOOGLE_API_KEY_{i}") or os.getenv(f"GEMINI_API_KEY_{i}")
                    if key:
                        self.api_keys.append(key)
                        i += 1
                    else:
                        break
                
                # 방법 3: 단일 키도 지원 (하위 호환성)
                if not self.api_keys:
                    single_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
                    if single_key:
                        self.api_keys = [single_key]
            
            if not self.api_keys:
                raise ValueError("GOOGLE_API_KEY 또는 GOOGLE_API_KEYS 환경변수가 설정되지 않았습니다.")
            
            # 현재 사용 중인 키 인덱스
            self.current_key_index = 0
            
            # 실패한 키의 쿨다운 시간 추적 (선택사항)
            self.key_cooldown = {}  # {key: failure_timestamp}
            # 쿨다운 시간을 환경변수로 설정 가능 (기본값: 5분)
            self.cooldown_duration = int(os.getenv("GEMINI_KEY_COOLDOWN_SECONDS", 300))  # 300초 = 5분
            
            # 첫 번째 키로 초기 설정
            genai.configure(api_key=self.api_keys[0])

            # 2. 사용할 Gemini 모델을 설정합니다.
            self.model = genai.GenerativeModel('gemini-2.0-flash')
            print(f"✅ GeminiService 초기화 완료. ({len(self.api_keys)}개의 API 키 로드됨)")

        except Exception as e:
            print(f"GeminiService 초기화 실패: {e}")
            self.model = None
            self.api_keys = []
            self.current_key_index = 0
            self.key_cooldown = {}
            self.cooldown_duration = int(os.getenv("GEMINI_KEY_COOLDOWN_SECONDS", 300))  # 300초 = 5분

    def _is_rate_limit_error(self, error):
        """
        429 Rate Limit 오류인지 확인합니다.
        """
        # Google API의 ResourceExhausted 오류 확인
        if isinstance(error, google_exceptions.ResourceExhausted):
            return True
        
        # 오류 메시지에서도 확인
        error_str = str(error).lower()
        if any(keyword in error_str for keyword in ['resource exhausted', 'rate limit', '429', 'quota']):
            return True
        
        return False

    def _switch_to_next_key(self):
        """
        다음 사용 가능한 키로 전환합니다.
        """
        original_index = self.current_key_index
        
        # 모든 키를 한 바퀴 시도
        for _ in range(len(self.api_keys)):
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            key = self.api_keys[self.current_key_index]
            
            # 쿨다운이 끝났는지 확인
            if key in self.key_cooldown:
                cooldown_end = self.key_cooldown[key] + self.cooldown_duration
                if time.time() < cooldown_end:
                    # 아직 쿨다운 중
                    continue
                else:
                    # 쿨다운 종료 - 목록에서 제거
                    del self.key_cooldown[key]
            
            # 새로운 키로 설정
            genai.configure(api_key=key)
            print(f"🔄 API 키 전환: 인덱스 {self.current_key_index} (총 {len(self.api_keys)}개 중)")
            return True
        
        # 모든 키가 쿨다운 중
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
        주어진 프롬프트와 설정에 따라 Gemini API를 호출하고 응답을 반환합니다.
        Rate Limit 오류 발생 시 자동으로 키를 전환하여 재시도합니다.

        Args:
            prompt (str): LLM에 전달할 프롬프트 문자열.
            generation_config (dict, optional): 
                응답 생성 방식을 제어하는 설정. 
                (예: {"temperature": 0.1}). Defaults to None.
            model_name (str, optional): 사용할 모델명. 
                (예: "gemini-2.5-pro"). Defaults to None (기본 모델 사용).

        Returns:
            dict: 성공 여부와 결과 또는 오류 메시지를 포함한 딕셔너리.
        """
        if not self.model and not model_name:
            return {'success': False, 'error': 'Gemini 모델이 초기화되지 않았습니다.'}
        
        if not self.api_keys:
            return {'success': False, 'error': '사용 가능한 API 키가 없습니다.'}

        # --- PII Redaction (LLM 전송 전) ---
        # 기본값: 켬 (GEMINI_LLM_PII_REDACTION=0 이면 비활성화)
        redaction_enabled = os.getenv("GEMINI_LLM_PII_REDACTION", "1") != "0"
        pii_meta = {"pii_redacted": False, "pii_counts": {"email": 0, "phone": 0, "rrn": 0}}
        prompt_to_send = prompt or ""
        if redaction_enabled:
            prompt_to_send, changed, counts = sanitize_prompt_for_llm(prompt_to_send)
            pii_meta = {"pii_redacted": bool(changed), "pii_counts": counts}

        # 모든 키를 시도
        max_retries = len(self.api_keys)
        last_error = None
        
        for attempt in range(max_retries):
            try:
                current_key = self.api_keys[self.current_key_index]
                
                # 모델명이 지정된 경우 해당 모델을 사용, 아니면 기본 모델 사용
                model = genai.GenerativeModel(model_name) if model_name else self.model
                model_display = model_name or "gemini-2.0-flash"
                
                # API 호출
                t0 = time.time()
                response = model.generate_content(
                    prompt_to_send,
                    generation_config=generation_config
                )
                duration = time.time() - t0
                
                # 성공 시 쿨다운 초기화 (선택사항)
                if current_key in self.key_cooldown:
                    del self.key_cooldown[current_key]

                # 토큰 사용량 (best-effort)
                usage = {}
                try:
                    um = getattr(response, "usage_metadata", None) or getattr(response, "usageMetadata", None)
                    if um is not None:
                        usage = {
                            "prompt_tokens": getattr(um, "prompt_token_count", None) or getattr(um, "promptTokenCount", None),
                            "completion_tokens": getattr(um, "candidates_token_count", None) or getattr(um, "candidatesTokenCount", None),
                            "total_tokens": getattr(um, "total_token_count", None) or getattr(um, "totalTokenCount", None),
                        }
                except Exception:
                    usage = {}

                # 로깅: duration + token usage + model
                log_duration("LLM(Gemini)", duration, extra=f"model={model_display}")
                log_tokens("gemini", usage, extra=f"model={model_display}")
                
                # 성공적인 응답을 딕셔너리 형태로 반환
                return {'success': True, 'content': response.text, "usage": usage, **pii_meta}
            
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
                        # 다음 시도로 계속
                        continue
                    else:
                        # 모든 키가 쿨다운 중
                        return {
                            'success': False, 
                            'error': f'모든 API 키가 쿨다운 중입니다. 마지막 오류: {str(e)}'
                        }
                else:
                    # Rate Limit이 아닌 다른 오류 (인증 실패, 서버 오류 등)
                    # 재시도하지 않고 즉시 반환
                    print(f"❌ 치명적 오류 (재시도 안 함): {str(e)}")
                    return {'success': False, 'error': str(e), **pii_meta}
        
        # 모든 키 시도 실패
        return {
            'success': False, 
            'error': f'모든 API 키의 시도가 실패했습니다. 마지막 오류: {str(last_error)}',
            **pii_meta
        }

    def analyze_image_with_vision(self, image_data, mime_type, prompt):
        """
        이미지를 Vision API로 분석합니다.
        
        Args:
            image_data (str): base64로 인코딩된 이미지 데이터
            mime_type (str): 이미지 MIME 타입 (예: 'image/jpeg', 'image/png')
            prompt (str): 이미지 분석을 위한 프롬프트
            
        Returns:
            str: 분석 결과 텍스트
        """
        if not self.model:
            return 'Gemini 모델이 초기화되지 않았습니다.'
        
        if not self.api_keys:
            return '사용 가능한 API 키가 없습니다.'
        
        max_retries = len(self.api_keys)
        last_error = None

        # --- PII Redaction (Vision 프롬프트 전송 전) ---
        redaction_enabled = os.getenv("GEMINI_LLM_PII_REDACTION", "1") != "0"
        prompt_to_send = prompt or ""
        if redaction_enabled:
            prompt_to_send, _, _ = sanitize_prompt_for_llm(prompt_to_send)
        
        for attempt in range(max_retries):
            try:
                current_key = self.api_keys[self.current_key_index]
                
                # 이미지 데이터를 바이너리로 디코딩
                image_bytes = base64.b64decode(image_data)
                
                # Gemini API에 이미지와 텍스트를 함께 전달
                import PIL.Image
                import io
                
                # 이미지를 PIL Image로 변환
                image = PIL.Image.open(io.BytesIO(image_bytes))
                
                # 프롬프트와 이미지를 함께 전달
                model = self.model
                response = model.generate_content([prompt_to_send, image])
                
                # 성공 시 쿨다운 초기화
                if current_key in self.key_cooldown:
                    del self.key_cooldown[current_key]
                
                return response.text
            
            except Exception as e:
                last_error = e
                current_key = self.api_keys[self.current_key_index]
                
                # Rate Limit 오류인지 확인
                if self._is_rate_limit_error(e):
                    print(f"⚠️ Vision API Rate Limit 오류 감지 (시도 {attempt + 1}/{max_retries}): {str(e)[:100]}")
                    
                    # 실패한 키에 쿨다운 설정
                    self._mark_key_failed(current_key)
                    
                    # 다음 키로 전환
                    if self._switch_to_next_key():
                        continue
                    else:
                        return f'모든 API 키가 쿨다운 중입니다. 마지막 오류: {str(e)}'
                else:
                    # Rate Limit이 아닌 다른 오류
                    print(f"❌ Vision API 치명적 오류: {str(e)}")
                    return f'이미지 분석 중 오류가 발생했습니다: {str(e)}'
        
        # 모든 키 시도 실패
        return f'모든 API 키의 시도가 실패했습니다. 마지막 오류: {str(last_error)}'

    def generate_text(self, prompt, generation_config=None):
        """
        텍스트 생성을 위한 간단한 래퍼 메서드
        
        Args:
            prompt (str): 프롬프트
            generation_config (dict, optional): 생성 설정
            
        Returns:
            str: 생성된 텍스트
        """
        result = self.generate_response(prompt, generation_config)
        if result.get('success'):
            return result.get('content', '')
        else:
            return result.get('error', '텍스트 생성 실패')

# 클래스의 인스턴스를 생성하여 다른 파일에서 쉽게 가져다 쓸 수 있도록 합니다.
# 예: from services.gemini_service import gemini_service
gemini_service = GeminiService()