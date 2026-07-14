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
    찾고, `start`/`end`/`status`를 수행한다. `dry_run=True`(기본값)는 이
    코드베이스의 다른 모든 write 경로(`IssueStateWriter`, `DeliveryService`)와
    동일하게 계산은 하되 실제 GitHub 쓰기는 하지 않는다.
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
  배포는 `hjlee83/devbot` 하나뿐).
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

보조 회귀 테스트(계약의 필수 테스트 목록 밖, 새 public API 커버리지):

- `test_get_issue_parses_single_issue` (`tests/test_github_client.py`) — 신규 `GitHubClient.get_issue()`.
- `test_update_comment_sends_patch_with_body` (`tests/test_github_write_client.py`) — 신규 `GitHubWriteClient.update_comment()`.
- `test_client_exposes_read_operations_only` (기존, Task 002 계약) — `get_issue` 추가를 허용 목록에 반영해 업데이트.

## 검증 결과

```
uv sync
  Resolved 15 packages in 22ms
  Checked 14 packages in 6ms

uv run ruff check .
  All checks passed!

uv run pytest
  270 passed (신규 12개 + 보조 회귀 2개 + 기존 256개, 회귀 없음)

uv run devbot timeline --help
  usage: devbot timeline [-h] {start,end,status} ...
  (start/end/status 서브커맨드 --help 포함 모두 정상 출력 확인)

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

## Improvement Suggestions

- daemon의 dev/review 단계에서 이 Task의 marker 기록 로직(`TimelineService`)을
  재사용해 자동으로 이벤트를 남기는 후속 Task(Task 017 문서 9절, Task 018
  계약의 "Out of Scope" 첫 항목)를 진행한다. `TimelineService.start/end`는
  이미 daemon과 무관하게 재사용 가능한 형태로 분리해 두었다.
- `devbot:done` Issue의 `Total elapsed`를 PR `merged_at` 기준으로 보정하는
  옵션을 추가한다(위 위험 요소 참고).
- 여러 저장소를 한 번에 다루는 배포를 지원하려면 `--repo` 필수화 또는
  기본 저장소 설정 항목을 `config/repositories.yaml`에 추가하는 방안을 검토한다.
