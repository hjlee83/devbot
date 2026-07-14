# Task 018: Timeline CLI

## Goal

Task 017의 `devbot-timeline:v1` 프로토콜을 실제 수동 운영에 사용할 수 있도록
`timeline start`, `timeline end`, `timeline status` CLI를 구현한다.

수동으로 Claude/Codex/GPT에게 구현·리뷰를 요청하더라도 Issue의 단일 Timeline
comment를 통해 누가 언제 어떤 역할을 시작·종료했는지 GitHub에 기록하고, 동일한
Status Card를 조회할 수 있어야 한다.

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
