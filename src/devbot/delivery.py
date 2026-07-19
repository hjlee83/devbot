"""Delivering an implementation: verify, commit, push, open a PR, and link
it back to the Issue.

`DeliveryService.deliver()` only commits/pushes/opens a PR once every
verification command has passed; a failed command leaves the local
workspace and the GitHub Issue exactly as they were (no partial commit,
push, or PR). `dry_run=True` (the default) runs verification but performs
none of the GitHub or Git side effects.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from devbot.github_client import GitHubIssue, PullRequest
from devbot.github_write_client import GitHubWriteClient, PullRequestInfo
from devbot.models import RepositoryConfig
from devbot.validation import (
    DEFAULT_VALIDATION_COMMANDS,
    ValidationFailureCategory,
    classify_validation_failure,
    run_validation_command,
    validation_commands_with_environment,
    workspace_validation_env,
)

DEFAULT_VERIFICATION_COMMANDS = DEFAULT_VALIDATION_COMMANDS


class DeliveryError(RuntimeError):
    """Raised when a Git operation (commit or push) fails."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of running every configured verification command."""

    passed: bool
    failed_command: tuple[str, ...] | None = None
    output: str = ""
    workspace_path: str = ""
    commands: tuple[tuple[str, ...], ...] = ()
    failure_category: ValidationFailureCategory | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class CheckpointEvidence:
    """One row of a Task's checkpoint-to-test evidence table."""

    checkpoint_id: str
    test_names: tuple[str, ...]
    result: str = "PASS"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Structured outcome of one `DeliveryService.deliver()` call."""

    verification: VerificationResult
    committed: bool
    pushed: bool
    pull_request: PullRequestInfo | None
    dry_run: bool
    message: str = ""


def run_verification_commands(
    repository: RepositoryConfig,
    commands: Sequence[Sequence[str]] = DEFAULT_VERIFICATION_COMMANDS,
) -> VerificationResult:
    """Run validation from `repository.local_path` using its own `.venv`.

    `uv sync` is always run first so a prepared worktree has a usable local
    environment. Parent `VIRTUAL_ENV` is stripped and PATH is biased toward the
    workspace `.venv/bin` to prevent fallback to the operator checkout's
    environment.
    """
    env = workspace_validation_env(repository)
    executed_commands = validation_commands_with_environment(commands)
    host_checkout_path = repository.host_checkout_path or repository.local_path
    for command in executed_commands:
        execution = run_validation_command(repository, command, env=env)
        if execution.returncode != 0:
            return VerificationResult(
                passed=False,
                failed_command=tuple(command),
                output=execution.output,
                workspace_path=str(repository.local_path),
                commands=executed_commands,
                failure_category=classify_validation_failure(
                    command=command,
                    returncode=execution.returncode,
                    output=execution.output,
                    host_checkout_path=str(host_checkout_path),
                    workspace_path=str(repository.local_path),
                ),
                exit_code=execution.returncode,
            )
    return VerificationResult(
        passed=True,
        workspace_path=str(repository.local_path),
        commands=executed_commands,
    )


def build_commit_message(issue: GitHubIssue) -> str:
    """A commit message that always references the Issue it delivers."""
    return f"{issue.title} (#{issue.number})"


def _run_git(repository: RepositoryConfig, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repository.local_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeliveryError(
            f"git {' '.join(args)} failed in {repository.local_path}: "
            f"{completed.stderr or completed.stdout}"
        )


def commit_all_changes(repository: RepositoryConfig, message: str) -> None:
    """Stage every change in the workspace and commit it with `message`."""
    _run_git(repository, "add", "-A")
    _run_git(repository, "commit", "-m", message)


def repository_has_changes(repository: RepositoryConfig) -> bool:
    """Return True when the Git workspace has staged or unstaged changes."""
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repository.local_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeliveryError(
            f"git status --porcelain failed in {repository.local_path}: "
            f"{completed.stderr or completed.stdout}"
        )
    return bool(completed.stdout.strip())


def push_task_branch(repository: RepositoryConfig, branch: str) -> None:
    """Push exactly `branch` to `origin`, never any other ref."""
    _run_git(repository, "push", "origin", f"{branch}:{branch}")


def local_branch_exists(repository: RepositoryConfig, branch: str) -> bool:
    """True when `branch` exists as a local ref.

    Checked before `push_task_branch` (Task 016 CP-016-11) so a missing
    branch fails as a clean, structured `delivery_branch_invalid` result
    instead of surfacing as Git's own `src refspec ... does not match
    any` from inside `push_task_branch` after the fact."""
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(repository.local_path),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def branch_has_implementation_evidence(
    repository: RepositoryConfig, branch: str, base_branch: str
) -> bool:
    """True when `branch` has more than one commit ahead of `base_branch`
    (Task 021 Scope §7/§8) - i.e. more than just a single Task-contract-
    authoring commit that opened the branch/PR, so there is git history
    evidence of real implementation work beyond that contract-only commit.

    Best-effort and conservative: any git failure (missing local ref,
    unreachable base, a workspace path that does not exist yet, ...) is
    treated as "no evidence" rather than raised - this check exists
    specifically to prevent a false "implementation completed" positive
    (CP-021-11), so an inconclusive result must never default to True."""
    try:
        completed = subprocess.run(
            ["git", "rev-list", "--count", f"{base_branch}..{branch}"],
            cwd=str(repository.local_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if completed.returncode != 0:
        return False
    try:
        return int(completed.stdout.strip()) > 1
    except ValueError:
        return False


def current_git_branch(repository: RepositoryConfig) -> str:
    """Return the branch currently checked out in `repository.local_path`
    (the literal string `"HEAD"` if the checkout is detached)."""
    completed = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repository.local_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeliveryError(
            f"git rev-parse --abbrev-ref HEAD failed in {repository.local_path}: "
            f"{completed.stderr or completed.stdout}"
        )
    return completed.stdout.strip()


def build_pr_body(issue: GitHubIssue, checkpoint_evidence: Sequence[CheckpointEvidence]) -> str:
    """Render a PR body that closes the Issue and lists every checkpoint's
    test-name mapping and result."""
    rows = "\n".join(
        f"| {evidence.checkpoint_id} | {', '.join(f'`{name}`' for name in evidence.test_names)} "
        f"| {evidence.result} |"
        for evidence in checkpoint_evidence
    )
    return (
        f"Closes #{issue.number}\n\n"
        "## Checkpoint evidence\n\n"
        "| Checkpoint | Test name(s) | Result |\n"
        "|---|---|---|\n" + rows + "\n"
    )


RunVerificationFn = Callable[[RepositoryConfig], VerificationResult]
CommitFn = Callable[[RepositoryConfig, str], None]
PushFn = Callable[[RepositoryConfig, str], None]
CurrentBranchFn = Callable[[RepositoryConfig], str]
HasChangesFn = Callable[[RepositoryConfig], bool]
BranchExistsFn = Callable[[RepositoryConfig, str], bool]


@dataclass
class DeliveryService:
    """Runs verification, then (unless `dry_run`) commits, pushes, opens a
    PR, and links it back to the Issue."""

    client: GitHubWriteClient
    dry_run: bool = True
    run_verification: RunVerificationFn = field(default=run_verification_commands)
    commit: CommitFn = field(default=commit_all_changes)
    push: PushFn = field(default=push_task_branch)
    has_changes: HasChangesFn = field(default=repository_has_changes)
    branch_exists: BranchExistsFn = field(default=local_branch_exists)
    current_branch: CurrentBranchFn = field(default=current_git_branch)

    def deliver(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        branch: str,
        checkpoint_evidence: Sequence[CheckpointEvidence],
        *,
        linked_pull_request: PullRequest | None = None,
    ) -> DeliveryResult:
        """`branch` is only the fallback: when `linked_pull_request` is
        given, its head branch is used instead and no new
        `devbot/devbot-*` branch is ever created or pushed to (Task 016
        CP-016-10) - `linked_pull_request` is present exactly when the
        Issue already has an open PR (e.g. a retried IMPLEMENT job), so
        reusing its branch and PR is required, not merely preferred."""
        verification = self.run_verification(repository)
        if not verification.passed:
            return DeliveryResult(
                verification=verification,
                committed=False,
                pushed=False,
                pull_request=None,
                dry_run=self.dry_run,
                message=f"Verification failed: {' '.join(verification.failed_command or ())}",
            )

        if self.dry_run:
            return DeliveryResult(
                verification=verification,
                committed=False,
                pushed=False,
                pull_request=None,
                dry_run=True,
                message="[dry-run] verification passed; no commit, push, or PR",
            )

        target_branch = linked_pull_request.head_ref if linked_pull_request is not None else branch

        # Task 023 Scope §7: "Delivery must reject branch mismatch before
        # commit or push." A prepared worktree should already be checked
        # out on `target_branch`, but this must never be assumed silently -
        # `self.commit()` always commits to whatever is actually checked
        # out (Git has no notion of "the branch the caller meant"), so an
        # unverified mismatch here would commit real work onto the wrong
        # local branch and then push `target_branch`'s *unrelated*, unmoved
        # ref instead - silently discarding the Agent's changes or (worse)
        # applying the previous run's stale ref while reporting success.
        try:
            actual_branch = self.current_branch(repository)
        except Exception as exc:  # noqa: BLE001 - must reject cleanly, not raise
            return DeliveryResult(
                verification=verification,
                committed=False,
                pushed=False,
                pull_request=None,
                dry_run=False,
                message=f"delivery_branch_mismatch: unable to determine current branch: {exc!r}",
            )
        if actual_branch != target_branch:
            return DeliveryResult(
                verification=verification,
                committed=False,
                pushed=False,
                pull_request=None,
                dry_run=False,
                message=(
                    f"delivery_branch_mismatch: expected {target_branch!r}, "
                    f"actual checked-out branch {actual_branch!r}"
                ),
            )

        if not self.has_changes(repository):
            # CP-016-12: no commit was created (a clean workspace after a
            # no-op/already-implemented Agent run), so there is nothing to
            # push - this is `no_repository_changes`, not a `DeliveryError`.
            return DeliveryResult(
                verification=verification,
                committed=False,
                pushed=False,
                pull_request=None,
                dry_run=False,
                message="no_repository_changes",
            )

        self.commit(repository, build_commit_message(issue))

        if not self.branch_exists(repository, target_branch):
            # CP-016-11: fail cleanly before `git push` instead of letting
            # Git's own `src refspec ... does not match any` surface as an
            # uncaught `DeliveryError` after the fact.
            return DeliveryResult(
                verification=verification,
                committed=True,
                pushed=False,
                pull_request=None,
                dry_run=False,
                message=f"delivery_branch_invalid: local branch {target_branch!r} not found",
            )

        self.push(repository, target_branch)

        if linked_pull_request is not None:
            pull_request = PullRequestInfo(
                number=linked_pull_request.number, html_url=linked_pull_request.html_url
            )
            evidence_body = build_pr_body(issue, checkpoint_evidence)
            self.client.create_comment(
                repository,
                issue.number,
                f"Updated pull request: {pull_request.html_url}\n\n{evidence_body}",
            )
        else:
            pull_request = self.client.create_pull_request(
                repository,
                title=build_commit_message(issue),
                body=build_pr_body(issue, checkpoint_evidence),
                head=target_branch,
                base=repository.default_branch,
            )
            self.client.create_comment(
                repository, issue.number, f"Opened pull request: {pull_request.html_url}"
            )

        return DeliveryResult(
            verification=verification,
            committed=True,
            pushed=True,
            pull_request=pull_request,
            dry_run=False,
            message="delivered",
        )
