# Task 039: Fix Release Operator CI Validation for main push workflows

## Goal

Make `devbot release preview`/`publish`'s "is the target `main` commit CI-validated"
check accept a successful GitHub Actions CI `push` run for the exact target commit,
without weakening any existing Release workflow safety behavior.

## Context

Reported bug: for `main` commit `d0fdb23b89d90fe15662413b5c141b374569b5f0`, GitHub
Actions has a successful `CI` workflow run (`push` event, `status: completed`,
`conclusion: success`, run id 29637320040 - verified with `gh run list --workflow CI
--commit <sha> --json databaseId,event,status,conclusion,url`), yet `uv run devbot
release preview` reported `target_commit_validated: no`.

### Root cause (confirmed live against `hjlee83/devbot`)

`devbot.release_ops.gather_release_context()` (Task 037) called
`GitHubClient.list_check_runs_for_ref()` (`GET
/repos/{owner}/{repo}/commits/{sha}/check-runs`) and required **every** returned
check run to have `conclusion == "success"`. That endpoint returns check runs from
**every** workflow that ran for the commit - not just the configured CI workflow.
`.github/workflows/release.yml` also triggers `on: push: branches: [main]`, so its own
jobs' check runs are mixed into the same commit's check-run list as the `CI`
workflow's `verify` check run:

```
$ gh api repos/hjlee83/devbot/commits/d0fdb23.../check-runs \
    --jq '.check_runs[]|{name,conclusion}'
{"name":"publish-release","conclusion":"skipped"}
{"name":"build-artifacts","conclusion":"skipped"}
{"name":"plan-release","conclusion":"failure"}
{"name":"validate-main","conclusion":"success"}
{"name":"verify","conclusion":"success"}

$ gh run list --repo hjlee83/devbot --workflow CI \
    --commit d0fdb23... --json databaseId,event,status,conclusion,url
[{"conclusion":"success","event":"push","status":"completed", ...}]
```

`plan-release` failing (a separate, pre-existing label-timing issue in the
push-triggered automatic release pipeline - out of this Task's scope, noted in the
Result document) and `build-artifacts`/`publish-release` being legitimately `skipped`
both make `all(conclusion == "success")` false, even though the `CI` workflow itself -
the one the operator actually cares about - passed cleanly. This is not specific to
that one failure: even in the ordinary case where `release.yml`'s `build-artifacts`/
`publish-release` jobs are correctly `skipped` (e.g. a `release:none` PR), the old
check-runs aggregation would still report `target_commit_validated: no`.

## In Scope

1. Replace the Check Runs based validation in `gather_release_context()` with a check
   against the configured CI workflow's own `push`-triggered runs, via the GitHub
   Actions Workflow Runs API.
2. Add `head_sha` filtering to `GitHubClient.list_workflow_runs()` (server-side
   narrowing, mirrors the existing `event`/`created` filters).
3. Preserve every existing `devbot release preview|publish|status` safety behavior;
   change only how `target_commit_validated` is computed.
4. Tests for all specified scenarios (see Quality Gates).

## Out of Scope

- The pre-existing label-timing race in `release.yml`'s push-triggered `plan-release`
  job (documented as a discovered, unrelated issue in the Result document, not fixed
  here).
- `.github/workflows/release.yml`'s own internal `validate-main` job or any of its
  tag/artifact/checksum/publication logic.
- `GitHubClient.list_check_runs_for_ref()` itself, or its use by the B2 automerge
  safety gate (`src/devbot/automerge.py`) - that use case (validating a PR's pre-merge
  head SHA before merging it) is exactly what Check Runs are for and is unaffected.
- Publishing `v0.1.1` or any real Release.
- Any manual bypass or operator override of CI validation.

## Functional Requirements

1. A `main` commit counts as CI-validated only when all of the following hold for at
   least one workflow run of the configured CI workflow (`ci.yml`):
   - the run's `head_sha` exactly matches the target commit;
   - the run's `event` is `push`;
   - the run's `status` is `completed`;
   - the run's `conclusion` is `success`.
2. A Pull Request Check Run (or workflow run) is never required or consulted for this
   check - only the CI workflow's own `push` runs against the exact target commit.
3. `GitHubClient.list_workflow_runs()` is queried with `event="push"` and
   `head_sha=<target commit>` (server-side filtering), consistent with "prefer the
   GitHub Actions Workflow Runs API."
4. Fail-closed behavior is preserved for every case: no matching run, a queued run, an
   in-progress run, a failed/cancelled run, a successful run for a different SHA, and a
   successful run that is not a `push` event (e.g. a pull-request run at a pre-merge
   SHA).
5. A GitHub API/authentication failure while checking CI validation is caught and
   reported as a distinguishable readiness blocker (not a crash, not silently treated
   as validated) - same pattern Task 037 already established for this failure mode.
6. `devbot release preview` becomes `ready: yes` (readiness has no blockers) once the
   target commit has a valid CI push run, the merged PRs since the last stable release
   all carry exactly one `release:*` label, and no other safety condition is violated.

## Quality Gates

### CP-039-1 - Exact target-commit `push` success is recognized

Required test:
- `test_ci_validated_true_for_exact_push_success`

### CP-039-2 - A pull-request run at the pre-merge SHA does not count

Required test:
- `test_ci_validated_false_for_pr_only_success_at_premerge_sha`

### CP-039-3 - A successful run for a different commit does not count

Required test:
- `test_ci_validated_false_for_success_on_another_sha`

### CP-039-4 - No matching run, in-flight runs, and unsuccessful runs all fail closed

Required tests:
- `test_ci_validated_false_when_no_run_found`
- `test_ci_validated_false_for_queued_run`
- `test_ci_validated_false_for_in_progress_run`
- `test_ci_validated_false_for_failed_run`
- `test_ci_validated_false_for_cancelled_run`

### CP-039-5 - API/authentication failure is a clear, non-crashing blocker

Required test:
- `test_gather_release_context_reports_api_failure_as_validation_error`

### CP-039-6 - `devbot release preview` reaches ready with valid CI and labels

Required test:
- `test_release_preview_is_ready_with_valid_ci_push_run_and_labels`

### CP-039-7 - Workflow Runs API is used, not Check Runs, for this check

Required evidence:
- `test_gather_release_context_computes_commit_range_from_compare_api` asserts
  `list_workflow_runs` is called with `(repository, "ci.yml", event="push",
  head_sha=<target>)` and `list_check_runs_for_ref` is never called.

### CP-039-8 - Documentation and evidence

Required evidence:
- `results/039-release-main-ci-validation.md`
- `docs/00-roadmap.md` (Task 039 entry)

### CP-039-9 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Validation Gate

```bash
uv run ruff check .
uv run pytest
```

Demonstrate `devbot release preview` for real against `hjlee83/devbot` at commit
`d0fdb23b89d90fe15662413b5c141b374569b5f0`, showing `target_commit_validated: yes`
(recorded in the Result document, with the remaining blocker being only the
implementer's own uncommitted working tree, not a false CI-validation negative).

`devbot release publish` (real, non-dry-run) must not be run as part of this Task.

## Files Expected to Change

- `src/devbot/github_client.py` (`head_sha` filter on `list_workflow_runs`)
- `src/devbot/release_ops.py` (`_target_commit_is_ci_validated`, `CI_WORKFLOW_FILE`,
  `gather_release_context`)
- `tests/test_release_ops.py`
- `docs/00-roadmap.md`
- `tasks/039-release-main-ci-validation.md`, `results/039-release-main-ci-validation.md`

## Risk

- If a repository ever renames its CI workflow file away from `ci.yml`, this check
  would need `ci_workflow_file` overridden (already an optional parameter on
  `gather_release_context`/`fetch_release_preview`/`publish_release`) - documented, not
  auto-detected, to avoid guessing which of several workflows is "the" CI gate.
- The underlying label-timing race in `release.yml`'s push-triggered `plan-release`
  job (root cause of the specific failed check run observed) remains unfixed; it no
  longer affects `devbot release preview`'s CI-validation check, but the automatic
  per-push release pipeline can still fail its own `plan-release` job for the same
  reason. Documented as a follow-up in the Result document, not fixed here.

## Rollback Strategy

Revert this Task's commits. `list_workflow_runs`'s new `head_sha` parameter is
optional and additive; `list_check_runs_for_ref` and its automerge use are untouched.

## Reviewer Focus

- Confirm `gather_release_context` no longer calls `list_check_runs_for_ref` at all
  (`test_gather_release_context_computes_commit_range_from_compare_api`'s
  `assert_not_called()`).
- Confirm every one of the 10 required test scenarios from the Task request is present
  and actually exercises the described condition (not a same-named stub).
- Confirm `src/devbot/automerge.py`'s use of `list_check_runs_for_ref` is untouched.
- Confirm no real `devbot release publish` was executed and `v0.1.1` was not published.

## Definition of Done

- All Quality Gates and required tests pass (`uv run pytest`, `uv run ruff check .`).
- `devbot release preview` was run for real against `hjlee83/devbot` at the reported
  commit and now shows `target_commit_validated: yes`, recorded in the Result
  document.
- `v0.1.1` was not published.
- Result and PR Evidence record the actual root cause, implementation, and validation
  outcomes.
- DevBot reaches a mergeable state on Issue #83 and its Pull Request.
- Final merge remains manual.

## Result 문서 경로

`results/039-release-main-ci-validation.md`

## Git Rules

- Task Issue: #83
- Branch: `task/039-release-main-ci-validation`
- Result: `results/039-release-main-ci-validation.md`
- Do not create another Issue, Branch, or Pull Request for this Task.
- Do not publish `v0.1.1` as part of this Task.
