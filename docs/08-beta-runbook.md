# Beta Runbook

Task 009 wires Task 001-008's pieces into one flow. Task 010 adds PR
feedback rework: polled `@devbot` PR feedback reworks the existing
branch/PR automatically. Task 011 adds a second Agent role
(reviewer) alongside the implementer. Task 012 turns all of this into a
per-repository job scheduler (`PollingService.run_cycle()`, see
`src/devbot/polling.py`) that also actually *runs* the reviewer role, so
the full ready -> review -> merge relay needs no manual trigger at any
step except the final Merge. Task 013 adds structured operational
logging (`src/devbot/observability.py`) so an operator can answer "why did
(or didn't) the daemon pick up this Issue" from logs alone. Task 014 adds
the explicit `devbot:rework` state and hardens failure recovery so no
in-process failure path should leave an Issue permanently stuck in
`devbot:working` - a crashed *process* (killed, OOM, host reboot) still
could, until CP-B1 (2026-07-18) added a startup sweep that self-heals this
on the next daemon restart (`docs/03-state-machine.md`'s "Startup
Recovery"). This is a
manual walkthrough for confirming the flow against a real target
repository, plus the operational checklist for running DevBot beyond a
single smoke test.

## Full flow (per repository, per cycle)

Each enabled repository independently contributes at most one candidate
job per cycle; `devbot.scheduler.select_jobs` then picks up to
`MAX_CONCURRENT_JOBS` of them (default `1` — the same serial behavior as
every earlier Task), never two for the same repository:

```text
Any devbot:working Issue in this repository? --yes--> no candidate this cycle

no
 |
 v
Any devbot:rework Issue in this repository?
 |
 +--yes--> linked PR has an unprocessed @devbot comment?
 |          |
 |          +--yes--> REWORK job (reuses the EXISTING branch/PR, reacts
 |          |          "eyes" when done) -> devbot:review -> reviewer
 |          |          re-reviews the new head
 |          |
 |          +--no --> no candidate this cycle; remains devbot:rework
 |
no rework Issue in this repository
 |
 v
Any devbot:review Issue in this repository?
 |
 +--yes--> linked PR's current head SHA has no auto-review marker yet?
 |          |
 |          +--yes--> REVIEW job:
 |          |          reviewer_runner runs, posts `# Review Summary` +
 |          |          `<!-- devbot:auto-review head=... -->` marker.
 |          |            REQUEST CHANGES -> posted comment contains
 |          |            "@devbot" and Issue becomes devbot:rework.
 |          |            MERGE READY -> stays devbot:review, waiting on
 |          |            a human Merge.
 |          |
 |          +--no --> fully caught up, no candidate this cycle
 |
 |  (a devbot:rework or devbot:review Issue in this repository always
 |   blocks a fresh `ready` implementation from starting in the SAME
 |   repository this cycle)
 |
 no review Issue in this repository
 |
 v
ready --(claim)--> working --(implementer_runner)--> [agent output]
                                    |
                                    v
                          run_verification_commands
                       (uv run ruff check . && uv run pytest,
                        in the target repository)
                                    |
                       passed? -----+----- failed?
                          |                   |
                          v                   v
                commit -> push -> PR    block (+ comment)
                          |
                          v
                  mark_for_review (-> review)
```

`state_writer`, `delivery`, `rework_service`, and `review_service` are
optional constructor arguments on `PollingService`. `devbot.main` always
supplies all four in production; any subset being omitted falls back to
progressively earlier Task behavior (down to Task 005's select + run the
agent only) - this is what every earlier Task's tests still exercise,
unchanged. Automatic Merge and automatic Issue Close are never performed
by any of this - `MERGE READY` always waits for a human.

## Manual dry-run walkthrough

This reproduces what `results/009-beta-smoke-test.md` records: a real
local Git + `uv` project as the "target repository", a stubbed `ready`
Issue (no real GitHub Issue needed), and `--dry-run` so nothing writes to
GitHub or pushes anywhere.

```bash
# 1. Build a minimal target repository DevBot can run verification in.
mkdir -p /tmp/devbot-smoke/workspace/myrepo && cd /tmp/devbot-smoke/workspace/myrepo
git init -q && git config user.email a@b.c && git config user.name test
cat > pyproject.toml <<'EOF'
[project]
name = "smoke-target"
version = "0.1.0"
requires-python = ">=3.13"

[tool.uv]
package = false

[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.6"]
EOF
mkdir tests && echo 'def test_ok() -> None:\n    assert True' > tests/test_dummy.py
uv sync -q && git add -A && git commit -q -m init

# 2. Point DevBot at it.
cd /path/to/devbot
cat > /tmp/devbot-smoke/.env <<EOF
WORKSPACE_ROOT=/tmp/devbot-smoke/workspace
GITHUB_TOKEN=fake-token-for-smoke-test
EOF
cat > /tmp/devbot-smoke/repositories.yaml <<EOF
repositories:
  - owner: someone
    repo: myrepo
    enabled: true
EOF

# 3. Run --once --dry-run with GitHubClient.list_issues stubbed to return
#    one devbot:ready Issue (see results/009-beta-smoke-test.md for the
#    exact Python snippet used).
```

Expected log output: Issue selected -> `AgentRunner 실행: ... dry_run=True`
-> `Delivery 시작: branch=...` -> real verification runs in
`/tmp/devbot-smoke/workspace/myrepo` and passes -> `Delivery 결과:
[dry-run] verification passed; no commit, push, or PR` -> exit code 0. No
label, comment, commit, push, or PR ever touches GitHub or the target
repo's Git history.

## Operational checklist

- [ ] `WORKSPACE_ROOT` points at a directory containing one checked-out
      clone per managed repository (`WORKSPACE_ROOT/<repo>`), each a clean
      Git working tree on a branch DevBot may commit to.
- [ ] Every managed repository is itself a `uv`-managed Python project
      where `uv run ruff check .` and `uv run pytest` both succeed on a
      clean checkout — `delivery.py`'s verification commands are
      hardcoded, not per-repository configurable (see "Known limitations"
      in `results/009-beta-smoke-test.md`).
- [ ] `GITHUB_TOKEN` has write access (labels, comments, PRs) on every
      managed repository, not just read access.
- [ ] `config/repositories.yaml` lists only repositories DevBot is
      authorized to write to and open PRs against; double-check
      `default_branch` per repository if it isn't `main`.
- [ ] `DEVBOT_LOCK_FILE` points at a path only this DevBot process uses,
      to prevent two instances from claiming the same Issue concurrently.
- [ ] Start with `DRY_RUN=true` (or `--dry-run`) in any new environment
      and confirm the full flow's log output looks right before flipping
      to `DRY_RUN=false`.
- [ ] `rework`-state Issues are polled every iteration for unprocessed
      `@devbot` PR comments, and `review`-state Issues are polled for an
      unreviewed PR head commit — no manual trigger needed for either.
      Each *repository* contributes at most one candidate per cycle
      (rework outranks review outranks a fresh `ready`
      implementation); a repository with multiple eligible Issues defers
      the rest to the next cycle, but a slow/stuck repository no longer
      blocks other repositories the way the pre-Task-012 global gate did.
- [ ] `MAX_CONCURRENT_JOBS` (default `1`) bounds how many *different*
      repositories' jobs run in the same cycle; leave it at `1` unless
      you've confirmed every managed repository has its own independent
      local workspace and Git remote credentials — two jobs never run for
      the *same* repository regardless of this value.
- [ ] If a `devbot:review` Issue looks stuck (no new auto-review comment
      appears across several polls), check the PR for a
      `<!-- devbot:auto-review head=... -->` marker matching its *current*
      head SHA — if one is missing/mismatched, the reviewer role should
      pick it up on the next cycle; if the Issue instead shows
      `devbot:blocked`, the blocking comment names the reviewer failure
      (missing/failed Agent CLI, or a Review Summary that didn't contain
      exactly one of `MERGE READY`/`REQUEST CHANGES`).
- [ ] If a `devbot:rework` Issue looks stuck, check the linked PR for an
      unprocessed `@devbot` comment. A processed comment has an `eyes`
      reaction. If no unprocessed comment exists, either add the missing
      feedback comment or manually move the Issue back to `devbot:review`
      after confirming the PR head is ready for review.
- [ ] If an Issue is `devbot:manual-action`, the latest review/rework
      comment asked for GitHub metadata work, external verification, or
      human approval that DevBot intentionally did not route through
      commit/push - or (CP-B1) DevBot could not resolve the Issue's linked
      PR (closed/merged outside DevBot, or the Issue was reopened after its
      PR was already resolved). Complete the named action or re-link/verify
      the PR, then move the Issue back to the appropriate stable state
      (`devbot:review`, `devbot:rework`, or `devbot:ready`).
- [ ] If an Issue is `devbot:blocked`, read the blocking comment first.
      After manual remediation, remove `devbot:blocked` and add the
      appropriate stable label (`devbot:ready`, `devbot:review`, or
      `devbot:rework`) based on the point where the workflow should resume.
      A `devbot:working` Issue left behind by a crashed process is also
      swept to `devbot:blocked` automatically on the next daemon startup
      (CP-B1) - not the next poll cycle of an already-running daemon.

## 운영 진단 절차 (Task 013)

데몬이 "왜" 특정 Issue를 선택했는지, 혹은 선택하지 않았는지 로그만으로
판단하는 절차다. 기본 로그 수준은 `INFO`이며, 상세 근거가 필요하면
`LOG_LEVEL=DEBUG`(영구 설정) 또는 `--verbose`(이번 실행에만 적용)를 켠다.

1. **시작 로그 확인**: 데몬을 시작하면 한 번 `DevBot 시작: version=...
   implementer=... reviewer=... dry_run=... poll_interval_seconds=...
   max_concurrent_jobs=... log_level=... 관리 저장소 수=...`와, 관리 저장소
   각각에 대해 `관리 저장소: <owner>/<repo> local_path=... default_branch=...`
   가 출력된다. 여기서 역할 배정(`implementer`/`reviewer`), `dry_run`
   여부, 관리 저장소 목록이 기대한 값과 일치하는지 먼저 확인한다.
2. **관리 저장소가 0개인 경우**: `no_managed_repositories: 관리 저장소가
   0개라 이번 cycle을 건너뜁니다...` 로그가 보이면 `config/repositories.yaml`
   또는 각 repository의 `enabled` 설정을 확인한다. 이 상태는 "선택 가능한
   ready Issue가 없음"(`no_ready_task`)과 로그 문구·상태 코드가 다르게
   구분되므로 혼동하지 않는다.
3. **cycle 단위로 추적**: 매 cycle은 `cycle 시작: cycle_id=...`로 시작해
   `cycle 종료: cycle_id=... 소요=...ms 후보(rework=... review=...
   implement=...) 선택=.../... 결과=...`로 끝난다. 같은 `cycle_id`를 가진
   로그 줄을 모으면 그 cycle에서 실제로 무엇을 검색하고, 무엇을 후보로
   만들고, 무엇을 선택해 실행했는지 전부 재구성할 수 있다. Task 020부터는
   이 사이에 **Queue Summary**(`ready`/`review`/`rework`/`blocked`/
   `manual-action`/`working` 6개 상태 전체 개수, cycle당 정확히 한 번),
   Job이 선택된 경우에만 나오는 **Selected**(`repo`/`issue`/`pr`/
   `job_type`), 그리고 **Cycle Result**(`NO_RUNNABLE_TASK` 또는
   `IMPLEMENT`/`REVIEW`/`REWORK` 또는 실패 분류 코드, 대문자 정규화된 값
   하나)가 순서대로 출력된다 - "이 cycle의 큐 상태", "무엇을 골랐는지",
   "결과가 무엇인지"를 서로 다른 세 줄로 분리해서 보는 것이 목적이다
   (README.md "Queue Summary / Selected / Cycle Result (Task 020)" 절 참고).
   기존 `cycle 시작`/`cycle 종료` 구조화 로그와 필드는 그대로 남아있다.
4. **저장소별 검색 조건 확인 (DEBUG)**: `저장소 검색: cycle_id=... repo=...
   state=open label_filter=devbot:*(client-side) 결과 수=...`로 GitHub에
   실제로 어떤 조건을 보냈고 몇 건이 돌아왔는지 확인한다. DevBot은 라벨로
   서버 측 검색을 하지 않고 `state=open`으로 전체를 가져온 뒤
   `devbot:*` 라벨을 클라이언트에서 걸러낸다.
5. **후보 제외 사유 확인 (DEBUG)**: `후보 제외: cycle_id=... repo=...
   issue=#... job_type=... reason=... detail=...`의 `reason`은 항상 다음
   중 하나로 고정된 코드다.

   | reason | 의미 |
   |---|---|
   | `repository_busy` | 같은 저장소에 `devbot:working` Issue가 있어 이번 cycle에 후보를 만들지 않음 |
   | `issue_busy` | 같은 저장소의 `devbot:review` Issue가 워크스페이스를 점유해 `ready` Issue가 대기 |
   | `concurrency_limit` | `MAX_CONCURRENT_JOBS` slot이 이미 다 찼음 |
   | `missing_linked_pr` | `review` Issue에 연결된(Closes #N) 열린 PR을 찾지 못함 |
   | `missing_pr_head` | 연결된 PR의 head commit을 확인할 수 없음 (예약된 코드, 현재 경로에서는 발생하지 않음) |
   | `already_reviewed_head` | PR의 현재 head SHA가 이미 자동 리뷰 marker를 가짐 |
   | `no_unprocessed_feedback` | 연결된 PR에 처리되지 않은 `@devbot` 댓글이 없음 |
   | `not_ready` | Issue 상태가 `devbot:blocked`/`devbot:done`처럼 이번 cycle의 스케줄 대상이 아님 |
   | `lower_priority` | 같은 저장소에서 더 높은 순위(REWORK>REVIEW>IMPLEMENT, 우선순위, 나이, Issue 번호) 후보가 이미 선택됨 |
   | `dry_run` | 예약된 코드 (Task 013 시점에는 실제로 발생하지 않음) |

6. **선택된 Job과 실행 시간 확인**: 후보 제외 로그 뒤에, 이번 cycle에
   실제로 선택된 Job이 있으면 `Selected\n  repo     : ...\n  issue    :
   #...\n  pr       : ...\n  job_type : ...` 블록이 (선택된 Job마다 한 번)
   출력되고, 이어서 `Job 시작`/`Job 종료`가 나온다. `Job 종료` 줄의
   `소요=...ms`가 총 소요 시간이고, DEBUG에서는 `단계 완료: ...
   stage=workspace_validate|agent_execution|delivery|rework_process|
   review_process 소요=...ms`로 단계별 소요 시간도 확인할 수 있다.
   Job이 하나도 선택되지 않은 cycle(`Cycle Result`가
   `NO_RUNNABLE_TASK`)에는 `Selected` 블록 자체가 나오지 않는다.
7. **실패 원인 확인**: Job이 실패(`workspace_invalid`/`agent_failed`/
   `blocked`/`iteration_error`)로 끝나면 `Job 종료` 바로 뒤에 `Job 실패
   요약: ...`이 ERROR 수준으로 남는다. Agent의 원본 stdout/stderr 전체는
   INFO에 출력되지 않고, 이 요약도 Secret/Token/Authorization 값은 항상
   `***`로 치환된다. cycle 맨 끝의 `Cycle Result`도 이 실패를
   `AGENT_EXECUTION_FAILED`/`DELIVERY_FAILED`/`REVIEW_FAILED`/
   `GITHUB_API_ERROR` 등 같은 `FailureCategory` 코드로 요약해서 보여준다 -
   `Job 실패 요약`을 못 봤어도 `Cycle Result` 한 줄만으로 실패 여부와
   대략적인 원인 분류를 알 수 있다.

`--verbose`는 `.env`나 실제 프로세스 환경을 변경하지 않고 그 실행에만
적용된다. 운영 중인 데몬을 잠시 더 자세히 보고 싶을 때는 별도로 `--once
--verbose`를 실행해 한 cycle만 상세 로그로 확인하는 편이 연속 실행 중인
데몬의 `LOG_LEVEL`을 바꾸는 것보다 안전하다.

## 시작 전/장애 진단 절차 (Task 019)

데몬을 시작하기 전, 또는 이상 동작을 의심할 때는 먼저 아래를 실행한다.

```bash
uv run devbot doctor
```

읽기 전용이며 이미 실행 중인 데몬 옆에서도 안전하게 실행할 수 있다
(GitHub에 쓰지 않고, daemon lock을 실제로 잡지 않는다 - probe 후 즉시
release). 관리 저장소 목록, 저장소별 워크스페이스 상태(clean/dirty)와
현재 branch, GitHub 연결/인증, daemon lock 점유 여부, 구성된
implementer/reviewer, `safe_to_start` 여부를 한 번에 보여준다. 세부 항목과
"무엇이 fatal인지"는 `docs/11-daemon-reliability.md` 4~5절을 참고한다.

데몬이 시작되면 startup 로그 바로 뒤에 같은 검사가 다시 한번
`시작 검증: name=... ok=... detail=...`로 기록된다 - 다만 이 로그는 항상
WARNING 수준일 뿐 fatal이 아니다(워크스페이스가 아직 clone되지 않았거나
dirty해도 데몬은 계속 시작한다). Job 실행 중 실패가 발생하면 `Job 실패
요약` 바로 뒤에 `[DevBot Diagnostic Report]`로 시작하는 진단 보고서가
ERROR 수준으로 남는다 - 실패 분류(`failure_category`), 재시도 여부, 복구
권장 조치가 함께 나온다(`docs/11-daemon-reliability.md` 1~3, 6절).

Agent 세션/사용량 제한으로 실패한 경우, 블로킹 댓글에
`[failure_category=agent_session_limit]`와 함께 "제한 해제 후 이전
상태로 되돌리라"는 안내가 붙는다 - 자동 재시도를 하지 않으므로 반드시
사람이 확인 후 Issue를 되돌려야 한다.

## 릴리스 운영 절차 (Task 037)

DevBot 자체 stable Release를 다루는 운영자의 전체 상호작용은 다음 한
문장으로 줄어든다: "다음 stable release를 게시해줘." 버전 번호, 커밋
SHA, Release Notes를 사람이 직접 정하지 않는다 - `devbot release`
하위 명령이 GitHub 상태에서 전부 계산한다. 세 명령 모두
`config/repositories.yaml`의 단일 enabled 저장소를 기본 대상으로
하며, 여러 저장소를 관리하는 배포에서는 `--repo owner/repo`로 대상을
지정한다. 세 명령 모두 daemon lock을 잡지 않으므로 실행 중인 데몬
옆에서 안전하게 실행할 수 있다(`doctor`/`timeline`/`worktree`와 동일한
정책).

```bash
# 1. 무엇이 게시될지 미리 본다 (GitHub에 아무것도 쓰지 않는다).
uv run devbot release preview

# 2. 준비됐으면 실제로 게시한다: 버전/커밋/Release Notes를 자동으로
#    결정하고 기존 .github/workflows/release.yml을 workflow_dispatch로
#    실행한 뒤 완료까지 기다리고 결과를 검증한다.
uv run devbot release publish

# 계산된 계획만 보고 워크플로는 실행하지 않으려면:
uv run devbot release publish --dry-run

# 3. 최근 게시 상태를 확인한다 (읽기 전용).
uv run devbot release status
```

`release preview`는 다음을 보여준다: 최신 stable 버전, 다음 버전,
최신 검증된 `main` 커밋(해당 커밋의 CI check-run이 모두 성공일 때만
"검증됨"으로 표시), 게시 준비 여부와 막고 있는 이유, 생성될 아티팩트
이름, 마지막 stable Release 이후 병합된 Pull Request 목록, 생성된
Release Notes 전문(한국어 섹션 다음 영어 섹션, 동일한 변경 사항을
설명). Release Notes는 오직 병합된 PR 번호/제목/`release:*` 라벨에서만
생성되며, 존재하지 않는 기능을 지어내지 않는다.

`release publish`는 다음 중 하나라도 해당하면 아무것도 쓰지 않고
실패한다: 로컬 checkout이 dirty함, 최신 `main` 커밋이 CI 검증되지
않음, 대상 커밋에 이미 stable Release가 존재함, 병합된 PR에
`release:*` 라벨이 없거나 둘 이상임, 게시 후 태그/Release/자산/
`SHA256SUMS`가 기대와 다름, 생성된 Release Notes가 비어 있음. 태그를
직접 옮기거나 만들지 않고, 기존 Release 워크플로를 우회하지도 않는다
- 실패하거나 시간 초과되면 워크플로 실행 URL과 함께 보고할 뿐, 로컬에서
대신 Release를 만들지 않는다.

`release status`는 최신 stable 버전, 최신 Release 워크플로 실행
상태, 마지막으로 게시된 커밋, 현재 게시 상태
(`never-run`/`in-progress`/`up-to-date`/`failed`)를 보여준다.

## Goal 기반 계획 절차 (Task 038)

운영자가 매 Task를 직접 결정하지 않아도 되도록, 상위 목표(Goal) 한 문장을
주면 DevBot이 로드맵/완료된 Task/열린 Issue-PR과 비교해 무엇이 필요한지
계산해 준다.

```bash
uv run devbot goal plan "Publish the next stable release."
```

daemon lock을 잡지 않고 GitHub에 아무것도 쓰지 않는(읽기 전용) 명령이므로
실행 중인 데몬 옆에서 안전하게 실행할 수 있다.

### 입력

- 위치 인자 하나: 분석할 Goal 문장(자유 텍스트). **한국어와 영어를 모두
  1급 입력으로 지원한다** (PR #82 리뷰로 추가). 예:
  `"Publish the next stable release."`, `"Improve Release UX."`,
  `"Implement Self Update."`, `"Reduce GitHub API failures."`,
  `"다음 안정 릴리스를 발행해"`, `"셀프 업데이트 기능을 구현해"`.
  입력 텍스트는 Unicode NFC로 정규화한 뒤 처리하므로 한글이 NFC/NFD 중
  어느 형태로 들어와도(예: macOS 일부 입력 경로에서 발생하는 자모 분리형)
  동일하게 인식한다.
- `--repo owner/repo` (선택): 생략하면 `config/repositories.yaml`의 단일
  enabled 저장소를 쓴다.

### 출력 스키마

```
goal: <입력한 Goal 원문>
decision: already_completed | duplicate_open_work | single_task | multi_task | ambiguous
reasons:
  - <결정을 내린 이유, 최소 1개>
evidence:
  - <근거: Task 번호/파일, 또는 Issue/PR 참조>
planned_tasks (<N>):
  [<순서>] <Task 제목>
      objective: <목표>
      dependencies: <의존하는 다른 계획된 Task 제목, 없으면 none>
      expected_deliverables:
        - <산출물>
      acceptance_criteria:
        - <완료 기준>
```

`decision`이 `already_completed`/`duplicate_open_work`/`ambiguous`이면
`planned_tasks`는 항상 비어 있다(0개). `single_task`는 정확히 1개,
`multi_task`는 2개 이상의 `planned_tasks`를 반환하며, 각 항목의
`dependencies`는 항상 더 앞선 순서의 Task만 가리킨다(순환/순방향 참조
없음).

`ambiguous`일 때만 종료 코드가 1이다 - 나머지 네 가지 `decision`은 모두
"계획을 성공적으로 계산했다"는 뜻이므로 종료 코드 0이다.

### 안전 동작과 한계

- **계획된 Task의 Issue/Branch/Contract/PR을 직접 만들지 않는다.** 이
  명령은 오직 읽기 전용 계획 수립만 한다 - 실행은 항상 사람(또는 별도
  Planner 절차)이 한다.
- **근거 없이 계획을 지어내지 않는다.** `planned_tasks`의 모든 title/
  objective/expected_deliverables/acceptance_criteria는 미리 손으로 정리해
  코드에 고정한 capability catalog(`src/devbot/goal_planner.py`의
  `CAPABILITY_CATALOG`)에서만 나온다 - Goal 문장을 요약하거나 자유
  생성하지 않는다. catalog에 없고 로드맵 텍스트와도 겹치지 않는 Goal은
  항상 `ambiguous`로 처리된다.
- **catalog는 손으로 유지 관리되며 오래될 수 있다.** 새 Task가 catalog에
  없는 기능을 구현해도, `docs/00-roadmap.md`를 직접 비교하는 별도 경로가
  있어 이미 완료된 작업은 (catalog가 아직 그 사실을 모르더라도)
  `already_completed`로 잡아낼 수 있다. 다만 아직 구현되지 않은 새로운
  종류의 Goal은 catalog를 갱신하기 전까지 `ambiguous`로만 보고된다.
- **중복/진행 중 작업 우선순위:** 열린 Issue/PR과의 텍스트 중첩이 완료된
  Task(로드맵)나 catalog 일치보다 먼저 확인된다 - 진행 중인 작업이 있다면
  이미 끝난 것으로 오판하지 않기 위함이다.
- **결정론적이며 LLM을 호출하지 않는다.** 키워드/구절 포함 매칭과 토큰
  중첩 비율만 사용하므로, 동일한 입력(Goal, 로드맵, 열린 Issue/PR
  목록)에는 항상 동일한 출력이 나온다 - 테스트와 재현이 쉽다는 뜻이지만,
  자유 형식 자연어를 사람처럼 이해하지는 못한다.
- **한국어 지원도 같은 고정 catalog/문자열 매칭 방식이며, 번역이나 자유
  생성을 하지 않는다.** catalog의 각 도메인은 영어 keyword phrase 옆에
  근거 있는 한국어 표현을 나란히 등록해 둔다(예: `self_update_runtime`
  도메인은 `"self update"`와 `"셀프 업데이트"`를 모두 인식). 실행 동사
  판정도 마찬가지로 영어 단어 집합과 한국어 어간
  (`구현`/`추가`/`개선`/`수정`/`발행`/`게시` 등, 활용형은 부분 문자열
  매칭으로 인식 - 예: `"구현해줘"`는 `"구현"` 어간 매칭) 양쪽을 확인한다.
  catalog나 로드맵 어디에도 없는 한국어 Goal은 영어와 마찬가지로 항상
  `ambiguous`다 - 언어를 더 많이 인식한다고 해서 "지어내지 않는다"는
  기본 원칙이 약해지지 않는다.
