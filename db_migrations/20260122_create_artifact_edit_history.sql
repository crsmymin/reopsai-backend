-- Artifact edit history (version snapshot) table
-- 목적:
-- - 아티팩트 수정 히스토리를 "적용 전/후 마크다운 스냅샷"으로 저장
-- - 프론트에서 히스토리 모달(비교 전용)로 조회 가능
-- - Undo/Redo 같은 패치/머지 로직 없이도 안정적인 비교/복원이 가능
--
-- (권장) pgcrypto extension이 필요할 수 있음 (gen_random_uuid)

-- Enable uuid generator (if not already enabled)
create extension if not exists pgcrypto;

create table if not exists public.artifact_edit_history (
  id uuid primary key default gen_random_uuid(),

  artifact_id bigint not null,
  -- users.id가 integer라 타입 맞춤 (audit 보존 위해 nullable)
  user_id integer,

  prompt text,
  source text, -- e.g. 'inline_improve' | 'diff_modal' | 'section_ai'

  before_markdown text not null,
  after_markdown text not null,

  selection_from integer,
  selection_to integer,

  -- selection_from/to가 둘 다 있을 때만 유효성 검사
  constraint chk_artifact_edit_history_selection_span
    check (
      (selection_from is null and selection_to is null)
      or (selection_from is not null and selection_to is not null and selection_from < selection_to)
    ),

  created_at timestamptz not null default now()
);

-- FK: artifacts.id = bigint identity
alter table public.artifact_edit_history
  add constraint artifact_edit_history_artifact_id_fkey
  foreign key (artifact_id) references public.artifacts(id)
  on delete cascade;

-- FK: users.id = integer
-- 유저가 삭제되더라도 히스토리는 남길 수 있게 set null
alter table public.artifact_edit_history
  add constraint artifact_edit_history_user_id_fkey
  foreign key (user_id) references public.users(id)
  on delete set null;

-- Helpful indexes
create index if not exists artifact_edit_history_artifact_id_created_at_idx
  on public.artifact_edit_history (artifact_id, created_at desc);

create index if not exists artifact_edit_history_user_id_created_at_idx
  on public.artifact_edit_history (user_id, created_at desc);

