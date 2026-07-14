# Task 018: Timeline CLI

## Goal

Task 017의 `devbot-timeline:v1` 프로토콜을 실제 수동 운영에 사용할 수 있도록
`timeline start`, `timeline end`, `timeline status` CLI를 구현한다.

수동으로 Claude/Codex/GPT에게 구현·리뷰를 요청하더라도 Issue의 단일 Timeline
comment를 통해 누가 언제 어떤 역할을 시작·종료했는지 GitHub에 기록하고, 동일한
Status Card를 조회할 수 있어야 한다.

## 배경

Task 017(`docs/10-github-status-timeline.md`)은 `devbot-timeline:v1` marker와
Status Card 규격만 정의했고, 실제로 marker를 GitHub comment에 기록하는 구현은
후속 Task로 남겼다(같은 문서 9절 "후속 구현 범위"). 그 사이 DevBot daemon이
아직 이 marker를 자동 기록하지 않으므로, Claude/Codex/GPT에게 수동으로 구현·
리뷰를 맡기는 현재 운영 방식에서는 "누가 언제 dev/review를 시작·종료했는지"를
GitHub만으로 답할 방법이 없다. 이 Task는 그 공백을 메우는 수동 기록/조회
CLI를 제공한다. daemon이 5개 필수 이벤트 발생 시 marker를 자동으로 남기는
것은 여전히 이 Task의 범위 밖이다(Out of Scope 참고).

## Dependencies

- Task 017(`docs/10-github-status-timeline.md`)의 `devbot-timeline:v1` marker
  형식, 필수 이벤트, 구간 계산 규칙, Status Card 형식이 확정되어 있어야 한다.
- 기존 `src/devbot/github_client.py`(read)/`github_write_client.py`(write)의
  Issue/comment 조작 API, `src/devbot/main.py`의 CLI 진입점 구조.
- 별도 외부 서비스 의존성은 없다(GitHub REST API, 기존 `GITHUB_TOKEN` 재사용).

## Branch / PR Policy

- Branch: `task/018-timeline-cli`
- Task 하나 = Branch 하나 = PR 하나
- 계약, 구현, 테스트, Result를 모두 이 브랜치와 PR에 포함한다.

## Scope

1. 다음 CLI를 제공한다.

```bash
uv run devbot timeline start --issue 34 --phase dev --actor claude
uv run devbot timeline end --issue 34 --phase dev --actor claude --result pushed --pr 35
uv run devbot timeline start --issue 34 --phase review --actor codex --pr 35
uv run devbot timeline end --issue 34 --phase review --actor codex --result request-changes --pr 35
uv run devbot timeline status --issue 34
```

2. `start`와 `end`는 해당 Issue에서 `<!-- devbot-timeline:comment:v1 -->` marker를 가진
   단일 Timeline comment를 찾는다. 없으면 하나를 만들고, 있으면 같은 comment를 수정한다.
   새 이벤트마다 별도 댓글을 생성하지 않는다.
3. Timeline comment는 사람이 읽는 Status Card와 기계용
   `<!-- devbot-timeline:v1 ... -->` event marker 목록을 함께 보관한다.
4. 이벤트 시각은 UTC ISO 8601로 저장하고, 출력은 기본적으로 로컬 시간대
   (`Asia/Seoul`)로 렌더링한다.
5. `cycle`은 최초 dev가 1이며 `request-changes` 이후 다음 dev start에서 1 증가한다.
6. `status`는 GitHub comment와 현재 Issue label/연결 PR을 읽어 Task 017 형식으로 출력한다.
7. 동일한 start/end 요청의 재실행은 중복 marker를 만들지 않는 idempotent 동작이어야 한다.
8. 종료되지 않은 동일 phase가 있는데 다시 start하면 명확한 오류를 반환한다.
9. 대응하는 start가 없는 end는 명확한 오류를 반환하며 comment를 훼손하지 않는다.
10. comment update 충돌 또는 GitHub API 실패 시 기존 comment 데이터가 유실되지 않아야 한다.
11. 기존 daemon `uv run devbot`, `--once`, `--dry-run` 동작은 회귀하지 않아야 한다.
12. `results/018-timeline-cli.md`에 구현 범위, 검증 결과, 제한 사항을 기록한다.

## Out of Scope

- DevBot daemon의 implement/review 단계에 marker를 자동 삽입하는 기능
- Dashboard 또는 웹 UI
- 여러 저장소의 Timeline을 한 번에 집계하는 기능
- 과거 Issue의 이벤트를 추정해 backfill하는 기능

## Required Checkpoints

- CP-018-1: `timeline start/end/status` 명령 파싱
- CP-018-2: Timeline comment가 없을 때 단 하나 생성
- CP-018-3: 기존 Timeline comment를 찾아 동일 comment 수정
- CP-018-4: Task 017 marker 형식의 start/end 이벤트 기록
- CP-018-5: cycle 증가 규칙
- CP-018-6: Status Card의 Queue/Dev/Wait reviewer/Review/Wait implementer 계산
- CP-018-7: actor와 phase 표시
- CP-018-8: 중복 요청 idempotency
- CP-018-9: 잘못된 start/end 순서 거부
- CP-018-10: GitHub API 실패 시 데이터 보존
- CP-018-11: 기존 daemon CLI 회귀 없음
- CP-018-12: Result 및 문서 정합성

## Required Tests

- `test_timeline_cli_parses_start_end_status`
- `test_timeline_start_creates_single_comment_when_missing`
- `test_timeline_start_updates_existing_comment`
- `test_timeline_end_appends_marker_and_renders_status_card`
- `test_timeline_request_changes_increments_next_dev_cycle`
- `test_timeline_status_calculates_active_and_waiting_durations`
- `test_timeline_status_shows_actor_for_each_phase`
- `test_timeline_duplicate_start_is_idempotent`
- `test_timeline_rejects_overlapping_same_phase_start`
- `test_timeline_rejects_end_without_start`
- `test_timeline_update_failure_preserves_existing_comment`
- `test_existing_devbot_once_cli_still_works`

## Verification Gate

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot timeline --help
uv run devbot timeline status --issue <test-issue>
```

Merge 전에 Task 계약, Result, PR Evidence, 테스트, CI가 모두 일치해야 한다. 하나라도
불일치하면 `REQUEST CHANGES`다.

## Files Expected to Change

- `src/devbot/timeline.py` (신규) - marker 파싱/렌더링, cycle 계산, Status Card,
  `TimelineService`
- `src/devbot/main.py` - `timeline start/end/status` 서브커맨드 등록
- `src/devbot/github_client.py` - 단일 Issue 조회(`get_issue`) 추가
- `src/devbot/github_write_client.py` - comment 수정(`update_comment`) 추가
- `tests/test_timeline.py` (신규), `tests/test_github_client.py`,
  `tests/test_github_write_client.py`
- `results/018-timeline-cli.md` (신규)
- `docs/00-roadmap.md` (Task 018 항목 상태 갱신)

daemon의 폴링/리뷰/rework 로직(`src/devbot/polling.py`, `review.py`, `rework.py`,
`issue_state.py`, `delivery.py`)은 변경 대상이 아니다 - CP-018-11이 이를
회귀 테스트로 고정한다.

## Risk

- 새 CLI가 `GitHubClient`/`GitHubWriteClient`에 공개 메서드를 추가하므로,
  두 클라이언트의 공개 API를 고정하는 기존 화이트리스트 회귀 테스트
  (`test_client_exposes_read_operations_only` 등)를 함께 갱신하지 않으면
  기존 테스트가 깨진다.
- `timeline start/end`는 실제로 GitHub write API를 호출한다. 배포의 전역
  `DRY_RUN` 값과 이 CLI 자체의 쓰기 여부가 서로 다른 스위치로 오인되면,
  운영자가 기록되는 줄 알았는데 기록되지 않거나 그 반대인 상황이 생길 수
  있다 - CLI `--help` 문구와 Result 문서에 그 관계를 명확히 기록해야 한다.
- Issue/PR 대상 저장소가 둘 이상으로 늘어나면 `--repo` 없이는 어느 저장소를
  가리키는지 모호해진다.

## Rollback Strategy

이 Task는 daemon의 기존 폴링/리뷰/rework 경로를 수정하지 않고 `timeline`
서브커맨드만 추가하므로, 문제가 생기면 `src/devbot/main.py`의 `timeline`
서브파서 등록과 `src/devbot/timeline.py` 파일만 되돌리면 기존
`devbot`/`devbot --once`/`--dry-run` 동작은 영향을 받지 않는다(CP-018-11의
회귀 테스트가 이를 보장). 이미 GitHub에 생성된 Timeline comment는 daemon이
읽지 않으므로(marker 자동 소비는 Out of Scope) 롤백 후에도 그대로 두어도
안전하며, 필요하면 사람이 comment를 수동으로 삭제/수정할 수 있다.

## Reviewer Focus

- idempotency: 동일한 start/end 재실행이 marker를 중복 기록하지 않는지.
- 잘못된 순서(겹치는 start, start 없는 end) 거부와 GitHub API 실패 시 기존
  comment 데이터 보존.
- `timeline start/end`가 배포 기본값(`DRY_RUN=true`)에서도 실제로 기록하는지 -
  전역 `DRY_RUN`과 CLI 자체 `--dry-run`을 혼동하지 않는지.
- cycle 증가 규칙과 actor/phase가 Status Card에 정확히 반영되는지.

## Definition of Done

- Required Checkpoints(CP-018-1~12) 전부가 Required Tests의 대응 테스트로
  통과한다.
- `uv sync`, `uv run ruff check .`, `uv run pytest`, `uv run devbot timeline
  --help`, `uv run devbot timeline status --issue <test-issue>`가 모두 성공한다.
- 기존 daemon CLI(`devbot`, `--once`, `--dry-run`)에 회귀가 없다.
- `results/018-timeline-cli.md`가 최신 구현/검증 결과를 반영한다.
- PR Evidence(연결 Issue/Task, Branch/PR, Result 경로, Checkpoint별 테스트,
  검증 명령/결과, CI 상태, 일치 근거)가 PR 본문 또는 최신 댓글에 있다.

## Result 문서 경로

`results/018-timeline-cli.md`
