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
- [x] Task 018: timeline CLI. 수동 Claude/Codex/GPT 작업에서도 Task 017의
      단일 Timeline comment를 갱신할 수 있도록 `timeline start`, `timeline end`,
      `timeline status` 명령을 제공한다. 이벤트는 UTC marker로 저장하고 Status Card는
      Queue/Dev/Review/Waiting 구간과 actor별 작업 시간을 계산해 표시한다
      (`src/devbot/timeline.py`, `results/018-timeline-cli.md`). daemon 자동
      marker 기록은 후속 Task로 남긴다.
- [x] Task 019: daemon reliability baseline. 9개 실패 분류
      (`FailureCategory`), 분류별 재시도 정책, 복구 정책, 폴링 시작 전
      시작 검증, `uv run devbot doctor`, 실패마다 남는 진단 보고서를
      도입한다. 단일 Job 실행 경로(`max_concurrent_jobs=1`)가 예상 밖
      예외로 daemon 전체를 죽일 수 있던 안전망 공백을 막고, Agent
      세션/사용량 제한을 별도 분류해 자동 재시도 없이 명확한 복구 힌트와
      함께 `devbot:blocked`로 보낸다(`src/devbot/reliability.py`,
      `src/devbot/startup.py`, `src/devbot/doctor.py`,
      `docs/11-daemon-reliability.md`,
      `results/019-daemon-reliability-baseline.md`). Worktree 격리,
      Timeline 자동 기록, 멀티 Agent failover, VPS 배포는 범위 밖으로
      남긴다.
- [x] Task 020: daemon queue summary and cycle logging. cycle마다 흩어져
      있던 자유 형식 로그 줄("폴링을 시작합니다", "ready 상태 Issue 수",
      "선택 가능한 ready Issue가 없습니다" 등)을 Queue Summary(6개
      워크플로 상태 개수, cycle당 정확히 한 번) / Selected(선택된 Job의
      repo/issue/pr/job_type) / Cycle Result(`NO_RUNNABLE_TASK` 또는
      `IMPLEMENT`/`REVIEW`/`REWORK` 또는 `FailureCategory` 코드로 정규화된
      값 하나) 세 블록으로 대체한다. `devbot:*` 상태 라벨이 두 개 이상
      붙은 Issue는 `issue_to_task`가 이미 쓰던 첫 일치 규칙으로 정확히 한
      버킷에만 집계하고, 그 충돌 자체는 `state_label_conflict`(DEBUG)로
      드러낸다. 스케줄러 우선순위/동시성, 상태 머신, 재시도, Timeline
      동작은 전혀 바꾸지 않는다(`src/devbot/observability.py`,
      `src/devbot/polling.py`, `results/020-daemon-queue-summary.md`).
- [x] Task 021: agent outcome classification hardening. Implementer Agent가
      네트워크/승인 요구로 중단되고 repository 변경이 없는데도 기존
      Task-contract-only PR이 연결되어 있다는 이유만으로 review로
      false-success 전이하던 Issue #41 사고를 닫는다. `AgentRunResult`를
      `implementation_completed`/`approval_required`/`network_blocked`/
      `repository_locked`/`session_limit`/`agent_failed` 등 명시적
      `AgentOutcome`으로 먼저 분류하고, `no_repository_changes` + 기존 연결
      PR 재사용 review 재개는 `branch_has_implementation_evidence()`로 실제
      구현 커밋 증거가 있을 때만 허용한다(`src/devbot/agent_outcome.py`,
      `results/021-agent-outcome-classification.md`).
- [x] Task 022: Planner workflow standard. Planner-owned contract-first
      워크플로(1 Task = 1 Issue = 1 Branch = 1 Contract = 1 Pull Request)를
      저장소 정책으로 고정하고 기계 검증 가능하게 만든다. Planner/
      Implementer/Reviewer/Operator 역할 경계, 명명·번호 규칙, Task
      Issue/PR 템플릿, 최소 리뷰 진입 계약(`Review PR #<number>.`), 중복
      작업공간·누락 Evidence 검증을 `docs/12-planner-workflow.md`와
      `src/devbot/planner.py`로 도입한다. daemon 자동 폴링 루프에는
      연결하지 않는다 - Planner 검증은 항상 명시적으로 호출되며, 기존
      daemon/리뷰/rework/delivery/timeline/상태 머신/재시도 동작은 변경하지
      않는다(`results/022-planner-workflow-standard.md`).
- [x] Task 023: host-managed workspace preparation. IMPLEMENT/REWORK Job
      실행 전에 DevBot host가 linked PR branch를 fetch하고 격리된
      `.devbot-worktrees/<repo>/issue-<N>` worktree를 준비한다. Agent는
      준비된 worktree 안에서만 구현하고, operator checkout의 현재 branch나
      dirty 상태와 무관하게 기존 Task branch/PR을 재사용한다
      (`docs/13-host-managed-workspace-preparation.md`,
      `results/023-host-managed-workspace-preparation.md`).
- [x] Task 024: timeline auto recording. Task 017/018 Timeline protocol을
      daemon lifecycle에 연결한다. `TimelineService`가 `PollingService`/
      `ReviewService`/`ReworkService`에 주입되어 IMPLEMENT/REVIEW/REWORK
      Job마다 `ready`/`dev:start`/`dev:end`/`review:start`/`review:end`를
      기존 Task 017/018 Timeline comment에 자동 기록한다
      (`safe_ready`/`safe_start`/`safe_end`, `src/devbot/timeline.py`).
      Timeline write 실패는 best-effort로 흡수되어 진단으로만 노출되고
      Job의 실제 결과나 상태 전이를 절대 대체하지 않는다. 재시도/rework
      cycle에서도 marker가 중복되지 않으며, 수동 `devbot timeline
      start/end/status` 동작은 그대로 유지한다
      (`results/024-timeline-auto-recording.md`).
- [x] Task 025: planner-linked PR resolution hardening. 실행 Issue의
      `Pull Request: #<number>` Planner metadata를 PR body closing keyword보다
      먼저 신뢰하고, resolved PR head branch를 host-managed worktree 준비에
      사용한다. 명시 PR 누락 또는 Issue branch/PR head mismatch는 fallback
      branch 생성 없이 workspace preparation failure로 중단하며, Planner
      metadata가 없는 legacy Issue만 기존 fallback branch 생성을 유지한다
      (`results/025-planner-linked-pr-resolution.md`).
- [x] Task 026: agent resume and timeout recovery. Agent timeout/
      interruption을 `resumable_interruption`으로 구조화하고, prepared
      worktree의 dirty 재사용 상태가 Issue/PR/branch/contract metadata와
      일치할 때만 continuation prompt를 붙여 같은 Branch/PR에서 이어서
      실행한다. Resume attempt는 Issue comment marker로 제한하며 cap 초과
      또는 unsafe metadata는 worktree를 삭제하지 않고 `manual-action`으로
      보낸다(`results/026-agent-resume-timeout-recovery.md`).
- [x] Task 027: autonomous review loop. 성공한 IMPLEMENT/REWORK delivery가
      사람 명령 없이 REVIEW 후보가 되고, `REQUEST CHANGES`는 REWORK로,
      성공한 REWORK는 다시 REVIEW로 자동 순환한다. 반복 횟수는 기본 3회로
      제한하고 초과 시 작업/PR/worktree를 보존한 채 `manual-action`으로
      전환한다. 현재 head의 유효한 `MERGE READY`만 PR 라벨을
      `devbot:ready-to-merge`로 독점 갱신하며, 미처리 피드백이나 안전하지 않은
      metadata 상태는 ready 표시 대신 manual-action 진단을 남긴다
      (`results/027-autonomous-review-loop.md`).
