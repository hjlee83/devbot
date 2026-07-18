# Task 037: Release Operator UX

## Goal

The operator should never need to manually determine a version number, commit SHA, or
Release Notes when publishing a DevBot stable Release. The operator's entire
interaction with releasing DevBot becomes one sentence: "Publish the next stable
release." DevBot determines everything else automatically, using only the existing
Release workflow (Task 032/033/034/035/036) as the single mechanism that actually
creates a tag or a GitHub Release.

## Context

`.github/workflows/release.yml` already supports a `workflow_dispatch` recovery/manual
path (`increment` choice + `commit_sha` string input), but using it today still
requires an operator to: read `git log`/GitHub to find the latest validated `main`
commit SHA, decide `patch`/`minor`/`major` by hand, and accept whatever
`manual_release_plan()` generates as Release Notes - a single line. This is not
hypothetical: `v0.1.0`, DevBot's own first stable Release
(https://github.com/hjlee83/devbot/releases/tag/v0.1.0, published 2026-07-18 via a
manual `workflow_dispatch`), has exactly this body: `- patch: manual release`. This
Task closes that gap with a `devbot release` CLI, without changing how
`.github/workflows/release.yml` creates tags, builds artifacts, or publishes Releases.

## In Scope

1. `devbot release preview` - read-only. Never writes to GitHub.
2. `devbot release publish` - detects the latest CI-validated `main` commit, determines
   the next semantic version, generates bilingual (Korean + English) Release Notes,
   dispatches the existing `.github/workflows/release.yml` via `workflow_dispatch`,
   waits for the run to complete, and validates the published tag/Release/assets/
   `SHA256SUMS`. Never creates a tag or Release directly.
3. `devbot release status` - read-only summary of the latest stable Release and the
   latest Release workflow run.
4. Aggregate next-version/Release-Notes computation across every merged `main` Pull
   Request since the last stable Release (not just a single triggering PR), reusing
   `devbot.release`'s existing per-PR `release:*` label policy for each one.
5. A new optional `notes` input on `.github/workflows/release.yml`'s
   `workflow_dispatch`, used verbatim by `scripts/release_pipeline.py plan` when
   present and non-empty; the existing one-line manual-dispatch fallback is unchanged
   when it is absent (recovery via the GitHub Actions UI keeps working).
6. Roadmap, operator runbook, and ADR log updates documenting the new commands.

## Out of Scope

- Changing `.github/workflows/release.yml`'s tag/artifact/checksum/publication logic
  (Task 032's `validate-main` / `plan-release` / `build-artifacts` / `publish-release`
  job graph and safety states are unchanged).
- Any second, DevBot-owned path that creates a tag or a GitHub Release directly.
- Publishing a real Release as part of this Task.
- Machine translation of prose; Release Notes are generated only from structured PR
  metadata (number, title, `release:*` label), never free-text summarization.
- Backfilling or editing `docs/history.md`'s existing Stable Releases narrative (a
  pre-existing staleness relative to the real published `v0.1.0` is noted in this
  Task's Result document, but fixing it is left to a future Task).
- Multi-repository release orchestration beyond the existing `--repo owner/repo`
  selector already used by `devbot timeline`/`devbot worktree`.

## Dependencies

- Task 032 (`src/devbot/release.py`, `.github/workflows/release.yml`,
  `scripts/release_pipeline.py`) for the release policy engine and workflow this Task
  dispatches and extends.
- Task 033/034/035/036 for the already-published `v0.1.0` baseline this Task's
  `latest_stable_version` computation must correctly recognize.
- `src/devbot/github_client.py` / `github_write_client.py` (existing read/write GitHub
  REST clients) - extended, not replaced.
- `src/devbot/startup.py::resolve_operator_checkout` (existing operator-checkout
  resolver, reused for the "main is dirty" safety check).

## Functional Requirements

1. `devbot release preview` computes and prints, without any GitHub write: latest
   stable version, next version, latest CI-validated `main` commit, release readiness
   (ready/not-ready with reasons), expected release assets, every merged Pull Request
   since the last stable Release, and the generated Release Notes.
2. `devbot release publish`:
   - detects the latest `main` commit and requires it to have an all-success set of
     Check Runs before treating it as validated;
   - determines the next semantic version by aggregating the `release:*` label of
     every merged Pull Request since the last stable Release (max of
     `major`/`minor`/`patch`, ignoring `none`);
   - generates Release Notes automatically (Korean section, then `---`, then an
     English section, both describing the identical set of Pull Requests);
   - dispatches `.github/workflows/release.yml` via `workflow_dispatch` with the
     computed `increment`, `commit_sha`, and `notes` inputs;
   - polls the resulting workflow run until it completes;
   - validates the resulting tag, GitHub Release (not draft, correct target commit),
     asset names, and `SHA256SUMS` manifest structure;
   - prints a final report (tag, Release URL, workflow run URL, validated assets).
3. `devbot release status` prints, read-only: latest stable version, latest Release
   workflow run (URL/status/conclusion), last published commit, and a publication state
   of `never-run` / `in-progress` / `up-to-date` / `failed`.
4. Release Notes are generated only from merged Pull Request number/title/`release:*`
   label - no other source, no invented content.
5. `devbot release publish` refuses (no GitHub write at all) when any of the following
   holds, and reports every applicable reason:
   - the resolved local checkout has uncommitted changes;
   - the latest `main` commit is not CI-validated;
   - a (non-draft) Release already exists for that commit;
   - a commit since the last stable Release has no associated merged Pull Request, or
     that Pull Request does not carry exactly one `release:*` label;
   - there are no eligible (`patch`/`minor`/`major`) changes since the last stable
     Release;
   - the generated Release Notes would be empty.
6. After a successful workflow run, `devbot release publish` refuses to report success
   if the tag/Release/assets/`SHA256SUMS` do not match what it dispatched for - it
   reports the mismatch instead, and never attempts to fix it by writing to GitHub
   itself.
7. `devbot release preview|publish|status` never acquire the daemon `ProcessLock` and
   never run as part of the automatic polling loop (same policy as `devbot doctor` /
   `devbot timeline` / `devbot worktree`).

## Quality Gates

### CP-037-1 - Release preview is read-only and computes the full plan

Required tests:
- `test_build_release_preview_ready_when_all_checks_pass`
- `test_release_preview_command_is_wired`
- `test_release_preview_reports_not_ready_as_failure`

### CP-037-2 - Next version aggregates every merged PR since the last stable Release

Required tests:
- `test_gather_release_context_computes_commit_range_from_compare_api`
- `test_gather_release_context_falls_back_to_full_history_without_prior_release`
- `test_build_release_preview_handles_first_release_bootstrap`

### CP-037-3 - Bilingual, deterministic Release Notes sourced only from merged PRs

Required tests:
- `test_aggregate_release_notes_is_deterministic`
- `test_aggregate_release_notes_has_korean_then_english_sections_in_the_same_order`
- `test_aggregate_release_notes_korean_and_english_describe_the_same_prs`
- `test_aggregate_release_notes_sections_are_deterministic_by_increment`
- `test_aggregate_release_notes_requires_at_least_one_entry`
- `test_build_release_preview_notes_describe_same_prs_in_both_languages`

### CP-037-4 - Publish only ever dispatches the existing Release workflow

Required tests:
- `test_dispatch_release_sends_expected_inputs`
- `test_publish_release_never_calls_any_write_method_other_than_dispatch`
- `test_release_workflow_supports_optional_manual_notes_override`
- `test_manual_release_plan_notes_override_is_used_verbatim`
- `test_manual_release_plan_without_override_keeps_trivial_notes`
- `test_release_pipeline_plan_command_uses_notes_file_override`
- `test_release_pipeline_plan_command_ignores_empty_notes_file`

### CP-037-5 - Publish fails closed on every safety condition

Required tests:
- `test_build_release_preview_blocks_on_dirty_checkout`
- `test_build_release_preview_blocks_on_unvalidated_ci`
- `test_build_release_preview_blocks_when_release_already_exists_for_target`
- `test_build_release_preview_blocks_on_missing_release_label`
- `test_build_release_preview_blocks_when_commit_has_no_associated_pr`
- `test_build_release_preview_not_ready_when_no_eligible_changes`
- `test_dispatch_release_refuses_when_not_ready`
- `test_manual_release_plan_rejects_blank_notes_override`
- `test_release_publish_refuses_when_not_ready`
- `test_local_checkout_is_dirty_detects_clean_and_dirty_repo`
- `test_local_checkout_is_dirty_returns_none_for_non_git_path`

### CP-037-6 - Publish waits for the workflow and validates the published Release

Required tests:
- `test_wait_for_dispatched_run_polls_until_success`
- `test_wait_for_dispatched_run_raises_on_failure_conclusion`
- `test_wait_for_dispatched_run_times_out_waiting_for_run_to_appear`
- `test_validate_published_release_success`
- `test_validate_published_release_rejects_draft`
- `test_validate_published_release_rejects_target_commit_mismatch`
- `test_validate_published_release_rejects_asset_mismatch`
- `test_validate_published_release_rejects_malformed_checksum_manifest`
- `test_release_publish_dispatches_when_ready`
- `test_release_publish_dry_run_never_dispatches`

### CP-037-7 - Release status is read-only

Required tests:
- `test_build_release_status_up_to_date`
- `test_build_release_status_never_run`
- `test_build_release_status_failed`
- `test_build_release_status_in_progress`
- `test_release_status_is_read_only`

### CP-037-8 - CLI wiring matches existing subcommand conventions

Required tests:
- `test_release_command_does_not_acquire_daemon_lock`

### CP-037-9 - Existing automated and manual-recovery release paths stay compatible

Required tests:
- existing `tests/test_release.py` suite (push-triggered `release_plan_for_pr`,
  `manual_release_plan` default behavior, workflow structural tests) continues to pass
  unmodified in behavior, only extended with the optional `notes` input

### CP-037-10 - Documentation and evidence

Required evidence:
- `results/037-release-operator-ux.md`
- `docs/00-roadmap.md` (Task 037 entry)
- `docs/08-beta-runbook.md` ("릴리스 운영 절차 (Task 037)" section)
- `docs/07-decisions.md` (ADR entry for this Task's design decisions)

### CP-037-11 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Validation Gate

Run from the Task branch:

```bash
uv run ruff check .
uv run pytest
```

Demonstrate, without publishing a real stable Release:

- `devbot release preview` against this repository's real GitHub state (read-only, safe
  to run for real).
- `devbot release status` against this repository's real GitHub state (read-only, safe
  to run for real).
- `devbot release publish --dry-run` (computes and prints the plan; does not dispatch
  the workflow).

`devbot release publish` (real, non-dry-run) must not be run as part of this Task.

## Files Expected to Change

- `src/devbot/release.py` (bilingual notes generation, `manual_release_plan` notes
  override)
- `src/devbot/release_ops.py` (new - GitHub-calling orchestration)
- `src/devbot/github_client.py` (new read methods: releases, commits, workflow runs,
  release assets)
- `src/devbot/github_write_client.py` (new `dispatch_workflow` write method)
- `src/devbot/main.py` (`devbot release preview|publish|status` CLI wiring)
- `.github/workflows/release.yml` (optional `notes` `workflow_dispatch` input)
- `scripts/release_pipeline.py` (`--notes-file` option for the `plan` subcommand)
- `tests/test_release.py`, `tests/test_release_ops.py` (new), `tests/test_main.py`,
  `tests/test_github_client.py`, `tests/test_github_write_client.py`
- `docs/00-roadmap.md`, `docs/08-beta-runbook.md`, `docs/07-decisions.md`
- `tasks/037-release-operator-ux.md`, `results/037-release-operator-ux.md`

## Risk

- Incorrectly computing "the latest CI-validated `main` commit" could either block a
  legitimate release (safe failure) or, worse, treat an unvalidated commit as safe.
  Mitigated by requiring an explicit all-success Check Run set and failing closed
  (no fallback to an older commit) when that is not the case.
- A bug in the aggregate multi-PR increment computation could under- or over-version a
  Release. Mitigated by reusing the existing single-PR `release_increment_for_pr`
  policy (exactly one label or fail closed) per commit, rather than inventing new label
  semantics.
- Polling a real GitHub Actions run indefinitely if GitHub's API is degraded.
  Mitigated by an explicit, operator-configurable `--timeout-seconds` that raises
  rather than hangs.
- Generated Release Notes could, in principle, omit or misattribute a change if a
  commit's associated Pull Request cannot be resolved. Mitigated by treating an
  unresolved commit as a hard readiness blocker rather than silently skipping it.

## Rollback Strategy

Revert this Task's commits. `.github/workflows/release.yml`'s only change is one new
optional `workflow_dispatch` input consumed by an `if [ -s ... ]` guard - removing it
(or simply never invoking `devbot release publish`) leaves the existing push-triggered
and manual `workflow_dispatch` release paths exactly as Task 032/036 left them. No
schema, label, or state-machine change is introduced anywhere else.

## Reviewer Focus

- Confirm `devbot release publish` never calls any `GitHubWriteClient` method other
  than `dispatch_workflow` (`test_publish_release_never_calls_any_write_method_other_
  than_dispatch`) - this is the load-bearing guarantee that DevBot never bypasses the
  existing Release workflow.
- Confirm every safety condition in Functional Requirement 5 has both a `build_release_
  preview` unit test and, where user-visible, a CLI-level test.
- Confirm the Korean and English Release Notes halves describe the identical PR set
  (`test_aggregate_release_notes_korean_and_english_describe_the_same_prs`), not just
  matching section headers.
- Confirm `.github/workflows/release.yml`'s existing structural regression tests
  (tag/asset/permission/concurrency/manual-dispatch shape) still pass unmodified.
- Confirm no real `devbot release publish` (non-dry-run) was executed against
  `hjlee83/devbot` as part of this Task's validation evidence.

## Definition of Done

- All Quality Gates and required tests pass (`uv run pytest`, `uv run ruff check .`).
- `devbot release preview` and `devbot release status` were run for real against
  `hjlee83/devbot` and their output is recorded in the Result document.
- `devbot release publish --dry-run` was run for real and its output is recorded in the
  Result document; `devbot release publish` (real) was not run.
- `.github/workflows/release.yml`'s tag/artifact/checksum/publication behavior for both
  the push-triggered and manual-dispatch-without-`notes` paths is unchanged.
- Result and PR Evidence record the actual implementation, files changed, and
  validation outcomes.
- DevBot reaches a mergeable state on Issue #79 and its Pull Request.
- Final merge remains manual.

## Result 문서 경로

`results/037-release-operator-ux.md`

## Git Rules

- Task Issue: #79
- Branch: `task/037-release-operator-ux`
- Result: `results/037-release-operator-ux.md`
- Do not create another Issue, Branch, or Pull Request for this Task.
- Do not publish a real Release as part of this Task.
