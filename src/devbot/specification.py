"""Task 042: Specification Generator.

`devbot specification generate --task <task-number>` produces a
deterministic, evidence-grounded `specifications/NNN-slug.md` - intended as
the authoritative implementation document a future Agent Dispatch (Task
041's Router, extended later) would hand to an implementation Agent instead
of a manually written prompt. Task 042 only *prepares* this interface: it
does not change `devbot.workspace.build_agent_prompt`, `devbot.polling`,
`devbot.review`, `devbot.rework`, or how any Agent is actually invoked.

**Grounding is the entire point.** A Specification is generated only from
real repository evidence: the Task's GitHub Issue, its Task Contract
(`tasks/NNN-slug.md`), and (optionally) its `docs/00-roadmap.md` entry. This
repository's 41 prior Task Contracts use noticeably different heading
vocabularies (English *and* Korean - "Goal"/"목표", "Out of Scope"/"제외
범위", "Git Rules"/"Git 규칙", ...), so a best-effort alias table
(`_SECTION_ALIASES`) maps the observed variants into this module's fixed
Specification sections. Whenever no alias matches, the Specification says so
explicitly ("Not specified in the Task Contract") rather than guessing - and
the complete, unmodified Contract text is *always* appended verbatim, so
nothing this module's section mapping misses is ever lost or contradicted.

**Deterministic.** `render_specification()` is a pure function of an already
-gathered `TaskEvidence` value: no timestamps, no random ordering, no
network calls inside it. Given identical repository state (same Contract
file content, same Issue title/body, same roadmap text), two calls produce
byte-identical output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devbot.github_client import GitHubClient
from devbot.goal_planner import parse_roadmap
from devbot.models import RepositoryConfig

TASKS_DIR = Path("tasks")
SPECIFICATIONS_DIR = Path("specifications")
ROADMAP_PATH = Path("docs/00-roadmap.md")

_NOT_SPECIFIED = "Not specified in the Task Contract."

_CONTRACT_TITLE_RE = re.compile(r"^#\s+Task\s+(\d{3}):\s*(.+?)\s*$")
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_GOAL_EXECUTOR_GOAL_RE = re.compile(r'from the Goal:\s*"(.+?)"')

# Best-effort mapping from this module's fixed Specification concepts to the
# literal `## ` heading text observed across tasks/*.md (English and Korean
# both appear - earlier Tasks were written entirely in Korean). Order within
# each tuple does not matter; every matching heading's body is concatenated.
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "goal": ("Goal", "목표"),
    "background": ("Context", "Background", "Motivation"),
    "prerequisites": ("Dependencies", "선행 조건"),
    "in_scope": ("In Scope", "In scope", "포함 범위", "Scope"),
    "out_of_scope": ("Out of Scope", "Out of scope", "제외 범위"),
    "functional_requirements": (
        "Functional Requirements",
        "Functional requirements",
        "핵심 규칙",
        "CLI 요구사항",
        "설계 요구사항",
        "Required Behavior",
        "Required structure",
    ),
    "quality_gates": (
        "Quality Gates",
        "Quality gates",
        "Checkpoints",
        "Required Checkpoints",
        "품질 게이트",
    ),
    "validation": (
        "Validation Gate",
        "Verification Gate",
        "Verification gates",
        "Verification commands",
        "검증 명령",
        "테스트 품질 요구사항",
    ),
    "files_changed": ("Files Expected to Change",),
    "risk": ("Risk",),
    "rollback": ("Rollback Strategy",),
    "definition_of_done": ("Definition of Done", "완료 조건"),
    "deliverables": ("Deliverables",),
    "result_path": (
        "Result 문서 경로",
        "Result 문서",
        "Result",
        "Result Requirements",
        "Result and PR evidence",
    ),
    "git_rules": (
        "Git Rules",
        "Git rules",
        "Git 규칙",
        "Git and PR",
        "Branch and PR Policy",
        "Branch / PR Policy",
        "PR evidence requirements",
        "PR 요구사항",
    ),
}

# The `# <heading>` structure every generated Specification must contain -
# the "Output schema" CP-042 tests check against this list.
REQUIRED_TOP_LEVEL_SECTIONS: tuple[str, ...] = (
    "# Overview",
    "# Functional Requirements",
    "# Technical Design",
    "# Validation",
    "# Safety",
    "# Completion",
    "# Handoff",
)


class SpecificationError(RuntimeError):
    """Base class for a Specification that cannot be safely generated."""


class InvalidTaskError(SpecificationError):
    """`task_number` is not a valid positive integer, or the on-disk
    Contract does not declare the Task number it is supposed to."""


class ContractMissingError(SpecificationError):
    """No `tasks/NNN-*.md` file exists for this Task number."""


class ContractAmbiguousError(SpecificationError):
    """More than one `tasks/NNN-*.md` file matches this Task number."""


class IssueMissingError(SpecificationError):
    """No GitHub Issue titled `Task NNN: ...` exists."""


class AmbiguousTaskError(SpecificationError):
    """More than one GitHub Issue matches this Task number, or the Issue's
    title disagrees with the Contract's own title - the repository does not
    have one unambiguous answer for what this Task is."""


class PlannerEvidenceMissingError(SpecificationError):
    """The Contract exists but has no identifiable planning content at all
    (no Goal/Background/Scope/Functional Requirements section) - too little
    evidence to ground a Specification safely."""


@dataclass(frozen=True)
class ContractSections:
    title: str
    full_text: str
    sections: dict[str, str]


@dataclass(frozen=True)
class TaskEvidence:
    task_number: int
    slug: str
    contract_path: Path
    contract: ContractSections
    issue_number: int
    issue_title: str
    issue_body: str
    issue_url: str
    goal_text: str | None
    roadmap_excerpt: str | None


@dataclass(frozen=True)
class Specification:
    task_number: int
    slug: str
    path: Path
    content: str


def _fenced_code_ranges(text: str) -> list[tuple[int, int]]:
    """Character-offset (start, end) ranges covered by ``` fenced code blocks,
    so illustrative `#`/`##` text inside an example (e.g. a Specification
    Structure template) is never mistaken for a real section heading."""
    ranges: list[tuple[int, int]] = []
    fence_start: int | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            if fence_start is None:
                fence_start = offset
            else:
                ranges.append((fence_start, offset + len(line)))
                fence_start = None
        offset += len(line)
    if fence_start is not None:
        ranges.append((fence_start, len(text)))
    return ranges


def _split_level2_sections(text: str) -> dict[str, str]:
    """Split `text` on `## ` headings only - a deliberately shallow parse.
    Deeper (`### `) headings stay inside their parent section's body, so
    nothing under them is ever dropped. Headings inside ``` fenced code
    blocks are ignored - they are illustrative example text, not real
    section boundaries."""
    fenced = _fenced_code_ranges(text)
    matches = [
        match
        for match in _SECTION_HEADING_RE.finditer(text)
        if not any(start <= match.start() < end for start, end in fenced)
    ]
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections[heading] = f"{sections[heading]}\n\n{body}" if heading in sections else body
    return sections


def parse_contract(text: str) -> ContractSections:
    lines = text.splitlines()
    title_match = _CONTRACT_TITLE_RE.match(lines[0]) if lines else None
    title = title_match.group(2) if title_match else ""
    return ContractSections(title=title, full_text=text, sections=_split_level2_sections(text))


def _lookup(sections: dict[str, str], concept: str) -> str | None:
    parts = [sections[heading] for heading in _SECTION_ALIASES[concept] if heading in sections]
    return "\n\n".join(parts) if parts else None


def _section_or_default(
    sections: dict[str, str], concept: str, *, default: str | None = None
) -> str:
    value = _lookup(sections, concept)
    if value:
        return value
    return default if default is not None else _NOT_SPECIFIED


def _find_contract_path(task_number: int, tasks_dir: Path) -> Path:
    matches = sorted(tasks_dir.glob(f"{task_number:03d}-*.md"))
    if not matches:
        raise ContractMissingError(
            f"no Task Contract found for Task {task_number:03d} in {tasks_dir}"
        )
    if len(matches) > 1:
        joined = ", ".join(str(match) for match in matches)
        raise ContractAmbiguousError(
            f"multiple candidate Contracts for Task {task_number:03d}: {joined}"
        )
    return matches[0]


def _find_task_issue(github_client: GitHubClient, repository: RepositoryConfig, task_number: int):
    pattern = re.compile(rf"^Task {task_number:03d}:\s*(.+)$")
    matches = []
    for issue in github_client.list_issues(repository, state="all"):
        match = pattern.match(issue.title)
        if match:
            matches.append((issue, match.group(1).strip()))
    if not matches:
        raise IssueMissingError(f"no GitHub Issue titled 'Task {task_number:03d}: ...' found")
    if len(matches) > 1:
        numbers = ", ".join(f"#{issue.number}" for issue, _ in matches)
        raise AmbiguousTaskError(
            f"multiple GitHub Issues match Task {task_number:03d}: {numbers}"
        )
    return matches[0]


def gather_task_evidence(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    task_number: int,
    *,
    tasks_dir: Path = TASKS_DIR,
    roadmap_path: Path = ROADMAP_PATH,
) -> TaskEvidence:
    """Read-only: gathers the Contract, GitHub Issue, and (optional) roadmap
    entry for `task_number`, cross-checking that they agree before returning
    - never writes anything."""
    if task_number <= 0:
        raise InvalidTaskError(f"invalid task number: {task_number}")

    contract_path = _find_contract_path(task_number, tasks_dir)
    contract_text = contract_path.read_text(encoding="utf-8")
    contract = parse_contract(contract_text)
    if not contract.title:
        raise InvalidTaskError(
            f"{contract_path} does not start with the canonical "
            f"'# Task {task_number:03d}: <title>' heading"
        )

    issue, issue_title_suffix = _find_task_issue(github_client, repository, task_number)
    if issue_title_suffix != contract.title:
        raise AmbiguousTaskError(
            f"Task {task_number:03d}: Contract title {contract.title!r} does not match "
            f"Issue #{issue.number} title {issue_title_suffix!r}"
        )

    has_core_content = any(
        _lookup(contract.sections, concept) is not None
        for concept in ("goal", "background", "functional_requirements", "in_scope")
    )
    if not has_core_content:
        raise PlannerEvidenceMissingError(
            f"Task {task_number:03d}'s Contract has no identifiable Goal, Background, "
            "Scope, or Functional Requirements content"
        )

    goal_match = _GOAL_EXECUTOR_GOAL_RE.search(issue.body)
    goal_text = goal_match.group(1) if goal_match else None

    slug = contract_path.stem[len(f"{task_number:03d}-") :]

    roadmap_excerpt: str | None = None
    if roadmap_path.is_file():
        entry = next(
            (
                entry
                for entry in parse_roadmap(roadmap_path.read_text(encoding="utf-8"))
                if entry.number == task_number
            ),
            None,
        )
        if entry is not None:
            roadmap_excerpt = entry.text

    return TaskEvidence(
        task_number=task_number,
        slug=slug,
        contract_path=contract_path,
        contract=contract,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body,
        issue_url=f"https://github.com/{repository.owner}/{repository.repo}/issues/{issue.number}",
        goal_text=goal_text,
        roadmap_excerpt=roadmap_excerpt,
    )


def specification_path(
    task_number: int, slug: str, *, directory: Path = SPECIFICATIONS_DIR
) -> Path:
    return directory / f"{task_number:03d}-{slug}.md"


def render_specification(evidence: TaskEvidence) -> str:
    """Pure: renders the full Specification markdown from already-gathered
    `evidence`. No network calls, no filesystem writes, no randomness - the
    same `evidence` value always renders to the same string."""
    sections = evidence.contract.sections
    goal = evidence.goal_text or _section_or_default(sections, "goal")
    in_scope = _section_or_default(sections, "in_scope")
    out_of_scope = _section_or_default(sections, "out_of_scope")
    background = _section_or_default(sections, "background")
    functional_requirements = _section_or_default(sections, "functional_requirements")
    quality_gates = _section_or_default(sections, "quality_gates")
    files_changed = _section_or_default(sections, "files_changed")
    prerequisites = _section_or_default(sections, "prerequisites")
    risk = _section_or_default(sections, "risk")
    rollback = _section_or_default(sections, "rollback")
    validation = _section_or_default(sections, "validation")
    definition_of_done = _section_or_default(sections, "definition_of_done")
    deliverables = _section_or_default(sections, "deliverables", default=definition_of_done)
    default_result_path = f"`results/{evidence.task_number:03d}-{evidence.slug}.md`"
    result_path_section = _section_or_default(sections, "result_path", default=default_result_path)
    git_rules = _section_or_default(sections, "git_rules")
    roadmap_excerpt = evidence.roadmap_excerpt or "Not specified in docs/00-roadmap.md."

    lines = [
        f"# Specification: Task {evidence.task_number:03d} — {evidence.contract.title}",
        "",
        "## Provenance",
        "",
        f"- Task Issue: [#{evidence.issue_number}]({evidence.issue_url})",
        f"- Task Contract: `{evidence.contract_path.as_posix()}`",
        "- Generated by `devbot specification generate` (Task 042) from repository "
        "evidence only - Goal, Issue, Task Contract, Roadmap. No speculative content.",
        "",
        "# Overview",
        "",
        "## Goal",
        "",
        goal,
        "",
        "## Scope",
        "",
        "In scope:",
        "",
        in_scope,
        "",
        "Out of scope:",
        "",
        out_of_scope,
        "",
        "## Background",
        "",
        background,
        "",
        "## Roadmap Context",
        "",
        roadmap_excerpt,
        "",
        "# Functional Requirements",
        "",
        "## Required Behaviour",
        "",
        functional_requirements,
        "",
        "## Acceptance Criteria",
        "",
        quality_gates,
        "",
        "## Out of Scope",
        "",
        out_of_scope,
        "",
        "# Technical Design",
        "",
        "## Architecture",
        "",
        "Not specified in the Task Contract - see Functional Requirements above and "
        "the full Task Contract reference below.",
        "",
        "## Files Expected to Change",
        "",
        files_changed,
        "",
        "## Dependencies",
        "",
        prerequisites,
        "",
        "## Constraints",
        "",
        risk,
        "",
        "## Migration Notes",
        "",
        rollback,
        "",
        "# Validation",
        "",
        "## Required Tests and Quality Gates",
        "",
        quality_gates,
        "",
        "## Validation Commands",
        "",
        validation,
        "",
        "## Success Criteria",
        "",
        definition_of_done,
        "",
        "# Safety",
        "",
        "## Things the Implementation Agent Must NOT Do",
        "",
        out_of_scope,
        "",
        risk,
        "",
        "# Completion",
        "",
        "## Expected Deliverables",
        "",
        deliverables,
        "",
        "## Result Document",
        "",
        result_path_section,
        "",
        "## PR Expectations",
        "",
        git_rules,
        "",
        "# Handoff",
        "",
        "## Required Handoff Procedure",
        "",
        "Not specified in the Task Contract. Follow `docs/12-planner-workflow.md`'s "
        "Implementer role boundary: continue only on the existing Branch/Pull Request, "
        "produce the Result Document above, and do not create another Issue, Branch, "
        "or Pull Request.",
        "",
        "## Token-Limit Behaviour",
        "",
        "Not specified in the Task Contract.",
        "",
        "# Full Task Contract Reference",
        "",
        "The complete, unmodified Task Contract this Specification was generated "
        "from. Nothing above should ever contradict this; if it does, this section "
        "is authoritative:",
        "",
        "```markdown",
        evidence.contract.full_text.rstrip("\n"),
        "```",
        "",
    ]
    return "\n".join(lines)


def validate_specification_schema(content: str) -> tuple[str, ...]:
    """Returns the subset of `REQUIRED_TOP_LEVEL_SECTIONS` missing from
    `content` - empty when the schema is satisfied."""
    return tuple(heading for heading in REQUIRED_TOP_LEVEL_SECTIONS if heading not in content)


def generate_specification(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    task_number: int,
    *,
    tasks_dir: Path = TASKS_DIR,
    roadmap_path: Path = ROADMAP_PATH,
) -> Specification:
    """Gather evidence and render the Specification - does not write
    anything to disk. Callers decide whether to persist `.content` (`devbot
    specification generate`) or just display it (`devbot specification
    show`)."""
    evidence = gather_task_evidence(
        github_client, repository, task_number, tasks_dir=tasks_dir, roadmap_path=roadmap_path
    )
    content = render_specification(evidence)
    missing = validate_specification_schema(content)
    if missing:  # pragma: no cover - defensive; the fixed template always satisfies this
        raise SpecificationError(f"generated Specification is missing sections: {missing}")
    return Specification(
        task_number=task_number,
        slug=evidence.slug,
        path=specification_path(task_number, evidence.slug),
        content=content,
    )


def write_specification(
    specification: Specification, *, directory: Path = SPECIFICATIONS_DIR
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / specification.path.name
    path.write_text(specification.content, encoding="utf-8")
    return path
