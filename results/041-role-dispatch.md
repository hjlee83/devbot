# Task 041 Result: Role Dispatch and Agent Registry

## 완료 내용

- `src/devbot/agent_registry.py`를 새로 만들어 Role/Capability/Agent/Router
  개념을 분리했다:
  - **Role**: `AgentRegistry.roles`/`AgentDescriptor.supported_roles`의 키로 쓰이는
    평범한 문자열("implementer", "reviewer", ...) - 별도 타입을 만들지 않았다.
  - **Capability**: `AgentDescriptor.capabilities`의 문자열 태그("python",
    "github", "testing" 등) - 이번 priority routing은 이 값을 전혀 읽지 않는다.
    향후 Auto routing을 위해 기록만 해 둔다.
  - **Agent**: `AgentDescriptor` - `AgentRunner`가 아니라 레지스트리 항목이다.
    `id`(운영자용 식별자, 예: "claude-local")와 `backend`(실제
    `build_agent_runner`가 이해하는 실행 백엔드 종류, "claude"/"codex")를
    분리했다 - 같은 backend를 여러 다르게 설정된 `id`로 등록하거나, 이
    코드베이스가 아직 구현하지 않은 backend를 등록해도 이 모듈의 구조를 다시
    바꿀 필요가 없도록.
  - **Router**: `resolve_agent(registry, role)` - priority routing만 구현했다
    (활성화된 Agent 중 해당 Role을 지원하는 것 중 가장 높은 priority 선택, 동점은
    `id` 오름차순으로 결정론적 처리).
- `config/agents.yaml`(선택)이 있으면 그것을 파싱하고, 없으면 기존
  `IMPLEMENTER_AGENT`/`REVIEWER_AGENT`/`DEFAULT_AGENT` 설정(`devbot.config`의
  기존 로직, 전혀 변경하지 않음)에서 레지스트리를 합성한다
  (`synthesize_registry_from_config`) - 이 파일이 없는 모든 기존 배포는 이 Task
  이전과 정확히 동일한 Agent로 dispatch된다.
- `main.py`의 daemon 실행 경로가 `config.implementer_agent`/`.reviewer_agent`를
  직접 읽던 것을 `resolve_agent(agent_registry, "implementer").backend`/
  `"reviewer"`로 바꿨다. `build_agent_runner`(실행 백엔드) 자체는 전혀 건드리지
  않았다.
- 읽기 전용 CLI 추가: `devbot role list`, `devbot role resolve <role>`,
  `devbot agent list`, `devbot goal dispatch`(Task 040의 `goal execute`와 완전히
  동일한 동작 + "implementer" Role이 어떤 Agent로 라우팅되는지 보여주는 한 줄
  추가 - Agent를 실제로 호출하지 않음).

## 아키텍처

```
        Task
         |
         v
  Required Role ("implementer" / "reviewer" / "planner" / "release" / ...)
         |
         v
   Agent Registry  (roles: routing 전략 - "priority"만 구현;
         |          agents: id/backend/enabled/priority/supported_roles/capabilities)
         v
      Router        resolve_agent(registry, role) -> AgentDescriptor
         |          (활성화+지원 Agent 중 최고 priority, 동점은 id 오름차순)
         v
  Selected Agent    AgentDescriptor.backend ("claude" | "codex")
         |
         v
 기존 실행 백엔드    build_agent_runner(backend, dry_run=...) -> AgentRunner
                     (devbot.agents - 이 Task에서 변경 없음)
```

## 주요 설계 결정

- **하위 호환이 가장 중요한 안전장치다.** `config/agents.yaml`이 없는 한(현재
  이 저장소를 포함한 모든 배포), `load_agent_registry`는 기존 `config.
  implementer_agent`/`.reviewer_agent`에서 레지스트리를 합성하고, 그 위에서
  `resolve_agent`를 돌리면 항상 정확히 같은 값이 나온다. `devbot.config.
  load_config`나 `DevBotConfig`의 필드는 단 한 줄도 바꾸지 않았다 - 위험을
  최소화하기 위한 의도적 선택이다.
- **`build_agent_runner`(실행 백엔드)는 전혀 바꾸지 않았다.** Router는
  `AgentDescriptor.backend` 문자열("claude"/"codex")을 그대로
  `build_agent_runner`에 넘긴다 - `devbot.agents` 패키지에는 이 Task의 diff가
  전혀 없다.
- **`AgentDescriptor.id`와 `.backend`를 분리했다.** 요청 예시 설정의 "claude-local",
  "codex-local", "gpt-cloud", "gemini" 같은 이름은 운영자용 레지스트리 식별자이지,
  실제 실행 백엔드 종류와 같을 필요가 없다 - 이 코드베이스는 지금도 "claude"와
  "codex" 두 backend만 구현하므로, `backend`는 로드 시점에
  `devbot.agents.KNOWN_AGENT_NAMES`로 검증하고 그 외 값은 `AgentRegistryError`로
  fail closed한다.
- **동점 처리는 등록 순서가 아니라 `id` 오름차순으로 명시적으로 결정한다.**
  Python의 `max()`가 우연히 첫 항목을 선택하는 동작에 기대지 않고,
  `sorted(candidates, key=lambda a: (-a.priority, a.id))[0]`로 어떤 순서로 Agent
  목록이 만들어지든 항상 같은 결과가 나오게 했다(`test_deterministic_tie_break_
  by_id`가 목록을 뒤집어도 같은 결과인지 확인한다).
- **`devbot goal dispatch`는 `devbot goal execute`(Task 040)와 동일한
  `execute_goal()` 호출을 재사용한다.** 새로운 실행 경로를 만들지 않고, 계획된
  Task를 구체화한 뒤 "implementer" Role의 Router 해석 결과를 한 줄
  추가했을 뿐이다 - 이 Task 041 자체는 goal 실행의 실제 부수효과(Issue/Branch/
  Contract 생성 여부)를 조금도 바꾸지 않는다.

## Future Admin integration (문서화 요구사항)

이 Task는 Admin UI를 구현하지 않지만, Agent Registry가 이미 UI가 그대로 노출할 수
있는 형태다: `devbot agent list`/`devbot role list`/`devbot role resolve`가 보여주는
정보(`AgentDescriptor`의 모든 필드, `RoleRoutingConfig`)는 이미 순수 데이터
구조이므로, 향후 Admin UI는 `config/agents.yaml`을 읽고 쓰는 REST 엔드포인트만
추가하면 되고 `resolve_agent`/`AgentRegistry`의 형태를 바꿀 필요가 없다. `enabled`
필드는 이미 존재하므로 "Agent 비활성화" 같은 UI 동작도 이 파일을 편집하는 것만으로
가능하다.

## Future Auto routing (문서화 요구사항)

`AgentDescriptor.capabilities`는 이미 존재하지만 priority routing은 이를 전혀
참조하지 않는다. 향후 Auto routing을 추가하려면: (1) `RoleRoutingConfig.routing`에
`"auto"`(또는 다른 이름)를 새 값으로 추가하고, (2) `resolve_agent`에
`role_config.routing == "auto"`일 때의 새 분기(예: Task가 요구하는 Capability
집합과 `AgentDescriptor.capabilities`의 교집합으로 점수를 매기는 로직)를 추가하면
된다 - `AgentRegistry`/`AgentDescriptor`의 필드는 이미 이를 지원할 수 있는 형태이므로
API를 바꿀 필요가 없다. 이번 Task는 `_SUPPORTED_ROUTING_STRATEGIES = frozenset({
"priority"})`로 "priority" 외의 값을 명시적으로 거부해, 아직 구현하지 않은 전략이
조용히 무언가 잘못된 것을 선택하는 대신 fail closed하도록 했다
(`test_unsupported_routing_strategy_raises`).

## 수정 파일

- `src/devbot/agent_registry.py` (신규)
- `src/devbot/main.py`
- `tests/test_agent_registry.py` (신규)
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `tasks/041-role-dispatch.md`
- `results/041-role-dispatch.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-041-1 Role 존재/누락 | `test_role_exists_resolves_configured_agent`, `test_role_missing_raises_role_not_configured` |
| CP-041-2 활성화된 Agent 없음/미지원 Role | `test_no_enabled_agents_raises_no_agent_available`, `test_unsupported_role_raises_no_agent_available` |
| CP-041-3 priority 순서와 결정론적 동점 처리 | `test_priority_ordering_selects_highest_priority`, `test_deterministic_tie_break_by_id` |
| CP-041-4 비활성화 Agent 제외 | `test_disabled_agents_are_skipped` |
| CP-041-5 dispatch가 해석된 Role을 사용 | `test_daemon_dispatch_uses_router_resolved_role`, `test_goal_dispatch_shows_role_resolution_without_invoking_agent` |
| CP-041-6 기존 dispatch 백엔드 무변경 | `test_daemon_dispatch_backend_unchanged_without_agents_registry_file` |
| CP-041-7 Agent Registry 파싱/하위 호환 로딩 | `test_parse_agent_registry_builds_roles_and_agents` 외 17개 (전체 목록은 계약서 참고) |
| CP-041-8 읽기 전용 role/agent CLI | `test_role_list_command_is_wired`, `test_role_resolve_command_is_wired`, `test_role_resolve_unconfigured_role_returns_failure_exit_code`, `test_agent_list_command_is_wired` |
| CP-041-9 문서와 근거 | 본 Result, `docs/00-roadmap.md` |
| CP-041-10 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS, 729 passed
  (기존 697개 + 이번 Task에서 추가한 32개: `tests/test_agent_registry.py` 25개,
  `tests/test_main.py` 7개)

## 수동 검증 결과 (읽기 전용, Agent 미호출)

`hjlee83/devbot` 실제 저장소를 대상으로 아래 명령을 직접 실행했다 - 전부 읽기
전용이며 어떤 Agent도 실제로 호출하지 않았다.

```
$ uv run devbot role list
implementer
reviewer

$ uv run devbot role resolve implementer
role: implementer
resolved_agent_id: codex
backend: codex
priority: 100
capabilities: none

$ uv run devbot role resolve reviewer
role: reviewer
resolved_agent_id: claude
backend: claude
priority: 100
capabilities: none

$ uv run devbot agent list
codex [enabled] backend=codex priority=100 roles=[implementer] capabilities=[none]
claude [enabled] backend=claude priority=100 roles=[reviewer] capabilities=[none]

$ uv run devbot goal dispatch "Add a global PATH launcher." --dry-run
...
resolved_role: implementer -> agent=codex (backend=codex) [not invoked]
```

`config/agents.yaml`이 이 저장소에 없으므로 합성된 레지스트리가 쓰였고, 결과
(`implementer -> codex`, `reviewer -> claude`)는 배포된 `.env`의
`IMPLEMENTER_AGENT`/`REVIEWER_AGENT` 값과 정확히 일치했다(직접 대조 확인).

## 남은 TODO와 제한

- **`devbot doctor`는 아직 Router를 거치지 않는다** - `check_agent_roles`/
  `check_agent_execution_readiness`는 여전히 `config.implementer_agent`/
  `.reviewer_agent`를 직접 읽는다. `config/agents.yaml`이 없는 한(현재 유일한
  프로덕션 상태) 정확하지만, 누군가 그 파일을 추가해 다른 Agent를 매핑하면
  `doctor`의 보고가 실제 dispatch와 어긋날 수 있다 - 후속 Task로 남긴다.
- **이 저장소에는 실제 `config/agents.yaml`을 추가하지 않았다** - 라이브 daemon은
  계속 합성된(기존과 동일한) 레지스트리를 사용한다. 실제로 다중 Agent
  priority routing을 쓰려면 별도로 그 파일을 만들고 검토해야 한다.
- **Auto routing/capability scoring/Admin UI/비용 최적화/부하 분산/다중 Agent
  dispatch는 계약대로 구현하지 않았다** - 위 "Future Auto routing"/"Future Admin
  integration" 절에 이후 어떻게 추가할 수 있는지만 문서화했다.

## 위험 요소

- 합성 레지스트리와 기존 config 필드 사이의 동치성이 이 Task의 안전성 핵심이다
  - `synthesize_registry_from_config`와 `devbot.config`의 실제 해석 로직이
  미래에 서로 다르게 바뀌면(예: 한쪽만 수정) 조용히 어긋날 수 있다.
  `test_daemon_dispatch_backend_unchanged_without_agents_registry_file`이 전체
  daemon 경로를 통해 이를 고정하지만, `devbot.config`를 수정하는 후속 Task는 이
  동치성도 함께 검토해야 한다.
- `config/agents.yaml`의 YAML 형식은 스키마 라이브러리 없이 수동 필드 검증만
  한다 - 잘못된 값은 `AgentRegistryError`로 fail closed하지만, 스키마가 더
  복잡해지면(예: 중첩 구조) 수동 검증이 번거로워질 수 있다.
