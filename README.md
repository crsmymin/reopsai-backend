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
├── app.py                    # 호환 Flask 진입점 (app = create_app())
├── reopsai/                  # 레이어드 아키텍처 패키지
│   ├── api/                  # Flask app factory 및 Blueprint 등록
│   ├── application/          # use case/service 계층
│   ├── domain/               # 프레임워크 독립 도메인 타입/정책
│   ├── infrastructure/       # adapter/export 호환 계층
│   └── shared/               # auth/security/http 등 공통 기능
├── config.py                 # 환경변수/설정 관리
├── requirements.txt          # Python 패키지 의존성
├── docker-compose.yml        # Docker 개발 환경
│
├── db/                      # 데이터베이스 계층
│   ├── engine.py            # SQLAlchemy 엔진 설정
│   ├── models/core.py        # ORM 모델 (User, Project, Study 등)
│   ├── repositories/        # SQLAlchemy Repository 계층
│   └── migrations/          # Alembic 마이그레이션
│
├── services/                # 기존 외부 서비스 singleton (호환 유지)
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

### 아키텍처 전환 원칙

현재 백엔드는 controller, application service, infrastructure adapter, shared helper를
`reopsai/*` 패키지 아래에 둔 레이어드 구조를 사용합니다.

- `reopsai.api.app_factory.create_app()`이 Flask 앱 생성, JWT/CORS, 보안 헤더, 요청 guard, Blueprint 등록을 담당합니다.
- `app.py`와 `asgi.py`는 기존 배포 계약을 깨지 않기 위한 호환 진입점이며 `app = create_app()` 계약을 유지합니다.
- `reopsai.api/*`는 요청 파싱, JWT/context 조회, 응답 JSON/status mapping에 집중하는 controller 역할을 맡습니다.
- `reopsai.application/*_service.py`는 workspace, auth, b2b, admin, plan/survey/guideline, artifact AI, screener 등 use case orchestration을 담당합니다.
- `db/repositories/*_repository.py`는 SQLAlchemy query/update/delete와 persistence payload 조립을 담당합니다.
- 새 코드에서는 `reopsai.shared.auth.tier_required`를 사용합니다.
- DB/LLM/RAG 접근은 service 생성 시 adapter/repository를 주입할 수 있는 구조로 전환하고, 기존 `services/*` singleton export는 호환을 위해 유지합니다.
- 기능 보존을 위해 public URL, 응답 JSON shape, JWT claim, cookie 동작, Alembic schema는 변경하지 않습니다.

### 현재 분리된 주요 레이어

| 영역 | Controller | Service | Repository |
|------|------------|---------|------------|
| Workspace CRUD/AI | `reopsai/api/workspace.py` | `workspace_service.py`, `workspace_ai_service.py` | `workspace_repository.py` |
| Auth | `reopsai/api/auth.py` | `auth_service.py` | `auth_repository.py` |
| B2B | `reopsai/api/b2b.py` | `b2b_service.py` | `b2b_repository.py` |
| Admin | `reopsai/api/admin.py` | `admin_service.py`, `admin_usage_service.py`, `admin_backoffice_service.py` | `admin_repository.py`, `admin_usage_repository.py`, `admin_backoffice_repository.py` |
| AI 생성 | `reopsai/api/plan.py`, `reopsai/api/survey.py`, `reopsai/api/guideline.py` | `plan_service.py`, `plan_generation_service.py`, `survey_service.py`, `guideline_service.py` | `plan_repository.py`, `survey_repository.py`, `guideline_repository.py` |
| Artifact AI | `reopsai/api/artifact_ai.py` | `artifact_ai_service.py` | `artifact_ai_repository.py` |
| 기타 API | `reopsai/api/screener.py`, `reopsai/api/study.py`, `reopsai/api/demo.py`, `reopsai/api/dev_evaluator.py`, `reopsai/api/generator.py` | 각 `*_service.py` | 필요 시 각 `*_repository.py` |

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
| plan | `/api/study-helper`, `/api/generator`, `/api/conversation`, `/api/debug` | 리서치 계획서 AI 생성 |
| survey | `/api/survey-diagnoser`, `/api/survey` | 설문지 AI 생성 |
| guideline | `/api/guideline`, `/api/extract-methodologies` | 가이드라인 AI 생성 |
| artifact_ai | `/api/artifacts` | AI 아티팩트 편집 |
| study | `/api/studies`, `/api/projects` | slug 기반 스터디/프로젝트 조회 |
| b2b | `/api/b2b` | B2B 멤버십/팀 관리 |
| admin | `/api/admin`, `/api/feedback` | 관리자/피드백 관리 |

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
```

### 2. 로컬 개발 (직접 실행)

```bash
# 3. Docker로 실행
docker-compose up -d --build

# 4. 확인
curl http://localhost:5001/health
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
