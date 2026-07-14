# Task 011: ClaudeRunner 및 Implementer/Reviewer 역할 분리

Version: 1.1.0
Last Updated: 2026-07-14

## 목표

`AgentRunner` 인터페이스에 Claude Code CLI 기반 `ClaudeRunner` 구현체를 추가하고,
"구현(implementer) 역할"과 "리뷰(reviewer) 역할"을 각각 독립된 `AgentRunner`로
설정·생성·주입할 수 있도록 실행 구조를 분리한다. 기본 역할 구성은
implementer=`claude`, reviewer=`codex`로 하되, 역할별 설정이 없는 기존 배포는
`DEFAULT_AGENT` 하나로 계속 동작해야 한다(회귀 없음).

---

## 배경

`docs/04-agent-system.md`는 `ClaudeRunner`를 "가능한 후속 구현"으로 이미
언급하고 있다. `AgentRunner`(Task 004)는 벤더에 종속되지 않는 인터페이스로
설계되어 있지만, 현재 구현체는 `CodexRunner` 하나뿐이고 `devbot.main`이
이를 `CodexRunner(dry_run=config.dry_run)`로 하드코딩해서 생성한다.
`DevBotConfig.default_agent`는 설정값으로 로드되지만 실제로 어떤 Runner를
만들지 선택하는 데 쓰이지 않는 죽은 설정이다.

이 Task는 v1.0.0 초안(PR #16) 작성 이후 **Task 010이 `main`에 병합**되어
`PollingService`가 `devbot:review` Issue의 PR 피드백을 감지해 `ReworkService`로
rework를 수행하는 두 번째 Agent 실행 경로(ready 최초 구현 경로와 별도)를 갖게
된 상태를 기준으로 계약을 다시 정리한다. 두 실행 경로(ready 최초 구현,
Task 010 rework) 모두 "구현" 작업이므로 **둘 다 implementer 역할의 Runner를
사용**해야 하며, reviewer 역할의 Runner는 이 Task에서는 설정·생성·주입까지만
하고 실제 실행(자동 PR 리뷰 트리거)은 후속 Task로 남긴다.

또한 `AGENTS.md` 8절은 "리뷰 AI 표준 절차"를 사람이 트리거하는 절차로만
정의하고 있고, 코드 상에는 구현자와 리뷰어를 서로 다른 Agent로 구분해서
실행하는 경로가 없다.

---

## Dependencies

- Task 004: 워크스페이스 준비 및 Agent 실행 (`AgentRunner`, `CodexRunner`)
- Task 005: 폴링 메인 루프 (`PollingService`, `devbot.main` 배선)
- Task 010: ReworkService 폴링 루프 연결 — **이미 `main`에 병합됨(PR #18,
  커밋 `7b2d738`)**. 이 브랜치는 구현을 시작하기 전에 최신 `main`을 먼저
  병합해서 Task 010의 `_process_review_task`/`ReworkService.process` 경로
  위에서 작업한다.

---

## 구현 범위

### 포함

- `src/devbot/agents/claude.py`: `ClaudeRunner` 구현. `AgentRunner`
  인터페이스(`run(repository, prompt) -> AgentRunResult`)를 준수하고,
  `CodexRunner`와 동일하게 `dry_run: bool = True` 기본값과 dry-run 시
  부작용 없는 반환 구조를 따른다.
  - 실제 실행 시 **비대화형(non-interactive) Claude Code CLI**를
    `repository.local_path`를 작업 디렉터리(`cwd`)로 서브프로세스 호출한다.
  - 다음 네 가지 상황을 각각 예외를 전파시키지 않고 **구조화된
    `AgentRunResult` 실패**로 반환한다: Claude CLI 미설치, 인증 실패,
    비정상 종료(0이 아닌 종료 코드), 실행 timeout. (CLI 미설치와 timeout은
    서로 구분되는 예외 경로로 처리한다. 인증 실패는 Claude CLI가 별도의
    전용 종료 코드를 문서화하고 있지 않으므로, 비정상 종료와 같은 경로로
    처리하되 CLI의 stdout/stderr 전체를 `message`에 그대로 보존해서 호출자가
    원인을 알 수 있게 한다 — Risk 절 참고.)
- Agent 선택 로직(`src/devbot/agents/__init__.py`의 `build_agent_runner`):
  agent 이름 문자열("codex" 또는 "claude")로 실제 `AgentRunner` 구현체를
  생성한다. 지원하지 않는 이름은 명확한 예외로 실패한다(조용한 fallback 금지).
- `DevBotConfig`에 `implementer_agent: str`, `reviewer_agent: str` 필드를
  추가하고, `load_config()`에서 다음 우선순위로 해석한다:
  1. `IMPLEMENTER_AGENT` / `REVIEWER_AGENT` 환경변수가 있으면 그 값을 쓴다.
  2. 없으면 기존 `DEFAULT_AGENT` 값이 있을 때 그 값을 그대로 양쪽 역할에
     쓴다(기존 배포 호환 — `DEFAULT_AGENT=codex`만 있는 배포는 이전과
     동일하게 양쪽 역할 모두 `codex`가 된다).
  3. 그마저도 없으면 새 기본값 implementer=`claude`, reviewer=`codex`를
     쓴다.
  4. 최종적으로 해석된 `implementer_agent`/`reviewer_agent` 값이 알려진
     Agent 이름(`codex`, `claude`)이 아니면 설정 로딩 단계에서 `ConfigError`로
     실패한다.
- `PollingService`의 Agent 실행 경로를 `implementer_runner`로 명명하고,
  주입 전용 `reviewer_runner`(기본값 `None`, 이 Task에서는 실행되지 않음)를
  추가한다.
- `devbot.main`에서 `CodexRunner` 하드코딩을 제거하고
  `build_agent_runner(config.implementer_agent, ...)` /
  `build_agent_runner(config.reviewer_agent, ...)`로 `implementer_runner`와
  `reviewer_runner`를 각각 생성한다. **ready Issue 최초 구현 경로와 Task 010의
  rework 경로(`_apply_rework_changes`) 모두 동일한 `implementer_runner`를
  사용**한다. `reviewer_runner`는 생성해서 `PollingService`에 주입만 하고,
  자동으로 실행하는 코드 경로는 추가하지 않는다.
- 실행 시작 시(1회 실행이든 연속 루프든 최초 1회) `implementer`, `reviewer`,
  `dry_run` 값을 로그로 명확히 남긴다.
- 위 변경에 대한 단위 테스트 추가(아래 품질 게이트 참고).
- `docs/04-agent-system.md`를 실제 구현 상태에 맞게 갱신.
- `docs/00-roadmap.md`를 실제 구현 상태에 맞게 갱신.
- `results/011-claude-runner-role-split.md` 작성.

### 제외

- 리뷰어 역할을 폴링 루프에 자동으로 연결해 PR에 대해 실제로 리뷰를
  트리거하는 로직(PR 자동 리뷰 실행). `reviewer_runner`는 이 Task에서
  설정·생성·주입까지만 다루고, 실행은 후속 Task로 남긴다.
- `main` 브랜치를 작업 브랜치에 반영하는 절차의 일반화/자동화(이번 Task
  착수 전 1회성으로 수행한 `origin/main` 병합은 이 계약서가 다루는 구현
  범위가 아니라 작업 준비 절차다).
- 작업 브랜치로의 자동 checkout 기능.
- Git worktree 기반 실행/격리.
- 중단된 실행의 resume 기능.
- Gemini, DeepSeek 등 Claude/Codex 이외의 새 벤더 Runner.
- GitHub Actions 워크플로 변경.
- 저장소별 검증 명령 설정.
- Codex/Claude 자동 리뷰 호출.

---

## 동작 규칙

1. `IMPLEMENTER_AGENT`/`REVIEWER_AGENT`가 명시적으로 설정되어 있으면 그 값을
   그대로 각 역할에 사용한다.
2. 역할별 설정이 없고 `DEFAULT_AGENT`만 있으면, 기존과 동일하게 두 역할 모두
   `DEFAULT_AGENT` 값을 쓴다(회귀 없음 — 예: 기존 배포의
   `DEFAULT_AGENT=codex`는 변경 없이 양쪽 다 `CodexRunner`).
3. 역할별 설정도 `DEFAULT_AGENT`도 전혀 없는 새 배포는 implementer=`claude`,
   reviewer=`codex`가 기본값이 된다.
4. 알 수 없는 `IMPLEMENTER_AGENT`/`REVIEWER_AGENT`/`DEFAULT_AGENT` 값(위
   해석 결과가 `codex`/`claude` 어느 쪽도 아닌 경우)은 설정 로딩 단계에서
   명확한 오류로 실패하고, 조용히 기본 Runner로 대체되지 않는다.
5. Ready Issue 최초 구현 실행과 Task 010 rework 실행은 모두
   `implementer_runner`를 사용한다. 둘 중 어느 경로도 `reviewer_runner`를
   호출하지 않는다.
6. `ClaudeRunner`는 `dry_run=True`가 기본값이며, dry-run에서는 Claude CLI를
   호출하지 않고 실행하지 않았다는 구조화된 결과만 반환한다.
7. `ClaudeRunner`는 실제 실행 시 `repository.local_path`를 작업 디렉터리로
   Claude CLI를 비대화형으로 호출한다.
8. 실행 시작 시 `implementer`, `reviewer`, `dry_run` 값이 로그에 남는다.

---

## 품질 게이트

| ID | 체크포인트 | 필수 테스트 이름 |
|---|---|---|
| CP-011-1 | Ready Issue 최초 구현 실행은 `implementer_runner`를 사용한다 | `test_ready_task_uses_implementer_runner` |
| CP-011-2 | Task 010 rework 실행도 `implementer_runner`를 사용한다 | `test_rework_uses_implementer_runner` |
| CP-011-3 | `reviewer_runner`는 주입되어도 구현(ready/rework) 어느 경로에서도 호출되지 않는다 | `test_reviewer_runner_is_not_used_for_implementation` |
| CP-011-4 | `ClaudeRunner`는 `repository.local_path`를 작업 디렉터리로 Claude CLI를 실행한다 | `test_claude_runner_uses_repository_as_working_directory` |
| CP-011-5 | `ClaudeRunner`는 Claude CLI 미설치를 구조화된 실패로 보고한다 | `test_claude_runner_reports_missing_cli` |
| CP-011-6 | `ClaudeRunner`는 실행 timeout을 구조화된 실패로 보고한다 | `test_claude_runner_reports_timeout` |
| CP-011-7 | 알 수 없는 implementer Agent 설정은 설정 로딩 단계에서 실패한다 | `test_config_rejects_unknown_implementer_agent` |
| CP-011-8 | 알 수 없는 reviewer Agent 설정은 설정 로딩 단계에서 실패한다 | `test_config_rejects_unknown_reviewer_agent` |

테스트는 Happy Path, Failure Path, Boundary Condition을 포함해야 한다. 위
8개 외에도 `ClaudeRunner`의 dry-run 무부작용, 실제 CLI 호출, 비정상 종료
처리 및 역할별 기본값/폴백 해석에 대한 추가 회귀 테스트를 포함한다.

---

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

---

## Files Expected to Change

아래 목록은 예상 범위이며, 실제 저장소 구조를 확인한 뒤 필요한 최소 파일만
수정한다.

- `src/devbot/agents/claude.py` (신규)
- `src/devbot/agents/__init__.py`
- `src/devbot/config.py`
- `src/devbot/models.py`
- `src/devbot/main.py`
- `src/devbot/polling.py`
- `tests/test_agents_claude.py` (신규)
- `tests/test_config.py`
- `tests/test_polling.py`
- `tests/test_main_loop.py`
- `tests/test_beta_smoke.py`
- `docs/04-agent-system.md`
- `docs/00-roadmap.md`
- `results/011-claude-runner-role-split.md`

---

## Risk

- Claude Code CLI의 실제 인자/호출 규약이 Codex CLI와 다를 수 있어, 잘못
  추정하면 dry-run이 아닌 실제 실행 경로가 항상 실패할 수 있다. 완화책:
  기본값이 dry-run이고, 실제 실행 실패는 항상 구조화된 결과로 흡수되어
  폴링 루프를 죽이지 않는다.
- "인증 실패"를 "비정상 종료"와 별도 코드 경로로 완벽히 구분하려면 Claude
  CLI의 정확한 종료 코드/에러 포맷 문서가 필요하다. 이 Task는 그 구분을
  문자열 휴리스틱으로 추측하지 않고, 두 경우 모두 종료 코드와 CLI 출력
  전체를 그대로 `AgentRunResult.message`에 담아 호출자가 판단할 수 있게
  하는 방식으로 완화한다.
- `PollingService.agent_runner` → `implementer_runner` 필드명 변경은 기존
  테스트 호출부(`tests/test_polling.py`, `tests/test_main_loop.py`,
  `tests/test_beta_smoke.py`)를 다수 건드리는 기계적 리팩터다. 동작은
  바꾸지 않고 이름만 바꾸므로 전체 테스트 스위트 통과로 회귀 여부를
  확인한다.
- 역할 분리 설정을 지금 잘못 설계하면, 리뷰어를 폴링 루프에 실제로 연결하는
  후속 Task에서 인터페이스가 맞지 않아 재작업이 필요할 수 있다.
- 배포 환경에 Claude CLI 인증/토큰이 없으면 실제 실행 경로가 항상 실패할 수
  있다(기본값이 dry-run이므로 완화됨).

각 위험은 구조화된 실패 결과와 회귀 테스트로 방어한다.

---

## Rollback Strategy

- `ClaudeRunner`와 Agent 선택 로직을 제거하면 Task 010까지의
  `CodexRunner` 단일 하드코딩 동작으로 즉시 복귀할 수 있어야 한다.
- 기존 `AgentRunner` 공개 인터페이스(`run(repository, prompt) ->
  AgentRunResult`)는 변경하지 않는다.
- 역할 분리 설정은 `DEFAULT_AGENT` 폴백을 통해 기존 `.env`/`config/
  repositories.yaml`과 호환성을 유지하므로, 새 환경변수를 추가하지 않은
  기존 배포는 별도 조치 없이 그대로 동작한다.
- `implementer_agent`/`reviewer_agent` 관련 변경이 실패하면 `main.py`의
  `CodexRunner` 직접 생성 코드로 되돌릴 수 있어야 한다.
- `PollingService.implementer_runner` 필드명 리팩터가 문제를 일으키면
  `agent_runner`로 되돌리는 단일 커밋 되돌리기로 복구 가능해야 한다.

---

## Reviewer Focus

- `ClaudeRunner`가 `CodexRunner`와 동일한 dry-run 안전 기본값을 따르는가
- `ClaudeRunner`가 CLI 미설치/timeout/비정상 종료를 모두 예외를 전파하지
  않고 구조화된 결과로 반환하는가
- Ready 최초 구현과 Task 010 rework 경로가 실제로 같은 `implementer_runner`
  인스턴스를 쓰는가(각 경로에서 별도로 Runner를 새로 만들지 않는가)
- `reviewer_runner`가 주입은 되지만 어떤 경로에서도 `.run()`이 호출되지
  않는가(자동 리뷰 실행이 범위 밖에 남아 있는가)
- `IMPLEMENTER_AGENT`/`REVIEWER_AGENT` 미설정 시 기존 `DEFAULT_AGENT` 하나로
  동작하던 배포가 그대로 유지되는가(역할별 설정 전무 + `DEFAULT_AGENT=codex`
  조합에서 회귀가 없는가)
- 알 수 없는 Agent 이름에 대한 오류 메시지가 명확하고 설정 로딩 단계에서
  발생하는가
- 실행 시작 로그에 implementer/reviewer/dry_run이 실제로 남는가
- `agent_runner` → `implementer_runner` 리팩터가 기존 CodexRunner 단일
  경로 테스트를 깨뜨리지 않는가
- `docs/04-agent-system.md`, `docs/00-roadmap.md`가 실제 구현 상태를
  반영하는가

---

## Definition of Done

- 모든 CP-011 체크포인트에 대응하는 테스트가 존재하고 통과한다.
- 전체 테스트와 Ruff가 통과한다.
- `uv run devbot --once --dry-run`이 기존과 동일하게 성공한다(회귀 없음).
- 역할별 설정이 없는 기존 배포(`DEFAULT_AGENT`만 있는 배포)는 회귀 없이
  그대로 동작함을 테스트로 증명한다.
- 새 배포(아무 Agent 설정도 없는 경우) implementer=`claude`,
  reviewer=`codex`가 선택됨을 테스트로 증명한다.
- Ready 최초 구현과 Task 010 rework 모두 `implementer_runner`를 사용하고
  `reviewer_runner`는 호출되지 않음을 테스트로 증명한다.
- `results/011-claude-runner-role-split.md`가 작성된다.
- `docs/04-agent-system.md`와 `docs/00-roadmap.md`가 갱신된다.

## Result 문서 경로

`results/011-claude-runner-role-split.md`

---

## 계약서 완성도 규칙

이 문서는 실행용 Issue를 생성하기 전에 완성되어야 한다.

다음 항목이 누락된 초안, 빈 파일, 자리표시자 상태에서는 PR 또는
`devbot:ready` Issue를 생성하지 않는다.

- 목표
- 배경
- Dependencies
- 구현 범위와 제외 범위
- 품질 게이트와 필수 테스트 이름
- 검증 명령
- Files Expected to Change
- Risk
- Rollback Strategy
- Reviewer Focus
- Definition of Done
