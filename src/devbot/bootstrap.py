"""Bootstrap execution artifacts for a ready Issue.

Planner-created Tasks still remain valid, but a `devbot:ready` Issue may now
arrive without a branch, Task Contract, or PR. This module keeps the
deterministic host-owned pieces in one place so the implementation Agent never
chooses names or expands scope.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from devbot.github_client import GitHubIssue
from devbot.planner import canonical_branch_name, canonical_contract_path, canonical_result_path

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")
_REPEATED_DASHES = re.compile(r"-+")
_TASK_BRANCH_RE = re.compile(r"^task/(?P<number>\d{3,})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$")
_MAX_SLUG_LENGTH = 48


class BootstrapValidationError(RuntimeError):
    """Raised when an Issue is not sufficiently specified for bootstrap."""

    def __init__(self, message: str, *, missing_fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing_fields = missing_fields


@dataclass(frozen=True, slots=True)
class BootstrapIssueMetadata:
    objective: str
    scope: str
    constraints: str
    acceptance_criteria: str
    verification: str
    implementation_context: str


@dataclass(frozen=True, slots=True)
class BootstrapPlan:
    task_number: int
    slug: str
    branch: str
    contract_path: str
    result_path: str


@dataclass(frozen=True, slots=True)
class BranchNamingPolicy:
    """Central deterministic naming policy.

    The preserved Planner pattern is `task/<NNN>-<slug>`, where `<NNN>` is
    zero-padded and `<slug>` is lowercase kebab-case.
    """

    max_slug_length: int = _MAX_SLUG_LENGTH

    def slugify(self, title: str) -> str:
        slug = _SLUG_INVALID_CHARS.sub("-", title.lower())
        slug = _REPEATED_DASHES.sub("-", slug).strip("-")
        slug = slug[: self.max_slug_length].strip("-")
        return slug or "issue"

    def generate(
        self, *, task_number: int, title: str, existing_branches: tuple[str, ...] = ()
    ) -> str:
        base_slug = self.slugify(title)
        branch = canonical_branch_name(task_number, base_slug)
        existing = set(existing_branches)
        if branch not in existing:
            return branch

        suffix = 2
        while True:
            suffix_text = f"-{suffix}"
            slug = base_slug[: self.max_slug_length - len(suffix_text)].strip("-")
            branch = canonical_branch_name(task_number, f"{slug}{suffix_text}")
            if branch not in existing:
                return branch
            suffix += 1

    def parse_slug(self, branch: str) -> str | None:
        match = _TASK_BRANCH_RE.match(branch)
        return match.group("slug") if match else None


def validate_bootstrap_issue_metadata(body: str) -> BootstrapIssueMetadata:
    """Require enough Issue content to create a contract without AI analysis."""

    sections = _markdown_sections(body)
    objective = _require_section(sections, "objective")
    scope = _require_section(sections, "scope", "target behavior")
    constraints = _require_section(sections, "non-goals", "constraints", "out of scope")
    acceptance = _require_section(sections, "acceptance criteria")
    verification = _require_section(
        sections, "verification commands", "verification expectations", "instructions"
    )
    context = _require_section(sections, "implementation context", "current behavior", "background")
    return BootstrapIssueMetadata(
        objective=objective,
        scope=scope,
        constraints=constraints,
        acceptance_criteria=acceptance,
        verification=verification,
        implementation_context=context,
    )


def build_bootstrap_plan(
    issue: GitHubIssue,
    *,
    existing_branches: tuple[str, ...] = (),
    policy: BranchNamingPolicy | None = None,
) -> BootstrapPlan:
    policy = policy or BranchNamingPolicy()
    task_number = issue.number
    branch = policy.generate(
        task_number=task_number, title=issue.title, existing_branches=existing_branches
    )
    slug = policy.parse_slug(branch)
    if slug is None:
        raise BootstrapValidationError(f"generated branch does not match task pattern: {branch}")
    return BootstrapPlan(
        task_number=task_number,
        slug=slug,
        branch=branch,
        contract_path=canonical_contract_path(task_number, slug),
        result_path=canonical_result_path(task_number, slug),
    )


def render_task_contract_from_issue(issue: GitHubIssue, plan: BootstrapPlan) -> str:
    metadata = validate_bootstrap_issue_metadata(issue.body)
    return (
        f"# Task {plan.task_number:03d}: {issue.title}\n\n"
        "## Source\n\n"
        f"- Issue: #{issue.number}\n"
        f"- Branch: `{plan.branch}`\n"
        f"- Result: `{plan.result_path}`\n\n"
        "## Objective\n\n"
        f"{metadata.objective}\n\n"
        "## Scope\n\n"
        f"{metadata.scope}\n\n"
        "## Constraints / Non-goals\n\n"
        f"{metadata.constraints}\n\n"
        "## Acceptance Criteria\n\n"
        f"{metadata.acceptance_criteria}\n\n"
        "## Verification\n\n"
        f"{metadata.verification}\n\n"
        "## Implementation Context\n\n"
        f"{metadata.implementation_context}\n"
    )


def materialize_task_contract(worktree_path: Path, issue: GitHubIssue, plan: BootstrapPlan) -> None:
    contract_path = worktree_path / plan.contract_path
    if contract_path.exists():
        return
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(render_task_contract_from_issue(issue, plan), encoding="utf-8")


def list_existing_task_branches(repository_path: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/task",
            "refs/remotes/origin/task",
        ],
        cwd=str(repository_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ()
    branches: list[str] = []
    for line in completed.stdout.splitlines():
        branch = line.strip().removeprefix("origin/")
        if branch:
            branches.append(branch)
    return tuple(branches)


def _markdown_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            if title:
                current = title
                sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _require_section(sections: dict[str, str], *names: str) -> str:
    for name in names:
        for title, content in sections.items():
            if title == name or title.startswith(f"{name} "):
                if content.strip():
                    return content.strip()
    raise BootstrapValidationError(
        f"missing required Issue metadata: {' or '.join(names)}",
        missing_fields=(names[0],),
    )
