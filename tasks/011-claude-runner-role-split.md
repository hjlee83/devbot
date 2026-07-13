# Task 011: ClaudeRunner 및 Implementer/Reviewer 역할 분리

Version: 1.0.0
Last Updated: 2026-07-14

## 목표

`AgentRunner` 인터페이스에 Claude Code CLI 기반 `ClaudeRunner` 구현체를 추가하고,
"구현(implementer) 역할"과 "리뷰(reviewer) 역할"을 각각 독립된 `AgentRunner`로
설정하고 실행할 수 있도록 실행 구조를 분리한다.

---

## 배경

`docs/04-agent-system.md`는 `ClaudeRunner`를 "가능한 후속 구현"으로 이미
언급하고 있다. `AgentRunner`(Task 004)는 벤더에 종속되지 않는 인터페이스로
설계되어 있지만, 현재 구현체는 `CodexRunner` 하나뿐이고 `devbot.main`이
이를 `CodexRunner(dry_run=config.dry_run)`로 하드코딩해서 생성한다.
`DevBotConfig.default_agent`는 설정값으로 로드되지만 실제로 어떤 Runner를
만들지 선택하는 데 쓰이지 않는 죽은 설정이다.

또한 `AGENTS.md` 8절은 "리뷰 AI 표준 절차"를 사람이 트리거하는 절차로만
정의하고 있고, 코드 상에는 구현자와 리뷰어를 서로 다른 Agent로 구분해서
실행하는 경로가 없다.

Task 010(`tasks/010-rework-polling-integration.md`)은 `ReworkService` 폴링
연결에만 집중하기 위해 `ClaudeRunner` 구현과 새로운 Agent 구현을 범위에서
명시적으로 제외했다. 이 Task는 그 제외된 범위를 별도로 다룬다.

---

## Dependencies

- Task 004: 워크스페이스 준비 및 Agent 실행 (`AgentRunner`, `CodexRunner`)
- Task 005: 폴링 메인 루프 (`PollingService`, `devbot.main` 배선)
- Task 010: ReworkService 폴링 루프 연결 — 이 Task와 별도 브랜치로
  병행 가능하지만, `src/devbot/main.py`를 공통으로 수정하므로 Task 010
  Pull Request가 먼저 병합된 뒤 이 브랜치를 최신 `main`에 리베이스하고
  시작하는 것을 권장한다.

---

## 구현 범위

### 포함

- `src/devbot/agents/claude.py`: `ClaudeRunner` 구현. `AgentRunner`
  인터페이스(`run(repository, prompt) -> AgentRunResult`)를 준수하고,
  `CodexRunner`와 동일하게 `dry_run: bool = True` 기본값과 dry-run 시
  부작용 없는 반환 구조를 따른다. 실제 실행 시 Claude Code CLI(`claude`)를
  서브프로세스로 호출한다.
- Agent 선택 로직 추가: `DevBotConfig.default_agent` 값("codex" 또는
  "claude")에 따라 실제로 어떤 `AgentRunner` 구현체를 생성할지 결정하는
  팩토리/선택 함수. 지원하지 않는 값은 명확한 오류로 실패한다.
- Implementer/Reviewer 역할 분리를 표현하는 데이터 구조 및 설정 확장:
  구현자용 Agent와 리뷰어용 Agent를 각각 독립적으로 지정할 수 있어야
  하며(같은 벤더를 양쪽에 써도 되고, 벤더를 다르게 지정해도 된다),
  역할별 설정이 없을 때는 기존처럼 단일 Runner로 동작해야 한다(회귀 방지).
- 위 변경에 대한 단위 테스트 추가.
- `docs/04-agent-system.md`를 실제 구현 상태에 맞게 갱신.
- `docs/00-roadmap.md`를 실제 구현 상태에 맞게 갱신.
- `results/011-claude-runner-role-split.md` 작성.

### 제외

- 리뷰어 역할을 폴링 루프에 자동으로 연결해 PR에 대해 실제로 리뷰를
  트리거하는 로직 (Task 008이 `ReworkService`를 구현만 하고 폴링 연결은
  Task 010으로 미룬 것과 동일한 방식으로, 이 Task는 역할 분리 구조와
  `ClaudeRunner` 구현까지만 다루고 자동 트리거는 후속 Task로 남긴다)
- `ReworkService` 및 Task 010의 폴링 연결 범위
- Gemini, DeepSeek 등 Claude/Codex 이외의 새 벤더 Runner
- GitHub Actions 워크플로 변경
- 저장소별 검증 명령 설정
- Codex 자동 리뷰 호출

---

## 동작 규칙

1. `DEFAULT_AGENT` 설정값이 `codex`이면 기존과 동일하게 `CodexRunner`가
   선택된다(회귀 없음).
2. `DEFAULT_AGENT` 설정값이 `claude`이면 `ClaudeRunner`가 선택된다.
3. 지원하지 않는 `DEFAULT_AGENT` 값은 설정 로딩 단계에서 명확한 오류로
   실패하고, 알 수 없는 값으로 조용히 기본 Runner를 선택하지 않는다.
4. Implementer 역할과 Reviewer 역할에 각각 다른 Agent를 지정할 수 있다.
5. 역할별 설정이 전혀 없으면 기존 단일 `AgentRunner` 배선과 동일하게
   동작한다.
6. `ClaudeRunner`는 `dry_run=True`가 기본값이며, dry-run에서는 Claude CLI를
   호출하지 않고 실행하지 않았다는 구조화된 결과만 반환한다.

---

## 품질 게이트

| ID | 체크포인트 | 필수 테스트 이름 |
|---|---|---|
| CP-011-1 | `ClaudeRunner`는 dry-run에서 CLI를 호출하지 않고 부작용 없는 결과를 반환한다 | `test_claude_runner_dry_run_has_no_side_effect` |
| CP-011-2 | `ClaudeRunner`는 실제 실행 시 Claude CLI를 호출한다 | `test_claude_runner_invokes_claude_cli` |
| CP-011-3 | `ClaudeRunner`는 CLI 비정상 종료를 구조화된 실패 결과로 반환한다 | `test_claude_runner_reports_nonzero_exit` |
| CP-011-4 | `DEFAULT_AGENT=claude`이면 `ClaudeRunner`가 선택된다 | `test_agent_factory_selects_claude_runner_for_claude` |
| CP-011-5 | `DEFAULT_AGENT=codex`이면 기존과 동일하게 `CodexRunner`가 선택된다(회귀 방지) | `test_agent_factory_selects_codex_runner_for_codex` |
| CP-011-6 | 지원하지 않는 `DEFAULT_AGENT` 값은 명확한 설정 오류로 실패한다 | `test_agent_factory_rejects_unknown_agent_name` |
| CP-011-7 | Implementer/Reviewer 역할에 서로 다른 Runner를 독립적으로 지정할 수 있다 | `test_implementer_and_reviewer_roles_use_independent_runners` |
| CP-011-8 | 역할별 설정이 없으면 기존 단일 Runner 동작과 동일하게 유지된다(회귀 방지) | `test_role_config_defaults_preserve_existing_single_runner_behavior` |

테스트는 Happy Path, Failure Path, Boundary Condition을 포함해야 한다.

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
- `src/devbot/agents/base.py`
- `src/devbot/agents/__init__.py`
- `src/devbot/config.py`
- `src/devbot/models.py`
- `src/devbot/main.py`
- `tests/test_agents_claude.py` 또는 신규 테스트 파일
- `tests/test_config.py`
- `docs/04-agent-system.md`
- `docs/00-roadmap.md`
- `results/011-claude-runner-role-split.md`

---

## Risk

- Claude Code CLI의 실제 인자/호출 규약이 Codex CLI와 달라, 잘못 추정하면
  dry-run이 아닌 실제 실행 경로가 항상 실패할 수 있다.
- Agent 선택 로직 추가가 기존 `CodexRunner` 단일 하드코딩 경로(Task
  001~010 전체 테스트 스위트)를 깨뜨릴 위험이 있다.
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
- 역할 분리 설정은 선택적 기본값을 사용해 기존 `.env`/`config/
  repositories.yaml`과 호환성을 유지한다.
- `default_agent` 관련 변경이 실패하면 `main.py`의 `CodexRunner` 직접
  생성 코드로 되돌릴 수 있어야 한다.

---

## Reviewer Focus

- `ClaudeRunner`가 `CodexRunner`와 동일한 dry-run 안전 기본값을 따르는가
- Agent 선택 로직이 기존 `CodexRunner` 단일 경로 테스트를 깨뜨리지 않는가
- Implementer/Reviewer 역할 설정이 Task 001~010의 기존 폴링 동작에 회귀를
  일으키지 않는가
- 알 수 없는 Agent 이름에 대한 오류 메시지가 명확한가
- 리뷰 자동 트리거처럼 범위 밖 기능이 섞여 들어오지 않았는가
- `docs/04-agent-system.md`, `docs/00-roadmap.md`가 실제 구현 상태를
  반영하는가

---

## Definition of Done

- 모든 CP-011 체크포인트에 대응하는 테스트가 존재하고 통과한다.
- 전체 테스트와 Ruff가 통과한다.
- `uv run devbot --once --dry-run`이 기존과 동일하게 성공한다(회귀 없음).
- `DEFAULT_AGENT=claude` 설정 시 `ClaudeRunner`가 선택되어 실행됨을
  테스트로 증명한다.
- Implementer/Reviewer 역할에 서로 다른 Runner를 지정할 수 있음을
  테스트로 증명한다.
- 역할 설정이 없는 기존 배포는 회귀 없이 그대로 동작한다.
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
