"""Blocked-job diagnostics and manual resume helpers."""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.models import JobType, RepositoryConfig, TaskState
from devbot.worktree import parse_branch_from_issue_body, parse_contract_path_from_issue_body

BLOCKED_DIAGNOSTIC_MARKER = "<!-- devbot-blocked-diagnostic:v1"


@dataclass(frozen=True, slots=True)
class BlockedDiagnosticContext:
    repository: RepositoryConfig
    issue: GitHubIssue
    job_type: JobType
    stage: str
    branch: str | None = None
    worktree_path: Path | None = None
    command: str | None = None
    changed_files: tuple[str, ...] = ()


def blocked_diagnostic_marker(*, stage: str, exception_type: str, message: str) -> str:
    signature = f"{stage}:{exception_type}:{message}"
    return f"{BLOCKED_DIAGNOSTIC_MARKER} signature={signature!r} -->"


def has_blocked_diagnostic(
    comments: list[PullRequestComment], *, stage: str, exception_type: str, message: str
) -> bool:
    marker = blocked_diagnostic_marker(
        stage=stage, exception_type=exception_type, message=message
    )
    return any(marker in comment.body for comment in comments)


def _missing_target(exc: BaseException) -> str:
    filename = getattr(exc, "filename", None)
    if filename:
        return str(filename)
    if isinstance(exc, FileNotFoundError) and exc.args:
        return str(exc.args[-1])
    return "-"


def _traceback_excerpt(exc: BaseException, *, limit: int = 6) -> str:
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    excerpt = "".join(lines[-limit:]).strip()
    for key, value in os.environ.items():
        if any(token in key.upper() for token in ("TOKEN", "SECRET", "KEY", "PASSWORD")) and value:
            excerpt = excerpt.replace(value, "***")
    return excerpt


def render_blocked_diagnostic_comment(
    context: BlockedDiagnosticContext,
    exc: BaseException,
    *,
    recovery: Literal["automatic", "manual", "non-resumable"] = "manual",
) -> str:
    exception_type = type(exc).__name__
    message = str(exc) or repr(exc)
    changed = "\n".join(f"- {path}" for path in context.changed_files) or "-"
    branch = context.branch or parse_branch_from_issue_body(context.issue.body) or "-"
    worktree = str(context.worktree_path) if context.worktree_path is not None else "-"
    command = context.command or "-"
    marker = blocked_diagnostic_marker(
        stage=context.stage, exception_type=exception_type, message=message
    )
    return f"""{marker}
DevBot blocked this job with a structured diagnostic.

Failure:
- stage: {context.stage}
- category: blocked
- exception: {exception_type}
- message: {message}
- missing executable or path: {_missing_target(exc)}
- command: {command}

Context:
- repository: {context.repository.full_name}
- issue: #{context.issue.number}
- job_type: {context.job_type.value}
- branch: {branch}
- worktree: {worktree}
- changed files:
{changed}

Recovery classification: {recovery}

Recovery steps:
1. Repair the missing executable, file, path, or environment problem above.
2. Inspect the preserved worktree and keep any useful changes.
3. Run `devbot resume {context.repository.full_name} {context.issue.number}`.
4. Let the daemon continue the task from the preserved branch/worktree.

Traceback excerpt:
```text
{_traceback_excerpt(exc)}
```
"""


@dataclass(frozen=True, slots=True)
class ResumeValidation:
    ok: bool
    message: str
    branch: str | None = None
    worktree_path: Path | None = None


def validate_blocked_resume(
    *,
    repository: RepositoryConfig,
    issue: GitHubIssue,
    worktree_path: Path,
) -> ResumeValidation:
    if TaskState.BLOCKED.value not in {
        label.removeprefix("devbot:") for label in issue.labels if label.startswith("devbot:")
    }:
        return ResumeValidation(False, "Issue가 devbot:blocked 상태가 아닙니다.")
    if not worktree_path.exists():
        return ResumeValidation(
            False,
            f"보존된 worktree가 없습니다: {worktree_path}. "
            "devbot:ready를 다시 적용해 새로 시작하세요.",
        )
    if not (worktree_path / ".git").exists():
        return ResumeValidation(
            False,
            f"worktree가 Git checkout이 아닙니다: {worktree_path}. "
            "필요하면 devbot worktree cleanup으로 정리하세요.",
        )
    contract_path = parse_contract_path_from_issue_body(issue.body)
    if contract_path is None:
        return ResumeValidation(
            False,
            "Task Contract 경로가 Issue 본문에 선언되어 있지 않아 resume을 거부합니다.",
        )
    worktree_root = worktree_path.resolve()
    contract_file = (worktree_path / contract_path).resolve()
    if not contract_file.is_relative_to(worktree_root):
        return ResumeValidation(
            False,
            f"Task Contract 경로가 worktree 밖을 가리킵니다: {contract_path}. "
            "Issue metadata를 수정하세요.",
        )
    if not contract_file.exists():
        return ResumeValidation(
            False,
            f"Task Contract가 worktree에 없습니다: {contract_path}. worktree 상태를 확인하세요.",
        )
    return ResumeValidation(
        True,
        "resume 가능: blocked Issue를 devbot:ready로 되돌립니다.",
        branch=parse_branch_from_issue_body(issue.body),
        worktree_path=worktree_path,
    )
