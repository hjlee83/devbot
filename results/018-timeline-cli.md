# Task 018 Result: Timeline CLI

## 완료 내용

- Task 017의 `devbot-timeline:v1` 프로토콜(`docs/10-github-status-timeline.md`)을
  실제 수동 운영에서 쓸 수 있도록 `uv run devbot timeline start/end/status` CLI를
  신규 구현했다(`src/devbot/timeline.py`, `src/devbot/main.py`).
- `src/devbot/timeline.py` (신규)
  - `TimelineEvent`: `devbot-timeline:v1` marker 한 줄을 표현하는 frozen dataclass.
    `issue`/`pr`/`cycle`/`phase`/`event`/`result`/`at`은 Task 017 필수 필드 그대로,
    `actor`는 이번 Task의 확장 필드다 — `docs/10` 4.2절이 "파서는 알 수 없는 추가
    필드를 무시해야 한다"고 명시하므로 추가해도 프로토콜을 깨지 않는다.
  - `parse_events()` / `TimelineEvent.to_marker()`: comment 본문의 hidden HTML
    comment marker를 왕복 파싱/직렬화한다.
  - `current_cycle()`: "1 + 지금까지의 `request-changes` review:end 개수"라는 단일
    공식으로 dev/review 어느 이벤트든 현재 cycle을 계산한다(CP-018-5).
  - `render_status_card()` / `render_comment_body()`: Task 017 7절 Status Card
    형식(State/Waiting/Queue/Cycle별 Dev·Wait reviewer·Review·Wait implementer·
    Result/Total active·waiting·elapsed)을 6절 계산 규칙대로 렌더링한다. Status
    Card에는 raw marker 필드를 노출하지 않는다.
  - `TimelineService`: `GitHubClient`(read)/`GitHubWriteClient`(write)를 받아
    `<!-- devbot-timeline:comment:v1 -->` marker를 가진 단일 Timeline comment를
    찾고, `start`/`end`/`status`를 수행한다. `dry_run=False`가 기본값이다 —
    이유는 아래 "리뷰 피드백 반영" 참고.
- `src/devbot/github_client.py`: `get_issue()` 추가 — 단일 Issue를 조회해
  `timeline status`/`start`/`end`가 현재 `devbot:*` 라벨을 읽을 수 있게 한다.
- `src/devbot/github_write_client.py`: `update_comment()`(PATCH) 추가 — 기존
  Timeline comment를 새 댓글 생성 없이 그대로 갱신한다.
- `src/devbot/main.py`: `argparse` 서브파서로 `timeline start|end|status`를
  추가했다. 서브커맨드가 없으면(`devbot`, `devbot --once`, `devbot --dry-run`)
  기존 daemon 경로가 그대로 실행된다 — `timeline` 분기는 daemon의
  `ProcessLock`/`PollingService` 초기화보다 앞에서 반환하므로, 데몬이 이미 실행
  중이어도 안전하게 병행 호출할 수 있다. `--repo owner/repo`는 선택 인자이며,
  생략하면 `config/repositories.yaml`의 단일 enabled 저장소를 사용한다(현재
  배포는 `hjlee83/devbot` 하나뿐). `start`/`end`에는 daemon 최상위 `--dry-run`과
  별개인 자체 `--dry-run`이 있다(리뷰 피드백 반영 참고).
- 대상 저장소 `hjlee83/devbot`, Issue #34에 대해 실제 `uv run devbot timeline
  status --issue 34`를 실행해 read 경로(인증, `get_issue`, `list_issue_comments`)가
  실제 GitHub API로 동작함을 확인했다(Issue #34는 아직 Timeline comment가 없어
  "기록된 이벤트 없음"으로 정확히 보고됨). Issue #34는 `devbot:manual-action`
  상태이므로 이번 Task에서는 `timeline start/end`(쓰기)를 이 Issue에 대해
  실행하지 않았다 — 지시대로 라벨/상태를 건드리지 않기 위함이며, start/end 쓰기
  경로는 `tests/test_timeline.py`의 fake GitHub backend로 전 시나리오를
  검증했다.

## 수정 파일

- `src/devbot/timeline.py` (신규)
- `src/devbot/main.py`
- `src/devbot/github_client.py`
- `src/devbot/github_write_client.py`
- `tests/test_timeline.py` (신규)
- `tests/test_github_client.py`
- `tests/test_github_write_client.py`
- `docs/00-roadmap.md`
- `tasks/018-timeline-cli.md` (2차 리뷰 반영: 누락된 필수 항목 8개 보강, 아래
  "2차 리뷰 피드백 반영" 참고)
- `results/018-timeline-cli.md` (신규, 이 문서)

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-018-1 명령 파싱 | `test_timeline_cli_parses_start_end_status` |
| CP-018-2 comment 없을 때 단 하나 생성 | `test_timeline_start_creates_single_comment_when_missing` |
| CP-018-3 기존 comment 수정 | `test_timeline_start_updates_existing_comment` |
| CP-018-4 start/end marker 기록 | `test_timeline_end_appends_marker_and_renders_status_card` |
| CP-018-5 cycle 증가 규칙 | `test_timeline_request_changes_increments_next_dev_cycle` |
| CP-018-6 구간 계산 | `test_timeline_status_calculates_active_and_waiting_durations` |
| CP-018-7 actor/phase 표시 | `test_timeline_status_shows_actor_for_each_phase` |
| CP-018-8 idempotency | `test_timeline_duplicate_start_is_idempotent` |
| CP-018-9 잘못된 순서 거부 | `test_timeline_rejects_overlapping_same_phase_start`, `test_timeline_rejects_end_without_start` |
| CP-018-10 API 실패 시 데이터 보존 | `test_timeline_update_failure_preserves_existing_comment` |
| CP-018-11 daemon CLI 회귀 없음 | `test_existing_devbot_once_cli_still_works` |
| CP-018-12 Result/문서 정합성 | 이 문서 자체 + PR Evidence (전용 unit test 없음 — 계약에도 CP-018-12에 대응하는 별도 필수 테스트 이름이 없다) |

보조 회귀 테스트(계약의 필수 테스트 목록 밖, 새 public API/리뷰 피드백 커버리지):

- `test_get_issue_parses_single_issue` (`tests/test_github_client.py`) — 신규 `GitHubClient.get_issue()`.
- `test_update_comment_sends_patch_with_body` (`tests/test_github_write_client.py`) — 신규 `GitHubWriteClient.update_comment()`.
- `test_client_exposes_read_operations_only` (기존, Task 002 계약) — `get_issue` 추가를 허용 목록에 반영해 업데이트.
- `test_timeline_start_ignores_global_dry_run_by_default` (`tests/test_timeline.py`) — PR #35 리뷰 Blocker 2 회귀.
- `test_timeline_start_dry_run_flag_opts_into_preview_only` (`tests/test_timeline.py`) — 위와 동일, `--dry-run` opt-in 경로.

## 검증 결과

PR #35 리뷰 피드백(Blocker 1/2) 반영 커밋 기준 전체 재검증:

```
uv sync
  Resolved 15 packages in 22ms
  Checked 14 packages in 6ms

uv run ruff check .
  All checks passed!

uv run pytest
  272 passed (신규 14개 + 보조 회귀 3개 + 기존 255개, 회귀 없음)

uv run devbot timeline --help
  usage: devbot timeline [-h] {start,end,status} ...
  (start/end/status 서브커맨드 --help 포함 모두 정상 출력 확인;
   start/end에 --dry-run 옵션이 노출됨을 확인)

uv run devbot timeline status --issue 34
  #34 / PR #-
  State: devbot:manual-action
  Waiting: 없음 (기록된 이벤트 없음)
  Queue: - (ready 이벤트 기록 없음)
  Total active: 0m
  Total waiting: 0m
  Total elapsed: 0m
  (실제 hjlee83/devbot Issue #34에 대해 실행, 종료 코드 0)
```

## TODO

- 없음 (이 Task 범위 내). daemon이 5개 필수 이벤트 발생 시 marker를 자동
  기록하는 것은 Task 017/018 모두의 명시적 제외 범위이며, 후속 Task로 남긴다.

## 위험 요소

- `timeline start/end`는 `--repo`가 없으면 `config/repositories.yaml`의
  enabled 저장소가 정확히 1개일 때만 자동으로 대상을 고른다. 저장소가
  여러 개로 늘어나면 `--repo owner/repo`를 반드시 지정해야 하며, 그렇지
  않으면 명확한 `ConfigError`로 실패한다(모호한 저장소로 잘못 쓰지 않도록
  하는 의도적 설계).
- `Total elapsed`는 `devbot:done` 이후 실제 Merge 시각(PR `merged_at`)을
  별도로 조회하지 않고, "Queue 시작 → 지금(or 마지막 기록 이벤트)"으로만
  계산한다. Task 017 문서의 7.3 예시(Merge 10분 후까지 포함한 87m)처럼 사람이
  실제 Merge한 시각까지 정확히 반영하려면 PR `merged_at` 조회가 추가로
  필요하다 — 계약의 CP-018 목록에 명시된 요구가 아니므로 이번 Task 범위에는
  넣지 않았다.
- `--phase queue`(즉 `ready` 이벤트)를 기록하는 CLI 경로는 없다. 계약의 CLI
  예시 5개(scope item 1)가 전부 `dev`/`review`만 사용하므로 `start`/`end`의
  `--phase` choices도 `dev`/`review`로 제한했다. `ready` 이벤트가 한 번도
  기록되지 않은 Issue의 Status Card는 `Queue: - (ready 이벤트 기록 없음)`으로
  표시되며 계산에서 제외된다(6.3절의 "best-effort 추정" 원칙과 일치).
- 동시에 같은 Issue에 대해 여러 프로세스가 `timeline start/end`를 경합
  실행하면(예: 두 사람이 동시에 같은 comment를 PATCH), GitHub 쪽 낙관적 동시성
  보장이 없어 나중에 도착한 쓰기가 먼저 쓰기를 덮어쓸 수 있다. 이는 Task
  014의 `IssueStateWriter`가 갖는 프로세스 내부 락과 달리 이 CLI는 매 호출이
  독립 프로세스이므로 프로세스 간 락을 제공하지 않는다 — 수동 운영 빈도에서는
  허용 가능한 위험으로 판단했다(계약에도 분산 락 요구가 없음).
- `timeline start/end`의 `--dry-run`은 daemon 최상위 `--dry-run`/`DRY_RUN`
  환경 변수와 이름은 같지만 완전히 별개 스위치다(아래 "리뷰 피드백 반영"
  참고). 운영자가 이름만 보고 둘을 같은 것으로 오해하지 않도록 `devbot
  timeline start --help`의 설명 문구에 "실제로 GitHub에 쓰지 않고"를 명시했다.

## 리뷰 피드백 반영 (PR #35, `hjlee83` REQUEST CHANGES)

`hjlee83`의 `REQUEST CHANGES` 리뷰(Blocker 2개)를 반영했다.

- **Blocker 1 — PR Evidence 누락**: PR #35 본문이 초기 "Planned scope" 상태로
  남아 있어 `docs/09-task-contract-standard.md`가 요구하는 PR Evidence(연결
  Issue/Task 경로, Branch/PR 번호, Result 문서 경로, Checkpoint별 테스트,
  검증 명령/결과, CI 상태, Task/Result/PR 일치 근거)가 없었다. PR #35 본문을
  구현 완료 상태의 Evidence로 갱신했다(Checkpoint-to-test 표 포함).
- **Blocker 2 — 기본 dry-run 충돌**: `src/devbot/main.py`가
  `TimelineService(..., dry_run=config.dry_run)`으로 구성되어 있어, 배포
  기본값 `DRY_RUN=true` 환경에서는 `timeline start/end`가 Status Card만
  계산하고 실제 GitHub comment를 생성/수정하지 않을 수 있었다. 이는
  `tasks/018-timeline-cli.md` Goal("실제 수동 운영에 사용할 수 있어야
  한다")과 Scope 2("start/end는 comment를 찾아 없으면 만들고 있으면
  수정한다"), CP-018-2/3/4와 정면으로 충돌하는 동작이었다.
  - **수정**: `start`/`end` 서브커맨드에 자체 `--dry-run` 플래그를
    추가했다(`_add_timeline_write_args`). `_run_timeline_command`는
    이제 `config.dry_run`이 아니라 이 플래그(`getattr(args, "dry_run",
    False)`)로 `TimelineService.dry_run`을 결정한다. `TimelineService`의
    dataclass 기본값도 `True → False`로 바꾸고, 이 클래스가 daemon의
    자동 쓰기(`IssueStateWriter`/`DeliveryService`)와 다른 이유(사람이
    그 순간 명시적으로 실행하는 1회성 기록 커맨드)를 docstring에 남겼다.
  - **결과**: `devbot timeline start/end`는 이제 전역 `DRY_RUN` 값과
    무관하게 기본적으로 실제 GitHub에 기록한다. 미리보기가 필요하면
    `--dry-run`을 명시적으로 줘야 한다.
  - **회귀 테스트 추가**: `test_timeline_start_ignores_global_dry_run_by_default`
    (env `DRY_RUN=true`여도 `TimelineService`가 `dry_run=False`로 구성됨을
    고정), `test_timeline_start_dry_run_flag_opts_into_preview_only`
    (`--dry-run`을 주면 `dry_run=True`로 구성됨을 고정).
  - **CP-018 영향 없음**: 계약의 CP-018-2/3/4/CLI 예시(Scope item 1)는
    바뀌지 않았다 — 오히려 이번 수정이 그 요구를 실제로 충족시킨다.
- **검증**: `uv run ruff check .` 통과, `uv run pytest` 272 passed(신규 회귀
  테스트 2개 포함, 기존 270개 회귀 없음).

## 2차 리뷰 피드백 반영 (PR #35, `hjlee83` REQUEST CHANGES, head `c941f55` 재리뷰)

이전 두 Blocker는 해결되었다고 확인된 재리뷰에서, 계약/Issue metadata 정합성
관점의 새 Blocker 2개가 나왔다.

- **Blocker 1 — Task 계약서가 `docs/09-task-contract-standard.md` 필수 14개
  항목을 충족하지 않음**: `tasks/018-timeline-cli.md`에는 배경, Dependencies,
  Files Expected to Change, Risk, Rollback Strategy, Reviewer Focus,
  Definition of Done, Result 문서 경로가 없었다. 대조해 보니 지적이 정확했다.
  - **수정**: 위 8개 섹션을 `tasks/018-timeline-cli.md`에 추가했다. 기존
    Goal/Branch·PR Policy/Scope/Out of Scope/Required Checkpoints/Required
    Tests/Verification Gate는 문구 하나 바꾸지 않았다 - CP-018 12개, 필수
    테스트 12개 이름 모두 그대로다(`docs/09`의 "구현 결과에 맞추기 위해
    Task를 사후에 축소하거나 품질 게이트를 삭제해서는 안 된다" 원칙 준수).
    새로 추가한 섹션 내용은 이미 완료된 구현·검증 결과를 그대로 옮겨 적은
    것이라(Files Expected to Change = 실제 diff 파일 목록, Risk/Rollback
    Strategy = 이번 rework에서 실제로 겪은 위험과 대응) 사후 정당화가 아니라
    사실 기록이다.
- **Blocker 2 — 실행용 Issue #34 metadata 누락**: Issue #34 본문에 Task 계약
  경로와 Branch는 있었지만 Pull Request 번호와 Result 문서 경로가 없어
  `docs/09`의 단일 Task 추적 정책과 어긋났다.
  - **수정**: Issue #34 본문에 `Pull Request: #35`와
    `Result 문서: results/018-timeline-cli.md` 두 줄만 추가했다. 라벨
    (`devbot:manual-action`)은 손대지 않았다 - 사용자 지시("Issue #34는
    devbot:manual-action 상태이므로 라벨을 변경하지 마세요")를 그대로
    지켰고, `gh issue edit --body-file`은 라벨에 영향을 주지 않는다.
- **검증**: `uv run ruff check .` 통과, `uv run pytest` 272 passed(코드 변경
  없음 - 이번 rework는 `tasks/018-timeline-cli.md`, Issue #34 본문, 이
  Result 문서만 수정했다).

## Improvement Suggestions

- daemon의 dev/review 단계에서 이 Task의 marker 기록 로직(`TimelineService`)을
  재사용해 자동으로 이벤트를 남기는 후속 Task(Task 017 문서 9절, Task 018
  계약의 "Out of Scope" 첫 항목)를 진행한다. `TimelineService.start/end`는
  이미 daemon과 무관하게 재사용 가능한 형태로 분리해 두었다.
- `devbot:done` Issue의 `Total elapsed`를 PR `merged_at` 기준으로 보정하는
  옵션을 추가한다(위 위험 요소 참고).
- 여러 저장소를 한 번에 다루는 배포를 지원하려면 `--repo` 필수화 또는
  기본 저장소 설정 항목을 `config/repositories.yaml`에 추가하는 방안을 검토한다.
