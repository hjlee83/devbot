# DevBot Roadmap

## Phase 0 — Development system
- [x] Common project rules
- [x] Task contract format
- [x] Result and PR evidence format
- [x] Verification gate convention

## Phase 1 — Local MVP
- [x] Task 001: Bootstrap
- [x] Task 002: GitHub read client
- [x] Task 003: Global queue selection
- [x] Task 004: Workspace and agent runner
- [x] Task 005: Main polling loop

## Phase 2 — GitHub workflow
- [x] Task 006: Issue state transitions
- [x] Task 007: Commit, push, and PR creation
- [x] Task 008: PR feedback rework loop
- [x] Task 009: Beta smoke test (Task 001-008 wired into one flow via
      `PollingService.run_once`)
- [x] Task 010: ReworkService wired into the polling loop (`review`-state
      Issues' unprocessed `@devbot` PR comments are now detected and
      reworked automatically, reusing the existing branch/PR)
- [x] Task 011: `ClaudeRunner` added and Agent execution split into
      implementer/reviewer roles (default implementer=`claude`,
      reviewer=`codex`; unset role config falls back to the legacy
      `DEFAULT_AGENT`). Both the ready-Issue path and Task 010's rework
      path run through `implementer_runner`; `reviewer_runner` is
      constructed and injected but not yet invoked anywhere (automatic
      review execution is a later Task).
- [x] Task 012: role-based polling orchestration. The reviewer role now
      actually runs — a `devbot:review` Issue's linked PR is auto-reviewed
      once per head commit, `REQUEST CHANGES` auto-triggers Task 010's
      rework path via the same posted comment, `MERGE READY` waits for a
      human Merge. A `Job`/`JobType` scheduler
      (`devbot.scheduler.select_jobs`) replaced the old global single-task
      gate with a per-repository one, so different repositories' jobs can
      run in parallel under `MAX_CONCURRENT_JOBS` (default `1`, same serial
      behavior as before).

## Phase 3 — Operations
- [ ] macOS launchd
- [ ] VPS systemd migration
- [x] Task 013: 운영 관찰성 및 디버그 로깅. 시작 시 실행 구성(버전/역할/
      dry-run/poll interval/concurrency)과 관리 저장소 목록을 INFO로
      기록하고, 관리 저장소가 0개면 `no_managed_repositories`로
      `no_ready_task`와 명확히 구분해 해당 cycle을 건너뛴다. 매 cycle마다
      `cycle_id`로 로그를 연관지을 수 있고, `LOG_LEVEL=DEBUG` 또는
      `--verbose`에서 저장소별 검색 조건, 후보 Job, 표준화된 제외 사유
      (`repository_busy`/`issue_busy`/`concurrency_limit`/
      `missing_linked_pr`/`already_reviewed_head`/`no_unprocessed_feedback`/
      `not_ready`/`lower_priority` 등), 선택 Job, 단계별 소요 시간을
      추적할 수 있다. `SecretRedactingFilter`/`SafeLogger`
      (`src/devbot/observability.py`)로 Secret·Authorization 노출과 로깅
      예외로 인한 Job 실행 중단을 모두 방지했다. Task 012의 자동화·우선순위·
      concurrency 동작은 변경하지 않았다.
- [x] Task 014: workflow state machine hardening. `devbot:rework` 상태를
      도입해 리뷰 대기와 수정 대기를 분리하고, claim 이후 사전 검증 실패·
      실행 실패·예상하지 못한 예외에서도 Issue가 `devbot:working`에 영구
      정체되지 않도록 이전 안정 상태 복구 또는 `devbot:blocked` 전이를
      보장한다. 상태 라벨 단일성, 경쟁 claim 방지, 동일 피드백 중복 방지,
      기존 우선순위와 concurrency 회귀 방지를 포함한다.
- [x] Task 015: review gate standardization. 리뷰어가 어떤 Agent로 바뀌어도
      동일한 `MERGE READY` / `REQUEST CHANGES` 기준을 적용하도록
      `AGENTS.md`, Task 계약 표준, 자동 리뷰 프롬프트, 테스트에 엄격한 검증
      게이트를 고정한다. PR Evidence, Result, CI, Task 계약, 운영 정책이
      하나라도 불일치하면 `REQUEST CHANGES`로 판단하며, 이 기준은 구현 관여
      여부나 특정 Agent 종류와 관계없이 모든 Reviewer에게 동일하게 적용된다.
- [x] Task 016: rework no-op and metadata action handling. 리뷰 결과가
      repository file change, GitHub metadata-only action, external verification
      중 무엇을 요구하는지 구분하고, `nothing to commit`을 DeliveryError로
      처리해 `devbot:blocked` 루프를 만드는 문제를 제거한다. metadata-only
      요청은 commit 경로로 보내지 않고, 실제 파일 변경 rework는 기존처럼 PR을
      업데이트한다. Agent가 대화형 승인을 요구한 경우 delivery로 진행하지 않고,
      Issue에 연결된 open PR이 있으면 delivery는 새 `devbot/devbot-*` branch가
      아니라 해당 PR의 head branch를 사용해야 한다.
- [x] Task 017: GitHub status timeline protocol. DevBot의 현재 상태를 VPS나
      로컬 로그가 아니라 GitHub Issue/PR/label/comment/check/commit 기준으로
      일관되게 요약할 수 있도록 상태 타임라인 규격을 문서화한다. Queue, Dev,
      Wait reviewer, Review, Wait implementer 구간의 시작 시간·종료 시간·소요
      시간을 모두 표시하고, 사람이 읽는 상태 카드와 기계용 hidden marker 형식을
      분리해 정의한다(`docs/10-github-status-timeline.md`). marker 자동 기록,
      `devbot status` CLI, Dashboard/UI는 후속 Task로 남긴다.
