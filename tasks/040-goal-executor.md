# Task 040: Goal Executor

## Goal

Extend the read-only Task 038 Goal Planner so the operator can take a validated plan
and materialize exactly one approved Task into the normal DevBot workflow (one GitHub
Issue, one branch, one draft contract), without turning the planner into an
unrestricted autonomous agent.

## Context

Task 038 added `devbot goal plan "<goal>"`, a read-only command that compares a
high-level Goal against the roadmap, completed Tasks, and open GitHub work and reports
a deterministic decision (`already_completed`/`duplicate_open_work`/`single_task`/
`multi_task`/`ambiguous`) plus, for `single_task`/`multi_task`, a fixed-catalog-sourced
list of `PlannedTask`s. That command never writes to GitHub. This Task adds the next,
strictly bounded step: turning ONE approved `PlannedTask` into a real Task Issue,
branch, and draft contract - stopping there. It does not implement, review, open a
Pull Request, merge, publish a Release, or execute more than one planned Task per
invocation.

## In Scope

1. `devbot goal execute "<goal>" --dry-run` - fully read-only.
2. `devbot goal execute "<goal>" --task <order>` (no `--confirm`) - also fully
   read-only (same as `--dry-run`).
3. `devbot goal execute "<goal>" --task <order> --confirm` - the only form that
   writes: creates exactly one GitHub Issue, one Task branch from the latest
   CI-validated `origin/<default_branch>`, and one draft Task contract file.
4. Reuses `devbot.goal_planner.fetch_goal_plan` (Task 038) verbatim - the plan is
   fetched exactly once per invocation and never regenerated or reinterpreted.
5. Reuses `devbot.planner`'s naming helpers (`canonical_branch_name`,
   `canonical_contract_path`, `canonical_result_path`, `canonical_issue_title`) and
   `devbot.release_ops`'s `local_checkout_is_dirty`/`target_commit_is_ci_validated`
   (Task 039, made public for this reuse) wherever they apply directly.
6. Dependency-ordered, idempotent materialization for multi-task plans.
7. Tests, contract, Result, roadmap, and operator runbook documentation.

## Out of Scope

- Creating a Pull Request (explicitly forbidden - the draft contract is left for a
  human Planner to finish before one is opened).
- Invoking any implementation Agent (Claude/Codex/GPT) - `goal_executor.py` does not
  import `devbot.agents` at all (enforced by a dedicated test).
- Merging anything, publishing a Release, or executing more than one planned Task per
  invocation - even for a `multi_task` plan, only the single selected (and eligible)
  Task is materialized.
- Automatically executing every Task in a multi-task plan.
- Deep content verification of a pre-existing branch/contract beyond
  existence-checking (documented limitation in the Result document).
- Changing Task 038's planning logic, catalog, or decision semantics in any way.

## Functional Requirements

1. `--dry-run` shows, without any GitHub write: the Goal, the planner decision, the
   selected planned Task, the next Task number, the proposed Issue title/body, the
   proposed branch name, a proposed contract path/content summary, dependencies, and
   every blocker.
2. Execution is allowed only when the planner decision is `single_task` (Task 1
   selected automatically when `--task` is omitted) or `multi_task` (an explicit valid
   `--task <order>` is required).
3. Execution refuses, without writing anything, for: `already_completed`,
   `duplicate_open_work`, `ambiguous`, invalid task order, unmet dependencies, a dirty
   local checkout, local main not matching `origin/main`, and a GitHub API/auth
   failure while checking any of the above.
4. Without `--confirm` (regardless of `--task`), nothing is ever written; the CLI
   explains that `--confirm` is required. `--dry-run` forces read-only even if
   `--confirm` is also given (shares the top-level `--dry-run` dest, same convention as
   `release publish --dry-run`).
5. With `--confirm` on an eligible plan, exactly one GitHub Issue, one Task branch (off
   the latest CI-validated `origin/<default_branch>`), and one draft Task contract file
   are created.
6. For `multi_task` plans, only the first not-yet-materialized planned Task (by
   `order`) may be materialized; a later Task stays blocked until every earlier one has
   a matching Task Issue.
7. Materialization is idempotent: an Issue is matched by its exact canonical title
   (`Task NNN: <planned Task title>`, open or closed) before creating one; a branch is
   matched by ref existence before creating one; a contract file is matched by content
   existence at that path on that branch before creating one. A retry after a partial
   failure only creates what is still missing.
8. If a newly-created Issue is immediately followed by a branch-ref collision with no
   matching Issue (an unrelated pre-existing branch happens to share the canonical
   name), materialization refuses with a clear "possible naming collision" error rather
   than silently reusing it.
9. The final report (`--confirm` or not) always includes: Goal, selected Task, Issue
   URL (when known), branch, contract path, created-vs-reused status for each of the
   three resources, and the next operator action.

## Quality Gates

### CP-040-1 - Single-task dry-run and confirmed execution

Required tests:
- `test_single_task_ready_when_no_blockers`
- `test_execute_goal_single_task_dry_run_never_writes`
- `test_execute_goal_single_task_confirmed_execution`
- `test_materialize_creates_all_three_when_none_exist`

### CP-040-2 - Multi-task selection requires an explicit, valid `--task`

Required tests:
- `test_multi_task_requires_explicit_task_flag`
- `test_multi_task_invalid_order_is_blocked`
- `test_multi_task_first_task_is_selectable`
- `test_execute_goal_multi_task_without_task_flag_refuses`
- `test_execute_goal_multi_task_valid_first_selection_executes`
- `test_execute_goal_invalid_task_order_refuses`

### CP-040-3 - Multi-task dependency ordering

Required tests:
- `test_multi_task_later_task_blocked_by_unmet_dependency`
- `test_multi_task_later_task_selectable_once_earlier_materialized`
- `test_execute_goal_later_task_blocked_by_unmet_dependency`

### CP-040-4 - Non-executable decisions refuse cleanly

Required tests:
- `test_already_completed_decision_blocks_execution`
- `test_duplicate_open_work_decision_blocks_execution`
- `test_ambiguous_decision_blocks_execution`
- `test_execute_goal_already_completed_refuses`
- `test_execute_goal_duplicate_open_work_refuses`
- `test_execute_goal_ambiguous_refuses`

### CP-040-5 - Local checkout safety gates

Required tests:
- `test_dirty_checkout_blocks_execution`
- `test_local_main_not_synced_blocks_execution`
- `test_local_main_matches_origin_true_when_synced`
- `test_local_main_matches_origin_false_when_local_ahead`
- `test_local_main_matches_origin_false_on_different_branch`
- `test_local_main_matches_origin_none_for_non_git_path`
- `test_execute_goal_dirty_checkout_refuses`
- `test_execute_goal_local_main_diverged_refuses`

### CP-040-6 - Missing confirmation never writes

Required tests:
- `test_execute_goal_without_confirm_never_writes`
- `test_goal_execute_dry_run_never_constructs_write_client`
- `test_goal_execute_dry_run_flag_overrides_confirm`

### CP-040-7 - Idempotency and partial-failure retry

Required tests:
- `test_already_materialized_selected_task_is_ready_and_flagged`
- `test_find_materialized_task_matches_exact_title`
- `test_find_materialized_task_returns_none_when_no_match`
- `test_materialize_reuses_existing_issue_and_branch_only_creates_contract`
- `test_materialize_is_fully_idempotent_when_everything_exists`
- `test_materialize_already_materialized_creates_nothing`
- `test_materialize_refuses_branch_collision_after_creating_new_issue`
- `test_execute_goal_existing_matching_issue_is_reused`
- `test_execute_goal_existing_matching_branch_only_completes_contract`

### CP-040-8 - Fail closed on API errors and inconsistent state

Required tests:
- `test_execute_goal_github_api_failure_fails_closed`
- `test_materialize_refuses_when_plan_not_ready`
- `test_next_task_number_scans_tasks_dir`
- `test_next_task_number_defaults_to_one_when_dir_missing`

### CP-040-9 - No Pull Request, no Agent invocation, no daemon-lock contention

Required tests:
- `test_execute_goal_never_creates_a_pull_request_or_invokes_an_agent`
- `test_goal_executor_module_never_imports_agents`
- `test_goal_execute_does_not_acquire_daemon_lock`
- `test_goal_execute_blocked_returns_failure_exit_code`
- `test_goal_execute_confirm_constructs_write_client_and_calls_execute`

### CP-040-10 - Documentation and evidence

Required evidence:
- `results/040-goal-executor.md`
- `docs/00-roadmap.md` (Task 040 entry)
- `docs/08-beta-runbook.md` (approval boundary, dependency handling, idempotency,
  recovery procedure)

### CP-040-11 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Validation Gate

```bash
uv run ruff check .
uv run pytest
```

Demonstrate `devbot goal execute --dry-run` for real against `hjlee83/devbot` for:
a single-task Goal, the multi-task Self Update Goal, an already-completed Goal, and an
ambiguous Goal - recorded in the Result document. Do not run a real confirmed
`devbot goal execute` against the live repository as part of this Task.

## Files Expected to Change

- `src/devbot/goal_executor.py` (new)
- `src/devbot/github_client.py` (`get_branch_ref`, `get_file_content`)
- `src/devbot/github_write_client.py` (`create_issue`, `create_branch`, `create_file`)
- `src/devbot/release_ops.py` (`target_commit_is_ci_validated` made public, reused)
- `src/devbot/main.py` (`devbot goal execute` CLI wiring)
- `tests/test_goal_executor.py` (new), `tests/test_main.py`, `tests/test_release_ops.py`,
  `tests/test_github_client.py`, `tests/test_github_write_client.py`
- `docs/00-roadmap.md`, `docs/08-beta-runbook.md`
- `tasks/040-goal-executor.md`, `results/040-goal-executor.md`

## Risk

- A draft contract created by this command is deliberately incomplete (no Quality
  Gates, no required test names, no Validation Gate) - it must never be mistaken for a
  finished, `devbot:ready` contract. Mitigated by an explicit "DRAFT" status section in
  the generated content and by never attaching any `devbot:*` label.
- Idempotency is existence-based, not content-verified: if an operator manually edits
  or replaces a matching Issue/branch/contract with unrelated content, materialization
  would treat it as already satisfied. Mitigated only partially (the branch-collision
  check in Functional Requirement 8); documented as a known limitation in the Result
  document.
- `next_task_number()` reads the local `tasks/` directory, which is only trustworthy
  once "local main matches origin/main" is confirmed - enforced as a hard precondition
  before any number is computed for a write.

## Rollback Strategy

Revert this Task's commits. `goal_executor.py` is fully additive - a new subcommand and
a new module. `target_commit_is_ci_validated`'s rename (private to public) is
mechanical and behavior-preserving (verified by the existing Task 039 test suite
continuing to pass under the new name).

## Reviewer Focus

- Confirm `goal_executor.py` never imports `devbot.agents`
  (`test_goal_executor_module_never_imports_agents`) and the materialize path never
  calls anything beyond `create_issue`/`create_branch`/`create_file`
  (`test_execute_goal_never_creates_a_pull_request_or_invokes_an_agent`).
- Confirm `fetch_goal_plan` (Task 038) is called exactly once per `execute_goal()` and
  never re-invoked or reinterpreted mid-flow.
- Confirm the multi-task dependency-order enforcement genuinely blocks skipping ahead
  (`test_multi_task_later_task_blocked_by_unmet_dependency` and its `execute_goal`
  counterpart), not just validating the requested order is *some* valid order.
- Confirm idempotent retry tests actually assert zero duplicate writes, not just a
  successful return value.
- Confirm no real `devbot goal execute --confirm` was run against `hjlee83/devbot` as
  part of this Task's validation evidence - only `--dry-run`.

## Definition of Done

- All Quality Gates and required tests pass (`uv run pytest`, `uv run ruff check .`).
- `devbot goal execute --dry-run` was run for real against `hjlee83/devbot` for a
  single-task Goal, the multi-task Self Update Goal, an already-completed Goal, and an
  ambiguous Goal, recorded in the Result document.
- No real confirmed execution was run against `hjlee83/devbot`.
- No Pull Request was created and no Agent was invoked as part of this Task's own
  implementation process beyond the normal Planner/Implementer workflow.
- Result and PR Evidence record the actual implementation, files changed, and
  validation outcomes.
- DevBot reaches a mergeable state on Issue #85 and its Pull Request.
- Final merge remains manual.

## Result 문서 경로

`results/040-goal-executor.md`

## Git Rules

- Task Issue: #85
- Branch: `task/040-goal-executor`
- Pull Request: #86
- Result: `results/040-goal-executor.md`
- Do not create another Issue, Branch, or Pull Request for this Task.
- Do not create a Pull Request for any Task materialized by this Task's own
  implementation.
