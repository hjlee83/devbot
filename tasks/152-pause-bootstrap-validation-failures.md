# Task 152: Pause Bootstrap Validation Failures Until Issue Update

## Issue

- GitHub Issue: #152
- Branch: `task/152-pause-bootstrap-validation-failures`
- Base: `task/151-bootstrap-metadata-diagnostic-comments` until #151 is merged
- Pull Request: TBD

## Goal

After a deterministic bootstrap metadata validation failure, DevBot should stop
retrying the unchanged invalid Issue every polling cycle and resume when the
Issue body changes.

## Scope

- Reuse the deterministic diagnostic comment path from Task 151.
- Mark bootstrap metadata failures with a validation-pause label while keeping
  the Issue in `devbot:ready`.
- Exclude unchanged paused Issues from scheduling.
- Resume automatically when the Issue body no longer matches the diagnostic
  marker's stored body hash.
- Do not pause transient workspace preparation failures.

## Acceptance Criteria

1. Invalid metadata causes one diagnostic comment.
2. The Issue is not selected again while unchanged.
3. Updating the Issue body automatically makes it schedulable again.
4. No duplicate comments are created during polling.
5. Transient failures continue to follow existing retry behavior.
6. Existing queue and state-machine tests continue to pass.

## Required Tests

- `test_paused_bootstrap_metadata_failure_is_not_selected_again`
- `test_paused_bootstrap_metadata_failure_resumes_when_issue_body_changes`
- `test_transient_workspace_preparation_failure_does_not_pause_issue`
- `test_bootstrap_metadata_failure_comment_is_not_duplicated`
- `test_corrected_bootstrap_issue_processes_after_metadata_failure`

