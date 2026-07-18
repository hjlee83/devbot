# Task 041: Role Dispatch and Agent Registry

## Goal

Replace direct agent dispatch with a role-based dispatch architecture. DevBot must
never hard-code a concrete Agent name (Claude/Codex/GPT/Gemini/...) at a dispatch call
site - it dispatches only Roles ("implementer", "reviewer", "planner", "release",
...), and a Router resolves the configured Agent for that Role from an Agent
Registry. This is the foundation for a future Admin UI, capability-aware Auto
routing, and multi-agent dispatch - none of which are implemented in this Task, only
the abstraction that allows them later without breaking APIs.

## Context

Before this Task, `devbot.main` read `config.implementer_agent`/`config.
reviewer_agent` - two plain strings, resolved and validated once at config-load time
by `devbot.config.load_config` (`IMPLEMENTER_AGENT`/`REVIEWER_AGENT`/`DEFAULT_AGENT`
env vars, validated against `devbot.agents.KNOWN_AGENT_NAMES` - "codex" or "claude")
- and passed those strings directly into `devbot.agents.build_agent_runner()`. That
worked, but every call site had to know both "which Role needs an Agent" and "which
Agent name that Role currently maps to" at once, with no seam for a registry, priority
selection among multiple candidates, or (later) capability-aware selection.

## Architecture

```
        Task
         |
         v
  Required Role ("implementer" / "reviewer" / "planner" / "release" / ...)
         |
         v
   Agent Registry  (roles: routing strategy: "priority" only; agents: id/backend/
         |          enabled/priority/supported_roles/capabilities)
         v
      Router        resolve_agent(registry, role) -> AgentDescriptor
         |          (highest priority among enabled agents supporting role;
         |           ties broken deterministically by id ascending)
         v
  Selected Agent    AgentDescriptor.backend ("claude" | "codex")
         |
         v
 existing execution backend   build_agent_runner(backend, dry_run=...) -> AgentRunner
                               (devbot.agents - UNCHANGED by this Task)
```

- **Role**: a plain string ("implementer", "reviewer", ...) - not a distinct type.
  Just a key into `AgentRegistry.roles` / `AgentDescriptor.supported_roles`.
- **Capability**: a plain string tag on an `AgentDescriptor.capabilities` (e.g.
  "python", "github", "testing"). Recorded for a future Auto routing strategy;
  **priority routing (this Task) never reads it**.
- **Agent**: `AgentDescriptor` - a registry entry, not an `AgentRunner`. `id` is the
  operator-facing registry identity (e.g. "claude-local"); `backend` is which concrete
  execution backend `build_agent_runner` instantiates ("claude"/"codex" today),
  deliberately decoupled from `id` so a future registry can list multiple
  differently-configured instances of the same backend, or backends this codebase
  does not implement yet, without changing this shape again.
- **Router**: `resolve_agent()`.

## In Scope

1. `src/devbot/agent_registry.py`: `AgentDescriptor`, `RoleRoutingConfig`,
   `AgentRegistry`, `resolve_agent` (the Router, priority routing only),
   `parse_agent_registry`, `synthesize_registry_from_config`, `load_agent_registry`,
   `list_roles`.
2. Optional `config/agents.yaml` (format shown below); when absent, the registry is
   synthesized from the existing `implementer_agent`/`reviewer_agent` config fields,
   so every deployment without this file dispatches identically to before this Task.
3. `main.py`'s daemon wiring resolves the `implementer`/`reviewer` Role via the Router
   instead of reading `config.implementer_agent`/`config.reviewer_agent` directly.
4. Read-only CLI: `devbot role list`, `devbot role resolve <role>`,
   `devbot agent list`, and `devbot goal dispatch` (identical to Task 040's
   `devbot goal execute`, plus a read-only "resolved_role: implementer -> agent=... "
   line - never invokes that Agent).

```yaml
# config/agents.yaml (optional - example)
roles:
  implementer:
    routing: priority
  reviewer:
    routing: priority
agents:
  - id: claude-local
    backend: claude
    enabled: true
    priority: 100
    supported_roles: [implementer, planner]
    capabilities: [github, python, testing]
  - id: codex-local
    backend: codex
    enabled: true
    priority: 90
    supported_roles: [implementer, reviewer]
    capabilities: [github, python, review]
```

## Out of Scope

- Admin UI, Auto routing, capability scoring, cost optimization, load balancing,
  multi-agent dispatch (explicitly deferred - the architecture must allow them later
  without an API change, per the request).
- Any change to `devbot.agents` (`build_agent_runner`, `AgentRunner`,
  `ClaudeRunner`/`CodexRunner`) - the execution backend is untouched.
- Any change to `devbot.config.load_config`'s existing `IMPLEMENTER_AGENT`/
  `REVIEWER_AGENT`/`DEFAULT_AGENT` resolution or `DevBotConfig`'s shape - kept exactly
  as-is; the Router reads those already-resolved fields when synthesizing.
- `devbot.doctor`'s `check_agent_roles`/`check_agent_execution_readiness` still read
  `config.implementer_agent`/`.reviewer_agent` directly rather than the Router -
  accurate whenever no `config/agents.yaml` exists (the only case in production
  today); noted as a follow-up in the Result document.
- Creating a real `config/agents.yaml` for this repository's own deployment - none is
  added, so the live daemon keeps using the synthesized (legacy-equivalent) registry.

## Functional Requirements

1. Priority routing: among Agents that are `enabled` and list the requested Role in
   `supported_roles`, the highest `priority` wins; ties are broken deterministically
   by `id` ascending (independent of registry declaration order).
2. `resolve_agent` raises `RoleNotConfiguredError` when the Role has no entry in
   `registry.roles` (and no Agent's `supported_roles` mentions it either).
3. `resolve_agent` raises `NoAgentAvailableError` when the Role is configured but no
   enabled Agent supports it (covers both "zero Agents at all" and "Agents exist but
   none list this Role").
4. `load_agent_registry(config)` reads `config/agents.yaml` (or `DEVBOT_AGENTS_PATH`)
   when present; otherwise returns `synthesize_registry_from_config(config)`, whose
   `resolve_agent()` result for "implementer"/"reviewer" is always exactly
   `config.implementer_agent`/`config.reviewer_agent`.
5. `main.py`'s daemon wiring calls `build_agent_runner(resolve_agent(registry,
   "implementer").backend, ...)` / the "reviewer" equivalent, replacing the direct
   `config.implementer_agent`/`.reviewer_agent` reads - `build_agent_runner` itself is
   not modified.
6. `devbot role list` / `devbot role resolve <role>` / `devbot agent list` are
   read-only, never acquire the daemon `ProcessLock`, and never construct a
   `GitHubWriteClient`.
7. `devbot goal dispatch` behaves identically to `devbot goal execute` (same
   underlying `execute_goal()` call, same Issue/Branch/Contract-only boundary) and
   additionally reports the Router's resolution for the "implementer" Role without
   ever invoking `build_agent_runner`/`AgentRunner.run()`.

## Quality Gates

### CP-041-1 - Role exists / role missing

Required tests:
- `test_role_exists_resolves_configured_agent`
- `test_role_missing_raises_role_not_configured`

### CP-041-2 - No enabled agents / unsupported role

Required tests:
- `test_no_enabled_agents_raises_no_agent_available`
- `test_unsupported_role_raises_no_agent_available`

### CP-041-3 - Priority ordering and deterministic tie-break

Required tests:
- `test_priority_ordering_selects_highest_priority`
- `test_deterministic_tie_break_by_id`

### CP-041-4 - Disabled agents are skipped

Required test:
- `test_disabled_agents_are_skipped`

### CP-041-5 - Dispatch uses resolved Role

Required tests:
- `test_daemon_dispatch_uses_router_resolved_role`
- `test_goal_dispatch_shows_role_resolution_without_invoking_agent`

### CP-041-6 - Existing dispatch backend unchanged

Required test:
- `test_daemon_dispatch_backend_unchanged_without_agents_registry_file`

### CP-041-7 - Agent Registry parsing and backward-compatible loading

Required tests:
- `test_parse_agent_registry_builds_roles_and_agents`
- `test_parse_agent_registry_infers_role_without_explicit_roles_section`
- `test_parse_agent_registry_defaults_backend_to_id`
- `test_parse_agent_registry_rejects_unknown_backend`
- `test_parse_agent_registry_rejects_missing_id`
- `test_parse_agent_registry_rejects_non_boolean_enabled`
- `test_parse_agent_registry_rejects_non_integer_priority`
- `test_parse_agent_registry_rejects_non_list_supported_roles`
- `test_parse_agent_registry_rejects_non_mapping_roles`
- `test_parse_agent_registry_rejects_non_list_agents`
- `test_synthesize_registry_matches_legacy_config_fields`
- `test_synthesize_registry_dedupes_same_backend_for_both_roles`
- `test_load_agent_registry_falls_back_to_synthesis_when_file_missing`
- `test_load_agent_registry_reads_real_file`
- `test_load_agent_registry_rejects_invalid_yaml`
- `test_load_agent_registry_rejects_non_mapping_top_level`
- `test_unsupported_routing_strategy_raises`
- `test_list_roles_returns_sorted_role_names`

### CP-041-8 - Read-only role/agent CLI

Required tests:
- `test_role_list_command_is_wired`
- `test_role_resolve_command_is_wired`
- `test_role_resolve_unconfigured_role_returns_failure_exit_code`
- `test_agent_list_command_is_wired`

### CP-041-9 - Documentation and evidence

Required evidence:
- `results/041-role-dispatch.md`
- `docs/00-roadmap.md` (Task 041 entry)

### CP-041-11 - Agent `id` uniqueness (PR #88 review)

`AgentDescriptor.id` is the operator-facing identity a future Admin UI, dispatch
history, and enable/disable operations will key on, so `parse_agent_registry` must
reject a duplicate `id` (fail closed at load time) while still allowing multiple
Agents to share the same `backend`.

Required tests:
- `test_parse_agent_registry_rejects_duplicate_agent_ids`
- `test_parse_agent_registry_allows_same_backend_with_different_ids`

### CP-041-10 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Validation Gate

```bash
uv run ruff check .
uv run pytest
```

Demonstrate, read-only, against `hjlee83/devbot`: `devbot role list`,
`devbot role resolve implementer`, `devbot role resolve reviewer`, `devbot agent
list`, and `devbot goal dispatch "<goal>" --dry-run` - recorded in the Result
document. No Agent is invoked by any of this Task's own verification.

## Files Expected to Change

- `src/devbot/agent_registry.py` (new)
- `src/devbot/main.py` (daemon dispatch wiring, `role`/`agent` CLI, `goal dispatch`)
- `tests/test_agent_registry.py` (new), `tests/test_main.py`
- `docs/00-roadmap.md`
- `tasks/041-role-dispatch.md`, `results/041-role-dispatch.md`

## Risk

- The synthesized fallback registry is the load-bearing safety mechanism for
  backward compatibility - if it ever diverged from `config.implementer_agent`/
  `.reviewer_agent`, existing deployments would silently dispatch to a different
  Agent. Mitigated by `test_synthesize_registry_matches_legacy_config_fields` and the
  full-daemon-path `test_daemon_dispatch_backend_unchanged_without_agents_registry_
  file` test, plus the entire pre-existing daemon test suite passing unmodified.
- `devbot.doctor`'s agent-readiness checks do not (yet) go through the Router - if an
  operator adds `config/agents.yaml` with different Agents than `IMPLEMENTER_AGENT`/
  `REVIEWER_AGENT`, `doctor`'s report would no longer describe what actually
  dispatches. Documented as a known follow-up, not fixed here (Out of Scope).
- `config/agents.yaml`'s YAML shape has no schema enforcement beyond
  `parse_agent_registry`'s manual field checks - malformed entries fail closed with an
  `AgentRegistryError`, never silently ignored.

## Rollback Strategy

Revert this Task's commits. `agent_registry.py` is fully additive; `main.py`'s
dispatch change is a like-for-like replacement (same resolved backend string reaches
`build_agent_runner` either way when no `config/agents.yaml` exists, which is every
current deployment).

## Reviewer Focus

- Confirm `test_daemon_dispatch_backend_unchanged_without_agents_registry_file` and
  `test_daemon_dispatch_uses_router_resolved_role` actually run the full `main(["--
  once", "--dry-run"], ...)` path (not a unit-level shortcut) and assert on
  `build_agent_runner`'s actual call arguments.
- Confirm `devbot.agents`/`build_agent_runner`/`AgentRunner` themselves have zero
  diff in this PR.
- Confirm `devbot goal dispatch` never calls `build_agent_runner` in its own test
  (`test_goal_dispatch_shows_role_resolution_without_invoking_agent`).
- Confirm no `config/agents.yaml` was added to this repository as part of this Task
  (the live deployment should keep using the synthesized registry).

## Definition of Done

- All Quality Gates and required tests pass (`uv run pytest`, `uv run ruff check .`).
- `devbot role list`/`role resolve`/`agent list`/`goal dispatch --dry-run` were run
  for real against `hjlee83/devbot` (read-only), recorded in the Result document.
- No Agent was invoked during this Task's own verification.
- Result and PR Evidence record the actual implementation, files changed, and
  validation outcomes.
- DevBot reaches a mergeable state on Issue #87 and its Pull Request.
- Final merge remains manual.

## Result 문서 경로

`results/041-role-dispatch.md`

## Git Rules

- Task Issue: #87
- Branch: `task/041-role-dispatch`
- Pull Request: #88
- Result: `results/041-role-dispatch.md`
- Do not create another Issue, Branch, or Pull Request for this Task.
