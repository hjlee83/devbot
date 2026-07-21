# Task 151: Comment on Bootstrap Metadata Validation Failures

## Issue

- GitHub Issue: #151
- Branch: `task/151-bootstrap-metadata-diagnostic-comments`
- Pull Request: TBD

## Goal

When bootstrap validation fails because a ready Issue is missing required
metadata, DevBot should leave a deterministic, actionable GitHub Issue comment
without changing the existing fail-closed state transition.

## Scope

- Detect bootstrap validation failures caused by missing Issue metadata.
- Include the validation reason, missing fields, and a minimal valid Issue body
  template in the comment.
- Suppress duplicate comments for the same unchanged validation failure.
- Keep the existing `working -> ready` restore behavior.
- Treat comment read/write failures as best-effort diagnostics that do not
  change the original workspace-preparation result.
- Do not invoke an AI model for the diagnostic comment.

## Acceptance Criteria

1. An Issue missing `objective` receives an actionable diagnostic comment.
2. The comment includes a minimal valid example containing required metadata fields.
3. Repeated polling of an unchanged invalid Issue does not spam duplicate comments.
4. After the Issue body is corrected, DevBot can claim and process it normally.
5. Other workspace-preparation failures do not receive a metadata-template comment.
6. Failure to post the comment does not crash the daemon or change the original result.
7. Tests cover first comment, duplicate suppression, corrected Issue, and comment failure.
8. Existing tests pass.

## Required Tests

- `test_bootstrap_metadata_failure_posts_actionable_comment`
- `test_bootstrap_metadata_failure_comment_is_not_duplicated`
- `test_corrected_bootstrap_issue_processes_after_metadata_failure`
- `test_bootstrap_metadata_comment_failure_preserves_original_result`
- `test_workspace_preparation_failure_skips_agent_and_recovers_state`

