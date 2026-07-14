# State Machine

## IDLE
No Issue is `working` or `review`.

- Query all enabled repositories.
- Select one `ready` Issue using the global priority rules.
- Atomically claim it by moving it to `working`.

## WORKING
The DevBot owns one Issue.

- Prepare branch and prompt.
- Run the agent.
- Run verification.
- Repeat repair until success or a defined stop condition.
- On success, create PR and move Issue to `review`.
- On metadata-only or external-verification work that cannot be solved by a repository commit, move Issue to `manual-action`.
- On unrecoverable failure, move Issue to `blocked`.

## REVIEW
No new Issue may start.

- Wait for PR merge or a new `@devbot` modification request.
- Modification request: move back to `working` and continue on the same branch/PR.
- Merge: move to `done`.

## BLOCKED
No automated retry unless explicitly returned to `ready`.

## MANUAL_ACTION
The Issue needs GitHub metadata work, external verification, or human
approval. It is not selected for automatic repository rework and does not
mean the Agent or delivery path failed.
