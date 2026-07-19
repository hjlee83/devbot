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
- [x] B2: automatic merge safety gate. `devbot:ready-to-merge` PR은
      `AUTOMERGE_ENABLED`, repository allowlist, self-repo 제외, GitHub
      check-runs green gate를 모두 통과할 때만 GitHub merge API로 병합하고
      Issue를 `devbot:done`으로 전이한다. gate 실패는 라벨을 유지하고
      로그/댓글로 이유를 남겨 사람 머지 경로를 보존한다.
- [x] Task 028: CLI version command. `devbot --version`이 패키지
      metadata의 단일 버전 소스에서 값을 읽고, `.env`/repository config,
      daemon lock, GitHub client, polling service, Agent runner 초기화 없이
      즉시 출력 후 종료한다(`results/028-cli-version-command.md`).
- [x] Task 029: prepared workspace validation environment. validation helper가
      PreparedWorkspace cwd에서 literal Validation Gate 명령을 실행하고,
      workspace `.venv/bin`을 우선하며 host checkout `VIRTUAL_ENV` fallback을
      제거한다. command/workspace/exit code evidence와 환경 준비, dependency
      network, command, forbidden fallback failure category를 기록한다
      (`results/029-prepared-workspace-validation.md`).
- [x] Task 030: external dependency reliability. GitHub API 일시적 실패
      (5xx/429/네트워크 오류)와 Agent 대화형 승인 요구가 Task 상태를 잘못
      전이시키지 않도록 분류별 재시도(`github_retry.py`)와 명시적 예외
      처리를 도입한다(`results/030-github-api-transient-retry.md`).
- [x] Task 031: agent execution environment. implementer/reviewer 역할이
      Local과 VPS 환경 모두에서 동일하게 명시적이고 관찰 가능한 실행
      Context를 받도록 표준화한다(`src/devbot/agent_execution.py`,
      `results/031-agent-execution-environment.md`).
- [x] Task 032: automated release pipeline. `main`에 도달한 자격 있는
      변경 이후 검증된 stable GitHub Release를 자동 생성한다. 단일
      버전 소스, merged PR `release:*` 라벨 기반 버전 정책, 플랫폼별
      아티팩트, SHA-256 체크섬 매니페스트, draft-first 원자적 게시,
      재실행 멱등성, 최소 권한, 수동 `workflow_dispatch` 복구 경로를
      포함한다(`src/devbot/release.py`, `.github/workflows/release.yml`,
      `results/032-automated-release-pipeline.md`).
- [x] Task 033: bootstrap initial release and project history. 검증된
      `main` 커밋에서 최초 stable Release(`v0.1.0`)를 부트스트랩하고,
      `docs/history.md`에 Release Notes 형식과 Task 000-032 개발
      마일스톤을 기록한다(`results/033-bootstrap-initial-release-and-project-history.md`).
- [x] Task 034: self-contained portable release artifact. 패키징된 CLI가
      호스트에 이미 설치된 Python 패키지에 의존하지 않도록, 아티팩트에
      잠긴(uv.lock) 런타임 의존성을 vendoring한다
      (`results/034-self-contained-portable-artifact.md`).
- [x] Task 035: release smoke uses managed Python path. Release smoke
      단계가 `astral-sh/setup-uv`가 설치한 Python 3.13 실행 파일을
      사용하도록 고쳐, 호스트 Python 유무와 무관하게 아티팩트를
      검증한다(`results/035-release-smoke-managed-python-path.md`).
- [x] Task 036: release tag Git identity. v0.1.0 Release workflow가
      `Create immutable tag` 단계에서 Git committer identity 부재로 실패한
      문제를 닫는다. Annotated tag 생성 전에 `github-actions[bot]` identity를
      local Git config로 설정하고, `git tag --annotate`보다 먼저 실행되는지
      workflow regression test로 고정했다
      (`results/036-release-tag-git-identity.md`).
- [x] Task 037: release operator UX. 운영자의 개입을 "다음 stable
      release를 게시해줘" 한 문장으로 줄인다. `devbot release
      preview|publish|status` CLI가 최신 검증된 `main` 커밋과 다음
      semantic version을 자동 결정하고, merged PR/Task 계약/Result
      문서만을 근거로 한국어+영어 병기 Release Notes를 생성하며,
      기존 `.github/workflows/release.yml`을 `workflow_dispatch`로
      dispatch하고 완료까지 기다린 뒤 tag/Release/자산/`SHA256SUMS`를
      검증한다. main dirty, CI 미검증, 기존 Release 존재, 라벨 누락,
      자산/체크섬 불일치, 빈 Release Notes 중 하나라도 있으면 게시를
      거부하며, 태그를 직접 옮기거나 워크플로를 우회하지 않는다
      (`src/devbot/release_ops.py`, `results/037-release-operator-ux.md`).
- [x] Task 038: goal-based planning (Operator Planner). 운영자가 매 Task를
      직접 결정하지 않아도 되도록, "Publish the next stable release." 같은
      상위 Goal 문장을 받아 `devbot goal plan "<goal>"`이 로드맵/완료된
      Task/열린 Issue-PR과 비교해 작업이 필요 없는지, Task 하나로
      충분한지, 순차적인 여러 Task가 필요한지를 결정론적으로 계산한다.
      계획은 오직 손으로 정리한 근거 기반 capability catalog
      (`src/devbot/goal_planner.py`)와 실제 로드맵/Issue/PR 텍스트에서만
      나오며, 지어낸 기능을 만들지 않는다. Goal이 안전하게 계획할 만큼
      명확하지 않으면 `ambiguous`로 fail closed하고, 이미 끝났거나
      진행 중인 작업은 새로 계획하지 않는다. 계획된 Task의 Issue/Branch/
      Contract/PR을 직접 생성하거나 실행하지 않는다(읽기 전용). 영어와
      한국어 Goal을 모두 1급 입력으로 지원하며(예: "셀프 업데이트 기능을
      구현해"), 한국어도 동일한 고정 catalog/문자열 매칭 방식만 쓰고 번역이나
      자유 생성은 하지 않는다(PR #82 리뷰 반영)
      (`results/038-goal-based-planning.md`).
- [x] Task 039: release operator main CI validation fix. `devbot release
      preview`가 실제로 성공한 main push CI를 "검증 안 됨"으로 잘못
      보고하던 버그를 닫는다. 근본 원인은 Check Runs API가 같은 커밋에
      대한 **모든** 워크플로(설정된 CI 워크플로뿐 아니라 push마다 함께
      트리거되는 `release.yml` 자신의 job들까지)의 check run을 섞어
      반환해, 무관한 워크플로의 실패/skip이 전체 판정을 오염시킨 것이다.
      Check Runs 집계 대신 GitHub Actions Workflow Runs API로 설정된 CI
      워크플로만, 정확한 head SHA와 `push` 이벤트, `completed`/`success`
      조건을 모두 만족하는 실행이 있는지 확인하도록 바꿨다. PR Check Run을
      머지 커밋에 요구하지 않으며, 매칭 없음/큐잉/진행 중/실패/취소/다른
      커밋의 성공/pre-merge PR 실행/API 인증 실패 모두 fail closed를
      유지한다(`src/devbot/release_ops.py`,
      `results/039-release-main-ci-validation.md`).
- [x] Task 040: goal executor. Task 038의 읽기 전용 Goal Planner를 확장해,
      운영자가 검증된 계획에서 승인된 Task 하나만 정상 DevBot 워크플로로
      구체화할 수 있게 한다(`devbot goal execute "<goal>" [--task <order>]
      [--confirm] [--dry-run]`). Task 038의 `fetch_goal_plan`을 정확히 한
      번만 호출하고 계획을 다시 계산하거나 재해석하지 않는다. `--confirm`
      없이는 아무것도 쓰지 않고, 실행 가능한 decision(`single_task`/
      `multi_task`)에서만, multi-task는 의존성 순서상 아직 구체화되지
      않은 첫 Task만 진행한다. `--confirm` 시 정확히 GitHub Issue 1개,
      최신 CI 검증된 `origin/main`에서 분기한 Branch 1개, 초안 Contract
      파일 1개만 생성한다 - PR 생성이나 구현 Agent 호출은 절대 하지
      않는다. Issue 존재 여부로 멱등성을 보장해 부분 실패 후 재시도해도
      중복 생성하지 않는다(`src/devbot/goal_executor.py`,
      `results/040-goal-executor.md`).
- [x] Task 041: role dispatch and agent registry. 직접 Agent 이름을
      dispatch하던 방식을 Role 기반 dispatch로 바꾼다 - DevBot은
      "implementer"/"reviewer" 같은 Role만 알고, 실제 Claude/Codex/GPT/
      Gemini 이름은 절대 dispatch 호출부에 하드코딩하지 않는다. 새
      `src/devbot/agent_registry.py`가 Role/Capability/Agent(레지스트리
      항목)/Router(`resolve_agent`, priority routing만 구현, 동점은 id
      오름차순으로 결정론적 처리)를 분리한다. 선택적
      `config/agents.yaml`이 없으면 기존 `IMPLEMENTER_AGENT`/
      `REVIEWER_AGENT`/`DEFAULT_AGENT` 설정에서 레지스트리를 그대로
      합성해, 파일이 없는 모든 기존 배포는 이 Task 이전과 완전히 동일하게
      dispatch된다(`test_daemon_dispatch_backend_unchanged_without_agents_
      registry_file`로 고정). `devbot.agents.build_agent_runner`(실행
      백엔드)는 전혀 변경하지 않았다. 읽기 전용 `devbot role list`/
      `devbot role resolve <role>`/`devbot agent list`/`devbot goal
      dispatch`(Task 040의 `goal execute`와 동일하지만 Role→Agent 해석을
      함께 보여줌, Agent를 실제로 호출하지 않음)를 추가했다. Admin UI/
      Auto routing/capability scoring/비용 최적화/부하 분산/다중 Agent
      dispatch는 이번에 구현하지 않고, 나중에 API를 바꾸지 않고 추가할 수
      있는 추상화만 만들었다(`src/devbot/agent_registry.py`,
      `results/041-role-dispatch.md`).
- [x] Task 042: specification generator. Goal → Planner → Issue → Contract
      워크플로 뒤에 **Specification** 단계를 추가한다 - 향후 Agent
      Dispatch가 수동 프롬프트 대신 참고할 산출물이다. 새
      `src/devbot/specification.py`가 정확히 하나의 `tasks/NNN-*.md`
      Contract, 제목이 정확히 일치하는 GitHub Issue(`Task NNN: <제목>`),
      선택적으로 `docs/00-roadmap.md` 항목만 근거로 삼아
      Overview/Functional Requirements/Technical Design/Validation/
      Safety/Completion/Handoff 7개 최상위 섹션을 결정적으로 렌더링한다
      - 같은 입력이면 몇 번을 실행하든 바이트 단위로 동일한 출력을
      만든다. Contract 헤딩이 영어/한국어로 제각각인 41개 기존 Task를
      다루기 위해 최선-노력 bilingual alias 테이블을 쓰되, alias가
      매칭하지 못한 개념은 절대 지어내지 않고
      `"Not specified in the Task Contract."`로 채우며, Contract 원문
      전체를 Specification 맨 끝에 그대로 붙여 넣어 정보 손실을 막는다.
      Contract 없음/여러 Contract 매칭/Issue 없음/Issue 여러 개 매칭
      또는 제목 불일치/Planner 근거(Goal/Background/Scope/Functional
      Requirements) 없음/유효하지 않은 Task 번호 여섯 가지 모두 전용
      예외로 fail closed 처리한다. 읽기 전용
      `devbot specification show --task <N>`과 파일에 쓰는
      `devbot specification generate --task <N>`(`--dry-run` 시
      show와 동일하게 쓰지 않음)을 추가했다 - 어느 쪽도 daemon lock을
      잡지 않는다. Dispatch 쪽 코드(`devbot.workspace`/`devbot.agents`/
      `devbot.polling`/`devbot.review`/`devbot.rework`)는 이번에 전혀
      바꾸지 않았고 어떤 Agent도 호출하지 않는다(`src/devbot/
      specification.py`, `results/042-specification-generator.md`).
- [x] Task 043: specification validator. Goal → Planner → Issue → Contract →
      Specification 뒤에 **Validation** 단계를 추가한다 - 향후 Workflow
      Engine이 Dispatch 전에 통과해야 하는 품질 게이트다. 새
      `src/devbot/specification_validation.py`가 Task 042의
      `REQUIRED_TOP_LEVEL_SECTIONS`(이번에 `# Full Task Contract
      Reference`를 8번째 항목으로 확장해 두 모듈이 같은 스키마를 공유하게
      했다)와 `fenced_code_ranges`(기존 `_fenced_code_ranges`를 공개
      helper로 승격)를 그대로 재사용해 별도의 호환되지 않는 스키마를
      만들지 않는다. 안정적인 규칙 코드 13개(SPV-001~013)로 제목/
      Provenance/필수 최상위 섹션과 서브섹션 존재·중복·순서/본문 비어
      있음(canonical fallback은 내용으로 인정)/Acceptance Criteria 형식
      (checkpoint/checklist/목록 중 하나 필요, 중복 checkpoint id
      거부)/Validation Commands(실행 명령 또는 명시적 미지정)/Safety
      경계/Contract 전문 인용(빈 값이나 경로만 있는 참조 거부)/미해결
      TODO·TBD·FIXME·XXX·`{{...}}` 마커/결정론적 이슈 정렬(줄→코드→
      메시지)을 검사한다 - 에러만 `passed=False`를 만들고 경고는 검증을
      절대 실패시키지 않는다. 라이브 검증 중 실제 버그를 하나 더
      발견했다: `# Full Task Contract Reference`가 원본 Contract를
      3-backtick 코드 펜스로 감싸는데, Contract 자신이 같은 펜스를
      내부에 또 쓰면(중첩 불가) 펜스 추적이 깨져 안쪽 예시 헤딩이 바깥
      문서의 진짜 섹션처럼 오검출됐다 - 이 섹션이 시작되면 그 뒤 전체를
      더 이상 문서 구조로 재해석하지 않도록 고쳤다. 읽기
      전용 `devbot specification validate --task <N>`(`--format json`
      지원)을 추가했다 - GitHub API를 전혀 호출하지 않고 로컬
      `specifications/NNN-*.md`만 읽으며, 통과 시 종료 코드 0, 문서
      검증 실패 시 1, Task 번호 오류·파일 없음·모호함·읽기 실패 같은
      운영 오류는 2로 구분한다. daemon lock을 잡지 않으며, Dispatch/
      Role Dispatch/polling/review/rework/Task 044 템플릿 생성/Task 045
      릴리스 분류/code-to-Spec 검증은 이번에 전혀 구현하지 않았다
      (`src/devbot/specification_validation.py`,
      `results/043-specification-validator.md`).

- [x] Task 044: specification template engine. Specification Generator가
      explicit `## Specification Type` 또는 `--template` override로 여섯
      built-in template(`feature`/`bugfix`/`refactor`/`docs`/`internal`/
      `generic`)을 결정론적으로 선택한다. Template은 canonical Task 043
      schema를 바꾸지 않는 policy overlay이며, 선택된 template ID와 source를
      Provenance에 기록한다. `devbot specification templates`와
      `devbot specification template show --template <id>`는 read-only
      inspection command로 제공된다. 여섯 template 출력 모두 Task 043
      Validator를 통과했다(`results/044-specification-template-engine.md`).
- [x] Task 045: contract schema. Task Contract가 관례가 아니라 형식적으로
      정의된 스키마를 갖도록 **Contract Schema v1**을 확정한다 - 향후
      Metadata Parser/정책 엔진/워크플로 소비자가 의존할 안정된 경계다.
      13개 normative 영역(Contract Version/Provenance/Task Identity/
      Metadata/Goal/Context/Scope/Out of Scope/Deliverables/Acceptance
      Criteria/Quality Gates/Handoff/References, References만 선택)의
      필수 여부·cardinality·의미를 명시하고, Metadata의 5개 필드
      (`specification_type`/`release_impact`/`risk_level`/`compatibility`/
      `migration`, Task 044의 `specification_type` 여섯 값과 일치)에
      canonical lowercase enum 값을 고정한다. `contract_version` 기반
      버전 관리 규칙(하위 호환 변경은 버전을 올리지 않고, 필수 필드
      추가·필드 제거/재정의·enum 값 제거/재정의·cardinality 변경·
      canonical 표현 변경은 breaking으로 새 정수 버전을 요구)을
      정의한다. `Contract Version`을 선언하지 않은 기존 44개 Task
      Contract 전부(001/022/037/042/044로 실제 확인)는 강제 마이그레이션
      없이 legacy로 분류된다. `docs/09-task-contract-standard.md`의 기존
      필수 항목 14개(Risk/Rollback Strategy/Reviewer Focus/Definition of
      Done 등)와 Schema v1의 13개 영역이 서로 다른 관심사(운영 완성도 대
      정체성·메타데이터·버전)를 다루는 상호 보완 관계임을 검증하고 양쪽
      문서에 상호 참조를 추가했다. `specifications/045-contract-schema.md`
      자신도 Task 042/043의 canonical Specification 스키마를 만족하도록
      재구성해 `devbot specification validate --task 45`를 통과한다.
      Metadata Parser/Domain Object/Release Recommendation/CLI/Review
      Loop/Workflow Engine/Release Automation은 이번에 전혀 구현하지
      않았다(`specifications/045-contract-schema.md`,
      `results/045-contract-schema.md`).
- [x] Task 046: contract metadata engine. Task 045의 Contract Schema v1
      위에 실제 런타임 파서를 구현한다 - 새 `src/devbot/
      contract_metadata.py`가 `parse_contract_metadata(text) ->
      ContractParseResult` 하나로 Task Contract Markdown을 결정론적으로
      타입화된 메타데이터로 파싱하는 유일한 컴포넌트다. `## Contract
      Version`이 없는 Contract는 명시적으로 legacy로 분류되고(에러
      아님, 강제 마이그레이션 없음), 일단 `Contract Version` 섹션이
      존재하면 그 이후 잘못된/미지원 내용은 legacy로 조용히 되돌아가지
      않고 항상 fail closed한다(미지원 버전/형식이 아닌 버전/필수 섹션
      누락·중복/필수 Metadata 필드 누락·중복/알 수 없는 필드/잘못된
      enum 값/Task Identity 불일치 9종 전용 예외). `x-` 접두사 확장
      필드는 별도의 불변 매핑에 보존되며 5개 핵심 필드 값에 절대 영향을
      주지 않는다(예: `x-specification_type`가 실제 `specification_type`
      값을 절대 덮어쓰지 못함을 테스트로 고정). CLI/정책/Release
      Recommendation/Review Loop/Workflow Engine은 이번에 전혀 구현하지
      않았다. `specifications/046-contract-metadata-engine.md`도 Task
      042/043의 canonical Specification 스키마를 만족하도록 재구성해
      (Task 045와 같은 패턴) `devbot specification validate --task 46`을
      통과한다. 실제 저장소의 Task 046 자신의 Contract(Schema v1)와
      6개의 legacy Contract(001/022/037/042/044/045)를 대상으로 라이브
      검증했다(`src/devbot/contract_metadata.py`,
      `results/046-contract-metadata-engine.md`).
- [x] Task 047: release classification policy. Task 046의 typed
      `ContractParseResult`를 받아 `major`/`minor`/`patch`/`none` 릴리스
      추천을 결정론적으로 계산하는 순수 정책 계층 `src/devbot/
      release_classification.py`를 추가한다 - Markdown을 다시 읽지
      않고, 버전 파일/Git 태그/GitHub Release/릴리스 노트 등 어떤
      외부 상태도 건드리지 않는다. 우선순위(높은 순): legacy Contract
      -> 전용 에러(추측하지 않음), metadata 없는 native 결과 -> 전용
      에러, `compatibility=breaking` -> `major`, `migration=required`
      -> `major`, 그 다음에야 `release_impact`
      매핑(breaking->major/feature->minor/fix->patch/docs·internal·
      none->none)을 적용한다. `risk_level`/`specification_type`은
      의도적으로 절대 참조하지 않는다 - 코드 자체가 이 두 필드를
      읽지 않으므로 결과에 영향을 줄 수 없음을 파라미터화 테스트
      18개 조합으로 직접 증명했다. `specifications/047-*.md`는 이미
      canonical 8-섹션 구조로 작성돼 있었고(Task 045/046 리뷰에서
      확립된 패턴을 그대로 따름), Provenance 생성 출처 누락과
      `Validation Commands` 서브섹션 누락 2건만 고쳐
      `devbot specification validate --task 47`을 통과시켰다. 실제
      저장소의 Task 047 Contract(내부용, `none` 추천)와 legacy
      Contract(Task 037, 에러)를 대상으로 end-to-end 라이브 검증했다.
      CLI/버전 변경/태그·Release 생성/릴리스 노트/여러 Task 통합
      추천은 이번에 전혀 구현하지 않았다(`src/devbot/
      release_classification.py`,
      `results/047-release-classification-policy.md`).
