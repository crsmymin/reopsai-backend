# ReOpsAI Backend

UX 리서치 자동화를 위한 AI 기반 백엔드 시스템입니다. 리서치 계획서, 설문지, 가이드라인 생성 및 참여자 선별 기능을 제공합니다.

## 기술 스택

- **Framework:** Flask 3.1.2
- **Database:** PostgreSQL + SQLAlchemy 2.0 (ORM)
- **Vector Database:** ChromaDB (RAG 시스템)
- **AI Models:** OpenAI GPT / Google Gemini
- **인증:** JWT (PyJWT) + Google OAuth
- **마이그레이션:** Alembic

## 프로젝트 구조

```
reopsai-backend/
├── app.py                    # 메인 Flask 앱 (Blueprint 등록)
├── config.py                 # 환경변수/설정 관리
├── requirements.txt          # Python 패키지 의존성
├── docker-compose.yml        # Docker 개발 환경
│
├── routes/                   # API Blueprints
│   ├── auth.py              # 인증/권한 (JWT, Google OAuth)
│   ├── workspace.py         # 프로젝트/스터디/아티팩트 CRUD
│   ├── screener.py          # 참여자 선별 (CSV 프로파일링)
│   ├── plan_routes.py       # 리서치 계획서 AI 생성
│   ├── survey_routes.py     # 설문지 AI 생성/관리
│   ├── guideline_routes.py  # 가이드라인 AI 생성
│   ├── study.py             # 스터디 관리
│   ├── generator.py         # 문서 생성 엔진
│   ├── artifact_ai.py       # AI 아티팩트 편집
│   ├── ai_persona.py        # AI 페르소나 시뮬레이션
│   ├── b2b.py               # B2B 기능
│   └── admin.py             # 관리자 기능
│
├── db/                      # 데이터베이스 계층
│   ├── engine.py            # SQLAlchemy 엔진 설정
│   ├── models/core.py        # ORM 모델 (User, Project, Study 등)
│   ├── repositories/        # Repository 패턴
│   └── migrations/          # Alembic 마이그레이션
│
├── services/                # 비즈니스 서비스
│   ├── vector_service.py    # ChromaDB VectorDB (싱글턴)
│   ├── openai_service.py    # OpenAI API
│   └── gemini_service.py     # Google Gemini API
│
├── screener/                # 참여자 선별 비즈니스 로직
│   ├── csv_profiler.py      # CSV 컬럼 분석
│   ├── filters.py           # 성실도 필터
│   ├── scoring.py           # 참여자 스코어링
│   ├── schedule_logic.py    # 일정 로직
│   └── participant_logic.py # 참여자 선택 로직
│
├── rag_system/              # RAG (Retrieval Augmented Generation)
│   └── improved/
│       ├── improved_vector_db_service.py
│       └── improved_rag_database_builder.py
│
├── prompts/                 # LLM 프롬프트
│   └── analysis_prompts.py  # 통합 프롬프트 클래스
│
├── utils/                   # 공통 유틸리티
│   ├── idempotency.py       # 멱등성 처리
│   ├── keyword_utils.py     # 키워드 추출
│   └── llm_utils.py         # LLM 응답 파싱
│
├── data/                    # 데이터 파일
├── uploads/                 # 사용자 업로드 파일
└── chroma_db/               # ChromaDB 저장소
```

## 데이터베이스 모델

**PostgreSQL (SQLAlchemy ORM)**

| 엔티티 | 설명 |
|--------|------|
| `users` | 사용자 (email, google_id, tier) |
| `projects` | 프로젝트 (owner_id, name, keywords) |
| `studies` | 스터디 (project_id, methodologies, timeline) |
| `artifacts` | 생성물 (study_id, type, content) |
| `teams` / `team_members` | 팀 기능 |
| `study_schedules` | 스터디 일정 |
| `user_feedback` | 사용자 피드백 |
| `artifact_edit_history` | AI 편집 이력 |

## API 엔드포인트

| Blueprint | URL Prefix | 설명 |
|-----------|------------|------|
| auth | `/api/auth` | 인증 (로그인, 회원가입, JWT) |
| workspace | `/api` | 프로젝트/스터디/아티팩트 관리 |
| screener | `/api/screener` | 참여자 CSV 업로드 및 선별 |
| plan | `/api/plans` | 리서치 계획서 AI 생성 |
| survey | `/api/surveys` | 설문지 AI 생성 |
| guideline | `/api/guidelines` | 가이드라인 AI 생성 |
| study | `/api/studies` | 스터디 관리 |

## 주요 기능

### 1. Workspace (프로젝트 관리)
- 프로젝트 CRUD (생성, 조회, 수정, 삭제)
- 스터디 CRUD
- 아티팩트 관리 (생성된 문서)
- URL 메타데이터 자동 크롤링
- 키워드 자동 추출

### 2. Screener (참여자 선별)
4단계 프로세스:
1. **계획서 분석** - AI로 선별 기준 추출
2. **CSV 프로파일링** - 컬럼명 정규화 및 스키마 분석
3. **스코어링** - 참여자별 평가 및 점수 계산
4. **최종 선택** - AI 기반 최종 후보자 선정

### 3. Plan Generator (리서치 계획서)
- RAG 기반 맞춤형 계획서 생성
- URL 컨텐츠 분석
- 키워드 기반 문맥 검색

### 4. Survey Builder (설문지)
- 목적 기반 설문지 AI 생성
- 문항 타입 자동 분류
- 가이드라인 연동

### 5. Guideline Generator (가이드라인)
- 인터뷰/설문 가이드 자동 생성
- 스터디 타입별 맞춤 템플릿

### 6. AI Persona
- AI 기반 페르소나 시뮬레이션
- 페르소나 챗봇 대화

## 개발 환경 설정

### 사전 요건
- Python 3.11+
- PostgreSQL (또는 Docker)
- OpenAI API Key
- Google API Key (선택)

### 1. 로컬 개발 (Docker 권장)

```bash
# 1. 저장소 클론
git clone <repository-url>
cd reopsai-backend

# 2. 환경변수 설정
# .env.local 파일을 backend/ 디렉토리에 생성
echo "JWT_SECRET_KEY=your-secret-key
OPENAI_API_KEY=your-openai-key
GOOGLE_API_KEYS=your-google-key
GOOGLE_CLIENT_ID=your-google-client-id
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/smart_research
ENVIRONMENT=development
FLASK_ENV=development
DEBUG=true
PORT=5001
BACKEND_URL=http://127.0.0.1:5001
FRONTEND_URL=http://localhost:3000
ALLOWED_ORIGINS=http://localhost:3000" > .env.local

# 3. Docker로 실행
docker-compose up -d

# 4. 확인
curl http://localhost:5001/health
```

### 2. 로컬 개발 (직접 실행)

```bash
# 1. 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. PostgreSQL 실행 (또는 Docker)
docker run -d --name postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=smart_research -p 5432:5432 postgres:16-alpine

# 4. 마이그레이션
alembic upgrade head

# 5. Flask 앱 실행
python -m flask --app app:app run --host 0.0.0.0 --port 5001 --debug --reload
```

### 3. 환경변수 설정

| 변수 | 설명 | 예시 |
|------|------|------|
| `JWT_SECRET_KEY` | JWT 서명용 비밀키 | 랜덤 문자열 |
| `OPENAI_API_KEY` | OpenAI API 키 | sk-... |
| `GOOGLE_API_KEYS` | Google Gemini API 키 | AIzaSy... |
| `GOOGLE_CLIENT_ID` | Google OAuth 클라이언트 ID | ...apps.googleusercontent.com |
| `DATABASE_URL` | PostgreSQL 연결 문자열 | postgresql+psycopg2://... |
| `FLASK_ENV` | 환경 (development/production) | development |
| `ENVIRONMENT` | 배포 환경 | development |
| `FRONTEND_URL` | 프론트엔드 URL | http://localhost:3000 |
| `BACKEND_URL` | 백엔드 URL | http://localhost:5001 |
| `ALLOWED_ORIGINS` | CORS 허용 오리진 (콤마 구분) | http://localhost:3000 |

## 마이그레이션

```bash
# 최신 버전으로 업그레이드
alembic upgrade head

# 특정 버전으로 이동
alembic upgrade <revision>

# 다운그레이드
alembic downgrade -1

# 마이그레이션 스크립트 생성
alembic revision --autogenerate -m "description"
```

## 보안 설정

- **JWT 인증:** 30일 만료, PyJWT 라이브러리 사용
- **CORS:** 환경변수 기반 출처 제한
- **보안 헤더:**
  - HSTS (max-age=31536000)
  - X-Frame-Options: DENY
  - X-Content-Type-Options: nosniff
  - X-XSS-Protection: 1; mode=block
  - Referrer-Policy: strict-origin-when-cross-origin

## 로깅 및 모니터링

- `api_logger.py` - API 호출/에러 로깅
- `debug_utils.py` - 요청 추적 및 성능 모니터링
- `telemetry.py` - 메트릭 수집

## 프로덕션 배포

```bash
# 환경 설정
export FLASK_ENV=production

# Docker Compose (Production)
docker-compose -f docker-compose.prod.yml up -d

# 또는 Gunicorn
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5001 app:app
```

## 라이선스

Private - All rights reserved.

## 지원

문제 발생 시 개발팀에 문의해주세요.
