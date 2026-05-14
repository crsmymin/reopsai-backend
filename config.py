import os
from pathlib import Path
from dotenv import load_dotenv

# ✅ 수정: backend/ 폴더를 BASE_DIR로 설정
BASE_DIR = Path(__file__).resolve().parent  # backend/ 폴더

# 먼저 시스템 환경변수에서 FLASK_ENV 확인 (기본값: development)
flask_env = os.getenv('FLASK_ENV', 'development')

# backend/ 폴더 내의 환경변수 파일 경로
production_env = BASE_DIR / '.env.production'
local_env = BASE_DIR / '.env.local'
default_env = BASE_DIR / '.env'


DEFAULT_DEPLOYED_FRONTEND_ORIGINS = [
    'https://stage.reopsai.com',
    'https://main.d18rr0wdie06s6.amplifyapp.com',
]


def _normalize_origin(origin: str) -> str:
    return (origin or '').strip().rstrip('/')


def _parse_allowed_origins():
    raw = os.getenv('ALLOWED_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000')
    origins = []
    for origin in raw.split(','):
        normalized = _normalize_origin(origin)
        if normalized and normalized != '*':
            origins.append(normalized)
    frontend_url = os.getenv('FRONTEND_URL')
    if frontend_url:
        normalized_frontend_url = _normalize_origin(frontend_url)
        if normalized_frontend_url and normalized_frontend_url != '*':
            origins.append(normalized_frontend_url)
    origins.extend(DEFAULT_DEPLOYED_FRONTEND_ORIGINS)
    if os.getenv('FLASK_ENV', 'development') != 'production':
        origins.extend(['http://localhost:3000', 'http://127.0.0.1:3000'])
    return list(dict.fromkeys(origins))

# 환경에 따라 적절한 파일 로드
if flask_env == 'production':
    # 프로덕션: .env.production > .env
    if production_env.exists():
        load_dotenv(production_env)
        print(f"✅ Loaded (production): {production_env}")
    elif default_env.exists():
        load_dotenv(default_env)
        print(f"✅ Loaded (production fallback): {default_env}")
else:
    # 개발/로컬: .env.local > .env
    if local_env.exists():
        load_dotenv(local_env)
        print(f"✅ Loaded (local): {local_env}")
    elif default_env.exists():
        load_dotenv(default_env)
        print(f"✅ Loaded (development): {default_env}")
    else:
        print("⚠️ No .env file found in backend/ directory!")

class Config:
    # API Keys
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    
    # Database
    DATABASE_URL = os.getenv('DATABASE_URL')
    
    # Server Settings
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    PORT = int(os.getenv('PORT', 5001))
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
    SQLALCHEMY_ECHO = os.getenv('SQLALCHEMY_ECHO', 'False').lower() == 'true'
    
    # JWT 설정 - 30일 만료
    from datetime import timedelta
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=30)
    JWT_TOKEN_LOCATION = ['headers', 'cookies']
    JWT_ACCESS_COOKIE_NAME = os.getenv('JWT_ACCESS_COOKIE_NAME', 'access_token_cookie')
    JWT_ACCESS_COOKIE_PATH = '/'
    JWT_COOKIE_SECURE = os.getenv(
        'JWT_COOKIE_SECURE',
        'true' if flask_env == 'production' else 'False',
    ).lower() == 'true'
    JWT_COOKIE_HTTPONLY = True
    JWT_COOKIE_SAMESITE = os.getenv(
        'JWT_COOKIE_SAMESITE',
        'None' if flask_env == 'production' else 'Lax',
    )
    JWT_COOKIE_CSRF_PROTECT = os.getenv('JWT_COOKIE_CSRF_PROTECT', 'False').lower() == 'true'
    
    # Environment
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    
    # API Base URLs
    BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:5001')
    FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')
    DOMAIN = os.getenv('DOMAIN', 'localhost')
    
    # CORS 허용 출처 설정
    ALLOWED_ORIGINS = _parse_allowed_origins()
