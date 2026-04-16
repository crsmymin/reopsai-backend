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
    
    # Supabase 설정
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY')
    SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    
    # Server Settings
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    PORT = int(os.getenv('PORT', 5001))
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
    DATABASE_URL = os.getenv('DATABASE_URL')
    SQLALCHEMY_ECHO = os.getenv('SQLALCHEMY_ECHO', 'False').lower() == 'true'
    
    # JWT 설정 - 30일 만료
    from datetime import timedelta
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=30)
    
    # Environment
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    
    # API Base URLs
    BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:5001')
    FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')
    DOMAIN = os.getenv('DOMAIN', 'localhost')
    
    # CORS 허용 출처 설정
    ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000').split(',')
