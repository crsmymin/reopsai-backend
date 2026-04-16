# DB Migration Plan (Supabase -> PostgreSQL + SQLAlchemy/Alembic)

## Scope Decisions (Locked)
- Supabase: full removal (DB/Auth/Storage all removed)
- DB access standard: SQLAlchemy + Alembic
- Runtime/deploy: Docker Compose (no systemd process management for backend)

## Step 1 - Dependency Audit (Completed)

### 1) Supabase usage footprint
- `supabase.table(...)` calls: 139
- Supabase auth usage: 1 (`supabase.auth.sign_in_with_password`)
- Supabase storage usage: not found in backend code
- Supabase service init/access usage spread across:
  - `backend/app.py` (largest concentration)
  - `backend/routes/admin.py`
  - `backend/routes/auth.py`
  - `backend/routes/b2b.py`
  - `backend/routes/demo.py`
  - `backend/routes/artifact_ai.py`
  - `backend/routes/study.py`
  - `backend/routes/screener.py`
  - `backend/routes/dev_evaluator.py`
  - `backend/utils/b2b_access.py`

### 2) Primary DB tables currently used
- `artifacts` (39)
- `studies` (25)
- `projects` (24)
- `users` (23)
- `team_members` (11)
- `teams` (8)
- `user_feedback` (3)
- `study_schedules` (2)
- `artifact_edit_history` (2)

Numbers above are occurrence counts of `table('...')` usage in backend files.

### 3) Hotspot files to migrate first
- `backend/app.py` (84 references)
- `backend/routes/admin.py` (29 references)
- `backend/routes/auth.py` (21 references)
- `backend/routes/b2b.py` (18 references)

### 4) Risk notes found during audit
- Environment files currently include Supabase variables and must be replaced by DB/JWT/app secrets strategy for Docker and production.
- Auth logic currently mixes local JWT issuance with a Supabase auth call path; this must be normalized in migration step.

## Step 2 - SQLAlchemy/Alembic Foundation (Completed)

### Goal
Create new DB foundation without changing business behavior yet.

### Planned outputs
- `backend/db/engine.py` (engine/session factory)
- `backend/db/base.py` (declarative base)
- `backend/db/models/*` (initial core models)
- `backend/alembic.ini`, `backend/alembic/` (migration framework)
- Initial migration for core tables:
  - users, projects, studies, artifacts
  - teams, team_members
  - study_schedules, user_feedback, artifact_edit_history

### Validation for Step 2
- `alembic upgrade head` succeeds against local Postgres in Docker
- Core tables created with expected PK/FK/indices
- App can boot with SQLAlchemy engine configured (even before repository migration)

## Step 3 - Workspace/Study/Artifact Core CRUD Migration (Completed)
- SQLAlchemy 우선 경로 + Supabase fallback 형태로 핵심 API read/write를 이전했습니다.

## Step 4 - Auth Path Migration (Completed)
- `routes/auth.py`의 Supabase 인증/사용자 조회 로직을 SQLAlchemy 기반으로 이전했습니다.
- JWT 발급 경로를 유지하고, Google verify 경로도 SQLAlchemy 사용자 저장/조회로 전환했습니다.
- 런타임 안정성을 위해 SQLAlchemy session `expire_on_commit=False` 설정을 적용했습니다.

## Step 5 - Remove `services/supabase_service.py` Usage (Completed)
- Completed in this step:
  - `backend/routes/study.py`
  - `backend/routes/dev_evaluator.py`
  - `backend/utils/b2b_access.py`
  - `backend/routes/b2b.py`
  - `backend/routes/admin.py`
  - `backend/routes/demo.py`
  - `backend/routes/artifact_ai.py`
  - `backend/routes/screener.py`
  - `backend/app.py` Supabase 의존 제거 완료
    - Supabase 초기화 제거
    - `fetch_project_keywords` SQLAlchemy-only 전환
    - `generator_create_plan_oneshot` SQLAlchemy-only 전환
    - `conversation_maker_finalize_oneshot` SQLAlchemy-only 전환
    - survey/guideline 생성 경로 SQLAlchemy-only 전환
    - workspace/study/artifact 주요 CRUD 및 stream 경로 SQLAlchemy-only 전환
- Remaining hotspots:
  - `backend/routes/auth.py` 하위호환 분기 제거
  - `backend/services/supabase_service.py` 제거
  - `backend/requirements.txt` Supabase 패키지 제거

## Remaining Steps (Execution Order)
1. Step 6: Compose 구성 추가 (completed)
2. Run Alembic `upgrade head` inside local Postgres container (completed by container startup command)
3. Production compose + nginx proxy alignment for EC2 (completed)

## Step 6 - Docker Compose (Completed)
- Added `backend/Dockerfile`
- Added `backend/docker-compose.local.yml` (backend + postgres + healthcheck)
- Added `backend/docker-compose.prod.yml` (backend only, RDS `DATABASE_URL` 전제)
- Local dev hot-reload enabled (`./:/app` bind mount + `uvicorn --reload`)

## Step 7 - Production Deploy Alignment (Completed)
- Added `backend/.env.prod.example` (EC2/RDS production env template)
- Added `backend/DEPLOY_EC2_COMPOSE.md` (compose + nginx deployment/operation guide)
