# Frontend Guide: 팀(소속) Soft Delete API 추가 안내

## 1. 변경 요약

백엔드에 소속(팀) 삭제 기능이 추가되었습니다. 이번 삭제는 hard delete가 아니라 soft delete 방식입니다.

팀 삭제 시 실제 `teams` row, `team_members`, `team_usage_events`는 삭제하지 않고 `teams.status = "deleted"`로 변경합니다. 따라서 과거 멤버십과 사용량 데이터는 보존됩니다.

## 2. 신규 API

### `DELETE /api/admin/teams/:team_id`

Super Admin 전용 팀 삭제 API입니다.

#### 권한

- `tier = super` 사용자만 호출 가능
- 쿠키 기반 JWT 또는 Authorization Bearer JWT 인증 필요

#### Request

```http
DELETE /api/admin/teams/5
```

Request body는 필요 없습니다.

#### Response 200

```json
{
  "success": true,
  "message": "팀이 삭제 처리되었습니다.",
  "team": {
    "id": 5,
    "team_name": "리서치팀",
    "description": "사용자 리서치 전담 팀",
    "plan_code": "pro",
    "company_id": 2,
    "enterprise_name": "(주)리옵스",
    "company_name": "(주)리옵스",
    "owner_email": "owner@company.com",
    "enterprise_account_id": 10,
    "owner_id": 10,
    "member_count": 4,
    "created_at": "2026-04-24T06:08:06.104764+00:00"
  },
  "affected": {
    "members_preserved": 4,
    "usage_events_preserved": 128
  }
}
```

이미 삭제 처리된 팀에 다시 요청하면 200으로 응답하며 메시지만 달라질 수 있습니다.

```json
{
  "success": true,
  "message": "이미 삭제 처리된 팀입니다.",
  "team": {},
  "affected": {
    "members_preserved": 4,
    "usage_events_preserved": 128
  }
}
```

#### Error

| Status | 의미 |
|---|---|
| 401 | 인증 누락/만료 |
| 403 | super 권한 없음 |
| 404 | 존재하지 않는 팀 ID |
| 500 | 서버 오류 |

## 3. 팀 목록 API 변경

### `GET /api/admin/teams`

팀 목록 API에 `status` query parameter가 추가되었습니다.

| Query | 설명 |
|---|---|
| `status=active` | 기본값. 삭제되지 않은 활성 팀만 조회 |
| `status=deleted` | 삭제 처리된 팀만 조회 |
| `status=all` | 모든 팀 조회 |

기존 호출은 수정하지 않아도 됩니다.

```http
GET /api/admin/teams
```

위 요청은 아래와 동일합니다.

```http
GET /api/admin/teams?status=active
```

삭제된 팀은 기본 목록에서 제외됩니다.

## 4. 삭제 시 실제 데이터 처리

팀 삭제 시 백엔드 동작은 다음과 같습니다.

```text
DELETE /api/admin/teams/:team_id
  -> teams.status = "deleted"
  -> team_members 유지
  -> team_usage_events 유지
  -> users 계정 유지
  -> users.company_id 유지
  -> companies 유지
```

따라서 프론트에서 삭제 후 다음처럼 이해하면 됩니다.

- 계정은 삭제되지 않습니다.
- 팀 멤버십 데이터는 보존됩니다.
- 팀 사용량 데이터는 보존됩니다.
- 삭제된 팀은 기본 팀 목록에서 사라집니다.
- 삭제된 팀은 기업 사용자의 대표 팀으로 선택되지 않습니다.

## 5. 인증/서비스 영향

삭제된 팀은 일반 서비스 흐름에서 제외됩니다.

- 기업 로그인 후 대표 팀 계산에서 `status = deleted` 팀은 제외됩니다.
- `/api/b2b/team`에서 삭제된 팀은 조회되지 않습니다.
- 삭제된 팀에는 신규 멤버 추가/역할 변경/멤버 제거가 불가능합니다.
- 삭제된 팀에는 신규 사용량 이벤트가 기록되지 않습니다.
- 기존 팀 사용량 조회 API는 과거 데이터 확인을 위해 삭제된 팀도 조회할 수 있습니다.

## 6. 사용량 API 영향

### 기존 팀 사용량 API는 유지

```http
GET /api/admin/teams/:team_id/usage
```

삭제된 팀이어도 과거 사용량 확인을 위해 이 API는 계속 동작합니다.

### 회사 사용량 API도 유지

```http
GET /api/admin/companies/:company_id/usage
```

삭제 전 기록된 팀 사용량은 회사 사용량 합계에도 계속 포함됩니다.

## 7. 프론트 구현 체크리스트

### 팀 목록 페이지

- 팀 row에 삭제 버튼을 추가합니다.
- 삭제 버튼은 Super Admin 화면에서만 노출합니다.
- 삭제 요청 성공 후 `GET /api/admin/teams`를 refetch합니다.
- 기본 목록은 `status=active` 또는 query 생략으로 호출합니다.
- 삭제된 팀 관리 탭이 필요하면 `status=deleted`로 호출합니다.

### 삭제 확인 모달

권장 문구:

```text
이 팀은 삭제 처리됩니다.
팀에 속한 계정, 멤버십 기록, 사용량 데이터는 삭제되지 않고 보존됩니다.
삭제 후 해당 팀은 기본 목록과 기업 사용자 서비스에서 제외됩니다.
```

CTA 예시:

- 취소
- 삭제 처리

### 삭제 후 UI 처리

삭제 성공 시:

- 목록 refetch
- 성공 toast 표시
- 현재 페이지에 항목이 없으면 이전 페이지로 이동하거나 첫 페이지 재조회

성공 toast 예시:

```text
팀이 삭제 처리되었습니다.
```

실패 toast 예시:

```text
팀 삭제에 실패했습니다. 권한 또는 팀 상태를 확인해주세요.
```

## 8. 타입 업데이트 예시

기존 `TeamRow` 타입에 `status` 필드를 추가해야 합니다. 팀 목록과 삭제 응답의 `team` payload에 `status`가 포함됩니다.

```ts
export type TeamStatus = 'active' | 'deleted';

export type DeleteTeamResponse = {
  success: boolean;
  message: string;
  team: TeamRow;
  affected: {
    members_preserved: number;
    usage_events_preserved: number;
  };
};
```

## 9. QA 시나리오

1. Super Admin으로 팀 목록에서 팀 삭제 버튼이 보이는지 확인합니다.
2. 팀 삭제 요청 후 기본 팀 목록에서 해당 팀이 사라지는지 확인합니다.
3. `GET /api/admin/teams?status=deleted` 호출 시 삭제된 팀이 조회되는지 확인합니다.
4. 삭제된 팀의 `GET /api/admin/teams/:team_id/usage`가 200으로 유지되는지 확인합니다.
5. 삭제된 팀에 속해 있던 사용자 계정이 계정 목록에 남아있는지 확인합니다.
6. 삭제된 팀 소속 기업 계정으로 로그인했을 때 삭제된 팀이 대표 팀으로 잡히지 않는지 확인합니다.
7. 삭제된 팀에 대해 멤버 추가/역할 변경 기능이 실패하거나 UI에서 접근 불가한지 확인합니다.

## 10. 프론트 전달용 요약 프롬프트

아래 내용을 프론트 작업 요청에 그대로 전달할 수 있습니다.

```text
백엔드에 팀(소속) soft delete API가 추가되었습니다.

신규 API:
DELETE /api/admin/teams/:team_id
- super 권한 전용
- body 없음
- 실제 row 삭제가 아니라 teams.status = "deleted" 처리
- team_members, team_usage_events, users, companies 데이터는 보존
- 성공 응답에는 affected.members_preserved, affected.usage_events_preserved 포함

팀 목록 API 변경:
GET /api/admin/teams?status=active|deleted|all
- status 생략 시 active 기본값
- 삭제된 팀은 기본 목록에서 제외됨
- 삭제된 팀 목록이 필요하면 status=deleted 사용

프론트 작업:
1. Super Admin 팀 목록 row에 삭제 버튼 추가
2. 삭제 전 확인 모달 표시
3. 삭제 성공 후 팀 목록 refetch
4. 기본 목록은 status=active 또는 query 생략으로 유지
5. 삭제된 팀 관리 탭을 만들 경우 status=deleted 호출
6. 팀 삭제는 계정/멤버십/사용량을 지우지 않는 soft delete임을 안내 문구에 반영
7. 회사/팀 사용량 화면에서는 삭제된 팀의 과거 사용량도 조회 가능하므로 기존 usage API 호출 유지
```
