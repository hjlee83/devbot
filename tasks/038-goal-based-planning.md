# Task 038: Goal-based Planning (Operator Planner)

## Goal

DevBot should no longer require the operator to manually decide every Task. The
operator provides a high-level Goal - "Publish the next stable release.", "Improve
Release UX.", "Implement Self Update.", "Reduce GitHub API failures." - and DevBot
determines whether no implementation is required, one Task is sufficient, or multiple
sequential Tasks are required, without ever inventing scope not backed by real source
code or documentation.

## Context

Every Task so far (000-037) started from an operator or Planner manually reading
`docs/00-roadmap.md`, the existing Task contracts, and open Issues to decide what to
build next (`docs/12-planner-workflow.md` section 1, "Planner owns Task number/scope").
This Task adds a read-only advisory layer in front of that process: `devbot goal plan`
answers "does this Goal need a new Task, and if so, what shape", without taking over
any of the Planner's actual authoring responsibilities (Task number, contract text,
Issue/branch/PR creation stay entirely manual, exactly as `docs/12-planner-workflow.md`
already specifies).

## In Scope

1. `devbot goal plan "<goal>"` - read-only, prints a plan, never writes to GitHub.
2. Compares the Goal against `docs/00-roadmap.md`'s completed Task entries and every
   open GitHub Issue/Pull Request.
3. A small, hand-curated, evidence-grounded capability catalog
   (`devbot.goal_planner.CAPABILITY_CATALOG`) covering capabilities already shipped
   (e.g. Task 037's release publish, Task 030's GitHub API retry) and capabilities
   explicitly recorded as not yet implemented (e.g. `docs/history.md`'s "Runtime
   automatic update discovery" known limitation, Task 032's "Global PATH launcher" Out
   of Scope item).
4. A deterministic decision: `already_completed` / `duplicate_open_work` /
   `single_task` / `multi_task` / `ambiguous`.
5. For `single_task`/`multi_task`, a plan of one or more `PlannedTask` entries, each
   with title, objective, dependencies, expected deliverables, acceptance criteria, and
   a recommended execution order consistent with its dependencies.
6. Roadmap, operator runbook, and this Task's contract/Result documenting the
   planner's input/output schema, limitations, and safety behavior.

## Out of Scope

- Automatic execution of a generated plan (creating the planned Tasks' Issues,
  branches, contracts, or PRs). `devbot goal plan` only ever prints a plan.
- Any free-text/LLM-generated task decomposition. Every planned Task's content comes
  from the fixed catalog below, not from summarizing the Goal text itself - this is
  what keeps "must not invent repository capabilities" enforceable and the command's
  output deterministic and testable.
- Modifying `docs/00-roadmap.md`'s existing Task entries, or any existing Task
  contract/Result.
- A general-purpose natural-language goal understanding system. A Goal outside the
  fixed catalog and without close roadmap-text overlap is reported `ambiguous`, not
  best-effort planned.

## Dependencies

- `docs/00-roadmap.md` (parsed as the "completed Tasks" source of truth).
- `src/devbot/github_client.py`'s existing `list_issues`/`list_pull_requests` (open
  work source of truth) - no new GitHub API surface needed.
- Task 037 (`src/devbot/release_ops.py`) as the architectural precedent this Task
  follows: a pure decision core (`plan_goal`) plus a thin GitHub/filesystem-reading
  wrapper (`fetch_goal_plan`), and `main.py`'s `_build_<name>_parser`/
  `_run_<name>_command` CLI wiring pattern.

## Functional Requirements

1. `devbot goal plan "<goal>"` accepts a free-text Goal as a positional argument and an
   optional `--repo owner/repo` (same convention as `timeline`/`worktree`/`release`).
2. The command never constructs a `GitHubWriteClient` and never acquires the daemon
   `ProcessLock`.
3. A Goal is `ambiguous` when it has fewer than two significant (non-stopword) tokens,
   or contains no recognizable actionable verb, or matches neither the capability
   catalog nor any completed roadmap entry closely enough to be evidence-grounded.
4. A Goal that overlaps an open Issue or Pull Request's title/body above a fixed,
   documented threshold is `duplicate_open_work`, citing that Issue/PR - checked before
   any catalog/roadmap match, since active work is the most immediately actionable
   duplicate signal.
5. A Goal matching an `implemented` catalog domain, or a completed roadmap entry when
   no catalog domain matches, is `already_completed`, citing the matching Task/evidence
   text - no planned Tasks are produced.
6. A Goal matching a not-yet-implemented catalog domain is `single_task` (exactly one
   planned Task) or `multi_task` (more than one), using that domain's fixed, curated
   Task templates - never synthesized from the Goal text.
7. Every `multi_task` plan's `PlannedTask.dependencies` reference only earlier-ordered
   tasks in the same plan (no forward references) -
   `devbot.goal_planner.dependency_order_is_valid` is the checkable invariant.
8. `devbot goal plan` exits `0` for every decision except `ambiguous`, which exits `1`
   (fail closed).

## Quality Gates

### CP-038-1 - Already-completed Goal detection

Required tests:
- `test_goal_matching_implemented_catalog_domain_is_already_completed`
- `test_goal_matching_completed_roadmap_entry_is_already_completed`
- `test_incomplete_roadmap_entry_is_not_treated_as_completed_evidence`

### CP-038-2 - One-Task Goal

Required test:
- `test_single_task_goal_produces_exactly_one_planned_task`

### CP-038-3 - Multi-Task Goal

Required test:
- `test_multi_task_goal_produces_multiple_ordered_planned_tasks`

### CP-038-4 - Ambiguous Goal fails closed

Required tests:
- `test_short_goal_is_ambiguous`
- `test_goal_without_an_actionable_verb_is_ambiguous`
- `test_actionable_but_unrecognized_goal_is_ambiguous_not_invented`
- `test_goal_plan_ambiguous_goal_returns_failure_exit_code`

### CP-038-5 - Duplicate/overlapping Goal detection

Required tests:
- `test_goal_overlapping_open_issue_is_duplicate_open_work`
- `test_open_work_overlap_takes_priority_over_catalog_match`
- `test_low_overlap_open_work_does_not_count_as_duplicate`
- `test_repo_name_and_task_template_boilerplate_do_not_inflate_overlap`

### CP-038-6 - Dependency ordering

Required tests:
- `test_multi_task_plan_dependencies_reference_strictly_earlier_tasks`
- `test_dependency_order_is_valid_detects_a_forward_reference`
- `test_every_catalog_domain_has_valid_dependency_ordering`

### CP-038-7 - Read-only behavior

Required tests:
- `test_fetch_goal_plan_never_calls_a_write_method`
- `test_fetch_goal_plan_reads_open_issues_and_pull_requests`
- `test_fetch_goal_plan_tolerates_a_missing_roadmap_file`
- `test_goal_plan_command_is_wired`
- `test_goal_plan_command_does_not_acquire_daemon_lock`

### CP-038-8 - Roadmap parsing

Required test:
- `test_parse_roadmap_extracts_completed_and_incomplete_entries`

### CP-038-9 - Documentation and evidence

Required evidence:
- `results/038-goal-based-planning.md`
- `docs/00-roadmap.md` (Task 038 entry)
- `docs/08-beta-runbook.md` (`devbot goal plan` operator section: input/output schema,
  limitations, safety behavior)

### CP-038-10 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Validation Gate

```bash
uv run ruff check .
uv run pytest
```

Demonstrate `devbot goal plan` for real against `hjlee83/devbot` (read-only, safe) for
at least one `already_completed`, one `multi_task`, and one `ambiguous` Goal, recorded
in the Result document.

## Files Expected to Change

- `src/devbot/goal_planner.py` (new)
- `src/devbot/main.py` (`devbot goal plan` CLI wiring)
- `tests/test_goal_planner.py` (new)
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `tasks/038-goal-based-planning.md`, `results/038-goal-based-planning.md`

## Risk

- A false `already_completed`/`duplicate_open_work` result could suppress a Goal that
  genuinely needs new work. Mitigated by requiring evidence (a cited Task/Issue/PR) for
  every non-`ambiguous` decision, and by an explicit, documented overlap threshold
  rather than a fuzzy/opaque score.
- A false `ambiguous` result on a Goal that is actually well-covered by the fixed
  catalog only costs the operator a wasted read - it never causes an unsafe write,
  since this Task performs no writes at all.
- The capability catalog is hand-curated and will drift out of date as new Tasks land
  (e.g. it will not automatically know about a Task 039 that implements something it
  currently lists as `implemented=False`). Mitigated by also checking
  `docs/00-roadmap.md` directly (which every Task's Definition of Done already requires
  keeping current), so a shipped capability is still detected as `already_completed`
  even before the catalog is manually updated to mention it by name.

## Rollback Strategy

Revert this Task's commits. `devbot goal plan` is fully additive - a new subcommand and
a new module with no changes to any existing command, service, or state machine.

## Reviewer Focus

- Confirm `devbot goal plan` never calls any `GitHubWriteClient` method and never
  acquires `ProcessLock` (`test_fetch_goal_plan_never_calls_a_write_method`,
  `test_goal_plan_command_does_not_acquire_daemon_lock`).
- Confirm every catalog domain's `evidence` string and every planned Task's
  deliverables/acceptance criteria cite a real Task number or real doc text, not
  generated prose (spot-check against `tasks/037-release-operator-ux.md`,
  `results/030-github-api-transient-retry.md`, `docs/history.md`).
- Confirm the four example Goals from this Task's Issue (`Publish the next stable
  release.`, `Improve Release UX.`, `Implement Self Update.`, `Reduce GitHub API
  failures.`) produce sensible, evidence-backed decisions.
- Confirm no Issue, branch, contract, or PR was created for any planned Task as part of
  this Task's own validation.

## Definition of Done

- All Quality Gates and required tests pass (`uv run pytest`, `uv run ruff check .`).
- `devbot goal plan` was run for real against `hjlee83/devbot` for `already_completed`,
  `multi_task`, and `ambiguous` example Goals, recorded in the Result document.
- No Issue, branch, contract, or PR was created for any planned Task.
- Result and PR Evidence record the actual implementation, files changed, and
  validation outcomes.
- DevBot reaches a mergeable state on Issue #81 and its Pull Request.
- Final merge remains manual.

## Result 문서 경로

`results/038-goal-based-planning.md`

## Git Rules

- Task Issue: #81
- Branch: `task/038-goal-based-planning`
- Pull Request: #82
- Result: `results/038-goal-based-planning.md`
- Do not create another Issue, Branch, or Pull Request for this Task.
