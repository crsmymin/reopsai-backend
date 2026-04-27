# Frontend Guide: Companies 정규화 및 기업/팀 사용량 집계 변경사항

## 1. 변경 요약

백엔드에서 기업 정보를 `users.company_name` 문자열 중심으로 관리하던 구조가 `companies` 테이블 중심의 관계형 구조로 변경되었습니다.

기존 호환을 위해 `company_name` 응답 필드는 계속 유지되지만, 이제 기준 데이터는 `companies.name`입니다. 프론트에서는 가능한 경우 `company_id`를 함께 보관하고, 회사 단위 기능이나 사용량 조회에는 `company_id`를 사용해야 합니다.

변경된 관계는 다음과 같습니다.

```text
companies
  ├─ users.company_id
  ├─ teams.company_id
  └─ team_usage_events.company_id

teams
  ├─ team_members.team_id
  └─ team_usage_events.team_id
```

## 2. 프론트에서 체크해야 할 핵심 포인트

### 2.1 `company_id` 필드 추가 대응

아래 응답들에 `company_id`가 추가되었거나 포함될 수 있습니다.

- `GET /api/profile`
- `POST /api/auth/enterprise/login`
- `POST /api/auth/enterprise/change-password`
- `GET /api/admin/enterprise/accounts`
- `POST /api/admin/enterprise/accounts`
- `PUT /api/admin/enterprise/accounts/:id`
- `GET /api/admin/teams`
- `POST /api/admin/teams`
- `GET /api/admin/teams/:id/usage`
- `GET /api/b2b/team`

프론트 표시용 회사명은 기존처럼 `company_name`을 사용하면 됩니다. 단, 회사 단위 API 호출에는 `company_id`를 사용해야 합니다.

### 2.2 `company_name`은 유지되지만 기준은 `companies.name`

기존 프론트 코드가 `company_name`만 표시하던 부분은 계속 동작합니다.

다만 백엔드 내부 기준은 다음과 같이 바뀌었습니다.

- 우선순위 1: `companies.name`
- fallback: 기존 `users.company_name`

따라서 프론트에서는 회사명 표시 시 `company_name`을 그대로 사용하되, 데이터 식별이나 API path에는 `company_id`를 사용해야 합니다.

### 2.3 계정 수정 시 회사명 변경 영향

`PUT /api/admin/enterprise/accounts/:id`에서 `company_name`을 수정하면 해당 사용자의 `company_id`에 연결된 회사명이 변경됩니다.

주의사항:

- 기업 오너 계정의 회사명을 변경하면 연결된 회사명 기준 응답도 함께 바뀔 수 있습니다.
- 같은 `company_id`를 공유하는 사용자/팀은 동일한 회사명을 표시하게 됩니다.
- 프론트에서 “사용자별 임의 회사명”처럼 취급하면 안 됩니다.

### 2.4 팀 플랜은 여전히 팀 기준

이번 변경으로 회사 테이블이 추가되었지만 `plan_code`는 기존대로 팀 기준입니다.

- 기업/팀 플랜 변경: `PUT /api/admin/teams/:id/plan`
- Google SSO 개인 계정 플랜 변경: `PUT /api/admin/enterprise/accounts/:id`의 `plan_code`
- 기업형 계정의 플랜은 계정 수정 모달에서 변경 불가

프론트에서 회사 단위 플랜 변경 UI를 만들면 안 됩니다. 현재 정책상 회사는 여러 팀을 가질 수 있고, 각 팀의 `plan_code`가 서비스 플랜 기준입니다.

## 3. API별 변경사항

### 3.1 `GET /api/profile`

기업 계정의 프로필 응답에 `company_id`, `company_name`이 포함됩니다.

예시:

```json
{
  "success": true,
  "user": {
    "id": 10,
    "email": "owner@company.com",
    "name": "홍길동",
    "company_id": 2,
    "company_name": "(주)리옵스",
    "tier": "enterprise",
    "account_type": "enterprise",
    "team_id": 5,
    "plan_code": "pro",
    "password_reset_required": false
  }
}
```

프론트 체크:

- 세션 복구 시 `company_id`가 null일 수 있음을 허용해야 합니다.
- 일반 Google SSO 계정은 `company_id`가 없거나 null일 수 있습니다.
- 기업 계정 화면에서는 `company_name` 표시를 우선 사용합니다.

### 3.2 `GET /api/admin/enterprise/accounts`

계정 목록 응답에 `company_id`가 추가됩니다.

예시:

```json
{
  "accounts": [
    {
      "id": 10,
      "email": "owner@company.com",
      "name": "홍길동",
      "company_id": 2,
      "company_name": "(주)리옵스",
      "plan_code": "pro",
      "account_type": "enterprise",
      "auth_type": "enterprise",
      "team_role": "owner",
      "is_owner": true,
      "team_id": 5,
      "team_name": "기본 팀",
      "created_at": "2026-04-24T06:08:06.104764+00:00"
    }
  ],
  "total_count": 1,
  "total_pages": 1,
  "current_page": 1
}
```

프론트 체크:

- 기업명 컬럼은 계속 `company_name`을 표시합니다.
- 회사 단위 상세/사용량 이동 링크를 만들 경우 `company_id`를 사용합니다.
- `company_id`가 null인 개인 계정도 목록에 포함될 수 있습니다.

### 3.3 `POST /api/admin/enterprise/accounts`

기업 계정 생성 시 기존 request body는 유지됩니다.

```json
{
  "email": "owner@company.com",
  "name": "홍길동",
  "company_name": "(주)리옵스",
  "plan_code": "pro",
  "team_name": "기본 팀",
  "team_description": "기본 팀 설명"
}
```

백엔드 처리 변경:

- `company_name`으로 `companies` 레코드를 생성하거나 기존 회사를 재사용합니다.
- 생성된 오너 사용자와 기본 팀에 동일한 `company_id`를 연결합니다.
- 초기 비밀번호는 기존 정책대로 `0000`입니다.

응답에 `company_id`가 포함됩니다.

```json
{
  "success": true,
  "account": {
    "id": 10,
    "email": "owner@company.com",
    "name": "홍길동",
    "company_id": 2,
    "company_name": "(주)리옵스",
    "account_type": "enterprise",
    "tier": "enterprise",
    "plan_code": "pro",
    "password_reset_required": true
  },
  "team": {
    "id": 5,
    "team_name": "기본 팀",
    "description": "기본 팀 설명",
    "plan_code": "pro",
    "company_id": 2,
    "owner_id": 10
  }
}
```

### 3.4 `PUT /api/admin/enterprise/accounts/:id`

기존 수정 정책은 유지됩니다.

- 모든 계정: `name`, `company_name` 수정 가능
- Google SSO 계정만: `plan_code` 수정 가능
- 기업형 계정: 이 API에서 `plan_code` 변경 불가

추가로 회사 정규화가 적용됩니다.

```json
{
  "name": "홍길동",
  "company_name": "(주)리옵스 엔터프라이즈"
}
```

프론트 체크:

- 기업형 계정의 플랜 변경 UI는 이 모달에서 계속 비활성화해야 합니다.
- 회사명 변경 후에는 목록을 refetch하는 것이 안전합니다.
- 같은 회사에 연결된 다른 사용자/팀의 `company_name` 표시가 함께 바뀔 수 있습니다.

### 3.5 `GET /api/admin/teams`

팀 목록 응답에 `company_id`가 포함됩니다.

```json
{
  "teams": [
    {
      "id": 5,
      "team_name": "기본 팀",
      "description": "기본 팀 설명",
      "plan_code": "pro",
      "company_id": 2,
      "enterprise_name": "(주)리옵스",
      "company_name": "(주)리옵스",
      "owner_email": "owner@company.com",
      "enterprise_account_id": 10,
      "owner_id": 10,
      "member_count": 3,
      "created_at": "2026-04-24T06:08:06.104764+00:00"
    }
  ],
  "total_count": 1,
  "total_pages": 1,
  "current_page": 1
}
```

프론트 체크:

- 기존 `company_name` 또는 `enterprise_name` 표시 코드는 계속 사용 가능합니다.
- 회사 단위 페이지/사용량 이동에는 `company_id`를 사용합니다.

### 3.6 `POST /api/admin/teams`

request body는 유지됩니다.

```json
{
  "enterprise_account_id": 10,
  "team_name": "리서치팀",
  "description": "리서치 전담 팀",
  "plan_code": "pro"
}
```

백엔드 처리 변경:

- 팀은 오너 계정의 `company_id`를 상속합니다.
- 오너에게 `company_id`가 없고 기존 `company_name`만 있으면 백엔드가 회사 레코드를 생성/연결합니다.

응답에는 `company_id`가 포함됩니다.

### 3.7 `GET /api/admin/teams/:team_id/usage`

기존 팀 사용량 API는 유지됩니다. 응답의 `team` 객체에 `company_id`, `company_name`이 추가됩니다.

```json
{
  "success": true,
  "team": {
    "id": 5,
    "name": "기본 팀",
    "company_id": 2,
    "company_name": "(주)리옵스",
    "plan_code": "pro"
  },
  "window": {
    "start_at": null,
    "end_at": null
  },
  "totals": {
    "request_count": 10,
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "total_tokens": 1500
  },
  "by_feature": [],
  "by_user": []
}
```

### 3.8 신규 API: `GET /api/admin/companies/:company_id/usage`

회사 전체 사용량을 조회하는 신규 API입니다.

권한:

- `tier = super` 전용

Query parameters:

| 이름 | 필수 | 설명 |
|---|---|---|
| `start_at` | 선택 | ISO datetime 시작일 |
| `end_at` | 선택 | ISO datetime 종료일 |

예시 요청:

```http
GET /api/admin/companies/2/usage?start_at=2026-04-01T00:00:00&end_at=2026-04-30T23:59:59
```

응답 예시:

```json
{
  "success": true,
  "company": {
    "id": 2,
    "name": "(주)리옵스",
    "status": "active"
  },
  "window": {
    "start_at": "2026-04-01T00:00:00",
    "end_at": "2026-04-30T23:59:59"
  },
  "totals": {
    "request_count": 100,
    "prompt_tokens": 12000,
    "completion_tokens": 8000,
    "total_tokens": 20000
  },
  "by_feature": [
    {
      "feature_key": "plan_generation",
      "request_count": 40,
      "total_tokens": 9000
    }
  ],
  "by_team": [
    {
      "team_id": 5,
      "request_count": 60,
      "total_tokens": 12000
    }
  ],
  "by_user": [
    {
      "user_id": 10,
      "request_count": 30,
      "total_tokens": 7000
    }
  ]
}
```

프론트 활용:

- 기업 계정 목록 또는 팀 목록에서 `company_id`가 있는 항목에 회사 사용량 보기 버튼을 연결할 수 있습니다.
- 회사 총 사용량 대시보드에서는 `totals`를 메인 수치로 사용합니다.
- 팀별 breakdown은 `by_team`, 사용자별 breakdown은 `by_user`를 사용합니다.

에러:

| Status | 의미 |
|---|---|
| 401 | 인증 누락/만료 |
| 403 | super 권한 없음 |
| 404 | 존재하지 않는 company_id |
| 400 | 날짜 형식 오류 |

### 3.9 `GET /api/b2b/team`

기업 사용자의 내 팀 조회 응답에 `company_id`, `company_name`이 추가됩니다.

```json
{
  "success": true,
  "team": {
    "id": 5,
    "name": "기본 팀",
    "description": "기본 팀 설명",
    "status": "active",
    "company_id": 2,
    "company_name": "(주)리옵스",
    "plan_code": "pro",
    "owner_id": 10,
    "created_at": "2026-04-24T06:08:06.104764+00:00"
  },
  "members": []
}
```

## 4. 화면별 프론트 체크리스트

### 4.1 Super Admin 계정 리스트

체크할 사항:

- `company_id`를 row 데이터에 보관합니다.
- 기업명 컬럼은 `company_name` 표시를 유지합니다.
- 회사 사용량 화면으로 이동하는 액션을 추가한다면 `company_id`가 있는 row에서만 활성화합니다.
- `company_id`가 null이면 회사 사용량 버튼은 비활성화합니다.
- 계정 수정 후에는 계정 목록을 refetch합니다.

### 4.2 Super Admin 팀 리스트

체크할 사항:

- `company_id`를 row 데이터에 보관합니다.
- 기업명 표시는 `company_name` 또는 기존 호환 필드 `enterprise_name`을 사용할 수 있습니다.
- 팀 사용량은 기존 `team_id` 기반 API를 사용합니다.
- 기업 전체 사용량은 신규 `company_id` 기반 API를 사용합니다.

### 4.3 기업 계정 생성 모달

체크할 사항:

- request body는 기존과 동일하게 `company_name`을 보냅니다.
- 응답의 `account.company_id`, `team.company_id`를 받을 수 있도록 타입을 업데이트합니다.
- 생성 성공 후 계정 목록/팀 목록을 refetch합니다.

### 4.4 계정 수정 모달

체크할 사항:

- 수정 가능 필드는 기존처럼 `name`, `company_name` 중심입니다.
- 기업형 계정의 `plan_code` 변경은 여전히 비활성화합니다.
- Google SSO 계정의 플랜 변경만 허용합니다.
- 회사명 수정은 회사 단위 명칭 변경으로 반영될 수 있음을 UI 문구에 반영하는 것이 좋습니다.

권장 문구 예시:

```text
기업명은 같은 회사에 연결된 사용자와 팀에 공통으로 표시됩니다.
```

### 4.5 사용량 화면

체크할 사항:

- 팀 사용량: `GET /api/admin/teams/:team_id/usage`
- 회사 전체 사용량: `GET /api/admin/companies/:company_id/usage`
- 기간 필터는 두 API 모두 `start_at`, `end_at` ISO datetime 형식을 사용합니다.
- 회사 사용량 API의 `by_team`, `by_user`에는 이름이 포함되지 않고 ID만 포함됩니다. 이름 표시가 필요하면 기존 목록 데이터와 매핑하거나 후속 API 개선이 필요합니다.

## 5. 타입 정의 업데이트 예시

프론트 타입에 아래 필드를 추가하는 것을 권장합니다.

```ts
export type AccountRow = {
  id: number;
  email: string;
  name: string | null;
  company_id: number | null;
  company_name: string | null;
  plan_code: string;
  account_type: 'enterprise' | 'individual';
  auth_type: 'enterprise' | 'google' | 'individual';
  team_role: 'owner' | 'member' | null;
  is_owner: boolean;
  team_id: number | null;
  team_name: string | null;
  created_at: string;
};

export type TeamRow = {
  id: number;
  team_name: string;
  description: string | null;
  plan_code: 'starter' | 'pro' | 'enterprise_plus';
  company_id: number | null;
  company_name: string | null;
  enterprise_name: string | null;
  owner_email: string | null;
  enterprise_account_id: number | null;
  owner_id: number | null;
  member_count: number;
  created_at: string;
};

export type CompanyUsageResponse = {
  success: boolean;
  company: {
    id: number;
    name: string;
    status: string;
  };
  window: {
    start_at: string | null;
    end_at: string | null;
  };
  totals: {
    request_count: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  by_feature: Array<{
    feature_key: string;
    request_count: number;
    total_tokens: number;
  }>;
  by_team: Array<{
    team_id: number;
    request_count: number;
    total_tokens: number;
  }>;
  by_user: Array<{
    user_id: number | null;
    request_count: number;
    total_tokens: number;
  }>;
};
```

## 6. 호환성 및 주의사항

- `company_name` 필드는 제거되지 않았습니다. 기존 표시 로직은 대부분 유지 가능합니다.
- 신규 회사 단위 기능은 반드시 `company_id` 기준으로 호출해야 합니다.
- `company_id`는 nullable입니다. 개인 계정 또는 과거 데이터 일부에는 null일 수 있습니다.
- `plan_code`는 회사 기준이 아니라 팀 기준입니다.
- `team_usage_events.company_id`는 마이그레이션 이후 백필되었고, 신규 이벤트는 자동으로 저장됩니다.
- 회사 삭제/비활성화 UI는 아직 없습니다. 백엔드에는 `companies.status`가 있지만 현재 API로 관리하지 않습니다.

## 7. 프론트 QA 시나리오

1. 기업 계정으로 로그인 후 `/api/profile` 응답에 `company_id`, `company_name`, `team_id`, `plan_code`가 들어오는지 확인합니다.
2. Super Admin 계정 리스트에서 기업 계정 row에 `company_id`가 존재하는지 확인합니다.
3. Super Admin 팀 리스트에서 팀 row에 `company_id`, `company_name`이 표시되는지 확인합니다.
4. 계정 수정 모달에서 회사명을 변경한 뒤 계정 리스트/팀 리스트의 회사명이 갱신되는지 확인합니다.
5. 팀 사용량 API가 기존처럼 정상 표시되는지 확인합니다.
6. 회사 사용량 API `GET /api/admin/companies/:company_id/usage`가 totals/by_feature/by_team/by_user를 반환하는지 확인합니다.
7. `company_id`가 null인 개인 계정에서는 회사 사용량 버튼이 비활성화되는지 확인합니다.
