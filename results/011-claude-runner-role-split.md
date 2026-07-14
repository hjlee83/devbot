# Result: Task 011

## Status

- COMPLETE. `ClaudeRunner`가 `AgentRunner` 두 번째 구현체로 추가됐고,
  구현(implementer)/리뷰(reviewer) 역할이 각각 독립된 Agent로 설정·생성·
  주입된다. 기본값은 implementer=`claude`, reviewer=`codex`이며, 역할별
  설정이 없는 기존 배포는 `DEFAULT_AGENT` 하나로 계속 동작한다(회귀 없음).
  Ready Issue 최초 구현과 Task 010 rework 모두 같은 `implementer_runner`를
  쓰고, `reviewer_runner`는 이 Task에서는 주입만 되고 실행되지 않는다.
- 착수 전 `origin/main`(Task 010 병합분, 커밋 `7b2d738`)을 이 브랜치에
  먼저 병합한 뒤 구현했다(계약서 Dependencies 참고).

## 이번 세션에서 추가/변경한 내용

1. **`ClaudeRunner`** (`src/devbot/agents/claude.py`, 신규)
   - `AgentRunner` 인터페이스 구현. `dry_run: bool = True` 기본값,
     dry-run에서는 부작용 없는 결과만 반환(`CodexRunner`와 동일 패턴).
   - 실제 실행 시 `repository.local_path`를 `cwd`로 비대화형 Claude CLI
     (`claude -p <prompt> --permission-mode acceptEdits`)를 서브프로세스로
     호출한다.
   - CLI 미설치(`FileNotFoundError`), timeout(`subprocess.TimeoutExpired`,
     `timeout_seconds` 필드로 설정 가능, 기본 1800초)을 각각 구분된 코드
     경로에서 잡아 구조화된 실패 `AgentRunResult`로 반환한다. 인증 실패는
     Claude CLI가 전용 종료 코드를 문서화하지 않으므로 "비정상 종료"와
     같은 경로(0이 아닌 `returncode`)로 처리하되, CLI의 stdout/stderr
     전체를 `message`에 그대로 보존해서 호출자가 원인을 판단할 수 있게
     했다(계약서 Risk 절 참고 — 이 구분은 실제 CLI 문서가 확인되면 후속
     Task에서 세분화할 수 있다).

2. **Agent 선택 팩토리** (`src/devbot/agents/__init__.py`)
   - `build_agent_runner(name, *, dry_run)`: `"codex"`/`"claude"` 문자열을
     실제 `AgentRunner` 구현체로 매핑한다. 그 외 이름은
     `UnknownAgentError`로 실패한다(조용한 fallback 없음).
   - `KNOWN_AGENT_NAMES`: `config.py`의 설정 검증이 재사용하는 단일
     레지스트리(등록된 Runner와 항상 동기화됨).

3. **설정 확장** (`src/devbot/config.py`, `src/devbot/models.py`)
   - `DevBotConfig`에 `implementer_agent: str`, `reviewer_agent: str` 필드
     추가.
   - 해석 순서: ①역할별 env var(`IMPLEMENTER_AGENT`/`REVIEWER_AGENT`) →
     ②(없으면) 기존 `DEFAULT_AGENT`(명시적으로 설정된 경우만) →
     ③(그마저 없으면) 새 기본값 implementer=`claude`, reviewer=`codex`.
   - 최종 값이 `KNOWN_AGENT_NAMES`에 없으면 `ConfigError`로 설정 로딩
     단계에서 실패한다.

4. **`PollingService` 역할 분리** (`src/devbot/polling.py`)
   - 필드명 `agent_runner` → `implementer_runner`로 변경(동작 동일, 이름만
     변경 — 순수 리팩터).
   - `reviewer_runner: AgentRunner | None = None` 필드 추가. 이 Task에서는
     어떤 코드 경로에서도 호출하지 않는다(주입만).
   - ready-task Agent 실행 로그를
     `"AgentRunner 실행: implementer=%s dry_run=%s"`로 변경(이전에는
     쓰이지 않던 `default_agent`를 참조했다).

5. **`devbot.main` 배선**
   - `CodexRunner(dry_run=...)` 하드코딩 제거. `build_agent_runner`로
     `implementer_runner`/`reviewer_runner`를 각각 생성한다.
   - Ready-task 경로(`PollingService(implementer_runner=...)`)와 Task 010
     rework 경로(`_apply_rework_changes`에 넘기는 클로저) 모두 **동일한**
     `implementer_runner` 인스턴스를 사용한다.
   - 프로세스 락 획득 직후, PollingService 구성 전에
     `"실행 구성: implementer=%s reviewer=%s dry_run=%s"` 로그를 남긴다
     (`--once`와 연속 루프 양쪽에서 최초 1회).

## 수정/신규 파일

- `src/devbot/agents/claude.py`(신규) — `ClaudeRunner`.
- `src/devbot/agents/__init__.py` — `build_agent_runner`, `KNOWN_AGENT_NAMES`.
- `src/devbot/config.py` — 역할별 Agent 해석/검증.
- `src/devbot/models.py` — `DevBotConfig.implementer_agent`/`reviewer_agent`.
- `src/devbot/polling.py` — `implementer_runner`/`reviewer_runner` 필드.
- `src/devbot/main.py` — Agent 선택 배선, 실행 시작 로그.
- `tests/test_agents_claude.py`(신규) — `ClaudeRunner` 단위 테스트.
- `tests/test_config.py` — 역할별 Agent 해석/검증 테스트.
- `tests/test_polling.py` — 역할 분리 테스트 3개 추가, 기존 `agent_runner=`
  호출부 전체를 `implementer_runner=`로 리네임.
- `tests/test_main_loop.py`, `tests/test_beta_smoke.py` — 동일 리네임,
  `DevBotConfig` fixture에 새 필드 반영.
- `docs/04-agent-system.md`, `docs/00-roadmap.md` — 실제 구현 상태 반영.
- `tasks/011-claude-runner-role-split.md` — v1.0.0(PR #16, Task 010 병합
  전 작성) → v1.1.0으로 갱신: Task 010 병합 상태 전제, 기본값/폴백 규칙
  명문화, 신규 필수 테스트 8개로 품질 게이트 재작성, worktree/resume/자동
  main 동기화 일반화/자동 브랜치 checkout을 제외 범위에 추가.
- `results/011-claude-runner-role-split.md`(본 파일).

## Checkpoint별 테스트

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-011-1 | Ready Issue 최초 구현 실행은 `implementer_runner`를 사용한다 | `test_ready_task_uses_implementer_runner` | PASS |
| CP-011-2 | Task 010 rework 실행도 `implementer_runner`를 사용한다 | `test_rework_uses_implementer_runner` | PASS |
| CP-011-3 | `reviewer_runner`는 주입되어도 구현 경로 어디서도 호출되지 않는다 | `test_reviewer_runner_is_not_used_for_implementation` | PASS |
| CP-011-4 | `ClaudeRunner`는 `repository.local_path`를 작업 디렉터리로 CLI를 실행한다 | `test_claude_runner_uses_repository_as_working_directory` | PASS |
| CP-011-5 | `ClaudeRunner`는 CLI 미설치를 구조화된 실패로 보고한다 | `test_claude_runner_reports_missing_cli` | PASS |
| CP-011-6 | `ClaudeRunner`는 실행 timeout을 구조화된 실패로 보고한다 | `test_claude_runner_reports_timeout` | PASS |
| CP-011-7 | 알 수 없는 implementer Agent 설정은 설정 로딩 단계에서 실패한다 | `test_config_rejects_unknown_implementer_agent` | PASS |
| CP-011-8 | 알 수 없는 reviewer Agent 설정은 설정 로딩 단계에서 실패한다 | `test_config_rejects_unknown_reviewer_agent` | PASS |

추가로 작성한 안전장치/경계 테스트(체크포인트 필수 목록에는 없지만 계약서
동작 규칙과 Risk 절을 직접 방어함):

- `tests/test_agents_claude.py`: `test_claude_runner_dry_run_has_no_side_effect`,
  `test_claude_runner_invokes_claude_cli`,
  `test_claude_runner_reports_nonzero_exit`(비정상 종료·인증 실패가 같은
  경로로 구조화되어 반환됨을 확인).
- `tests/test_config.py`:
  `test_role_agents_default_to_claude_and_codex_when_nothing_is_configured`
  (신규 배포 기본값), `test_role_agents_fall_back_to_default_agent_for_existing_deployments`
  (기존 `DEFAULT_AGENT=codex` 단일 배포 회귀 방지 — CP-011 Definition of
  Done 항목), `test_role_agents_can_be_set_independently_of_default_agent`.

## 1차 리뷰 반영 (2026-07-14)

리뷰에서 blocker로 지적된 내용: `ClaudeRunner`가 CLI 미설치/timeout을
`AgentRunResult(executed=False, dry_run=False, returncode=None)`로 구조화해
반환하지만, 호출부(`polling.py`의 ready 경로, `main.py`의
`_apply_rework_changes`)는 `returncode not in (None, 0)`만 실패로 판정하고
있었다. `returncode=None`은 이 조건을 통과하므로, dry-run의 정상적인
"실행 안 함"(`executed=False, dry_run=True`)과 진짜 실행 실패
(`executed=False, dry_run=False`)가 같은 `None` returncode로 뭉뚱그려져
후자도 성공처럼 delivery/commit/push까지 흘러갈 수 있는 실제 버그였다.

수정: `AgentRunResult`에 `failed` property를 추가해 판정 로직을
데이터클래스 자체에 통합했다 — `dry_run=True`면 항상 `False`(의도된
no-op), 그 외에는 `not executed or returncode not in (None, 0)`이면
`True`. `polling.py`의 `if agent_result.returncode not in (None, 0):`와
`main.py`의 `if result.returncode not in (None, 0):`를 모두
`if ....failed:`로 교체했다.

신규 회귀 테스트:
- `tests/test_agents_base.py`(신규): `AgentRunResult.failed`의 Happy/
  Failure/Boundary 4가지 조합(dry-run no-op, 정상 실행, nonzero 실행,
  `executed=False, dry_run=False, returncode=None`).
- `tests/test_beta_smoke.py::test_unexecuted_non_dry_run_agent_result_blocks_before_delivery` —
  ready 경로 전체 흐름(full flow)에서 이 결과가 `AGENT_FAILED` + blocked
  전환으로 이어지고 `delivery.deliver()`가 호출되지 않음을 확인.
- `tests/test_main_loop.py::test_apply_rework_changes_raises_when_agent_result_is_unexecuted_and_not_dry_run` —
  rework 경로에서 `_apply_rework_changes()`가 예외를 던져
  `ReworkService.process()`가 검증/commit/push로 진행하지 않고 blocked로
  이어짐을 확인.

## 검증 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS |
| `uv run pytest` | PASS, 142 passed |
| `uv run devbot --once --dry-run` | PASS, `no_ready_task`; 시작 로그에 `실행 구성: implementer=claude reviewer=codex dry_run=True` 출력 확인 |

## 회귀 확인

- 기존 `CodexRunner` 단일 경로: `implementer_agent`/`reviewer_agent`가
  모두 `DEFAULT_AGENT`(명시된 경우) 또는 새 배포 기본값으로 해석되고,
  기존 `DEFAULT_AGENT=codex`만 있는 배포는 `test_role_agents_fall_back_to_default_agent_for_existing_deployments`가
  증명하듯 양쪽 역할 모두 그대로 `codex`가 된다.
- `PollingService.agent_runner` → `implementer_runner` 필드명 변경은
  전체 스위트로 동작 동일함을 확인했다. 변경은 기계적 리네임뿐이며
  로직은 바꾸지 않았다.
- `.env` 테스트 격리: `IMPLEMENTER_AGENT`/`REVIEWER_AGENT`를 검증하는
  신규 config 테스트는 `monkeypatch.setenv`로 값을 주입한다(`.env` 파일에
  적으면 `load_dotenv(..., override=False)`가 프로세스 환경에 영구
  반영되어 이후 테스트로 값이 새는 것을 실제로 확인하고 수정했다).

## 위험 요소

- Claude Code CLI의 실제 인자 규약을 검증 환경에서 실제 CLI로 확인하지
  못했다(오프라인/실행 불가 환경). `-p <prompt> --permission-mode
  acceptEdits` 호출 규약이 실제와 다르면 dry-run이 아닌 실행 경로가 항상
  실패로 끝날 수 있다 — 기본값이 dry-run이고 모든 실패가 구조화된 결과로
  흡수되므로 폴링 루프 자체는 죽지 않는다.
- "인증 실패"는 "비정상 종료"와 같은 코드 경로로 처리된다(Risk 절 참고).
  Claude CLI의 정확한 인증 실패 종료 코드/포맷이 확인되면 후속 Task에서
  더 구체적으로 분기할 수 있다.
- `reviewer_runner`는 생성·주입만 되고 어디서도 호출되지 않는다 — 자동
  리뷰 트리거는 의도적으로 범위 밖이다(다음 Task).

## TODO

- 리뷰어 역할을 폴링 루프에 실제로 연결해 PR에 자동 리뷰를 트리거하는
  후속 Task.
- 실제 Claude CLI가 설치된 환경에서 `ClaudeRunner`의 실제 실행 경로(dry-run
  아님)를 1회 수동 검증한다.
