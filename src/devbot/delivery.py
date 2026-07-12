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

from devbot.github_client import GitHubIssue
from devbot.github_write_client import GitHubWriteClient, PullRequestInfo
from devbot.models import RepositoryConfig

DEFAULT_VERIFICATION_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "pytest"),
)


class DeliveryError(RuntimeError):
    """Raised when a Git operation (commit or push) fails."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of running every configured verification command."""

    passed: bool
    failed_command: tuple[str, ...] | None = None
    output: str = ""


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
    """Run each command in `repository.local_path`, stopping at the first
    non-zero exit."""
    for command in commands:
        completed = subprocess.run(
            list(command),
            cwd=str(repository.local_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return VerificationResult(
                passed=False,
                failed_command=tuple(command),
                output=(completed.stdout or "") + (completed.stderr or ""),
            )
    return VerificationResult(passed=True)


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


def push_task_branch(repository: RepositoryConfig, branch: str) -> None:
    """Push exactly `branch` to `origin`, never any other ref."""
    _run_git(repository, "push", "origin", f"{branch}:{branch}")


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


@dataclass
class DeliveryService:
    """Runs verification, then (unless `dry_run`) commits, pushes, opens a
    PR, and links it back to the Issue."""

    client: GitHubWriteClient
    dry_run: bool = True
    run_verification: RunVerificationFn = field(default=run_verification_commands)
    commit: CommitFn = field(default=commit_all_changes)
    push: PushFn = field(default=push_task_branch)

    def deliver(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        branch: str,
        checkpoint_evidence: Sequence[CheckpointEvidence],
    ) -> DeliveryResult:
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

        self.commit(repository, build_commit_message(issue))
        self.push(repository, branch)

        pull_request = self.client.create_pull_request(
            repository,
            title=build_commit_message(issue),
            body=build_pr_body(issue, checkpoint_evidence),
            head=branch,
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
