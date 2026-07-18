"""Task 038: Goal-based Planning (Operator Planner).

`devbot goal plan "<goal>"` lets an operator state a high-level Goal ("Publish
the next stable release.", "Implement Self Update.", "셀프 업데이트 기능을
구현해", "다음 안정 릴리스를 발행해") instead of manually deciding which
Task(s) to write. This module never invents scope: it only ever cites (a) a
small hand-curated catalog of capability domains whose `evidence`/planned-task
deliverables are grounded in real Task numbers and doc text that existed when
the catalog was written, (b) real `docs/00-roadmap.md` entries, or (c) real
open GitHub Issues/Pull Requests. A Goal that matches none of these is
reported `ambiguous`, never guessed at.

**Language contract:** English and Korean Goal text are both first-class
inputs (PR #82 review). Text is Unicode-normalized (NFC) before matching;
tokenization recognizes Hangul syllables as well as ASCII words; the
actionable-verb gate and every catalog domain's `keywords` include Korean
phrases alongside their English equivalents. There is no free-form
translation or generation anywhere in this module - Korean support is the
same fixed-catalog/substring-matching mechanism as English, just with more
phrases in it. A Goal in a language with no matching catalog/roadmap
evidence at all (English or Korean or otherwise) is still `ambiguous` -
recognizing more phrasings does not weaken the fail-closed default.

Two layers, mirroring `devbot.release_ops`: `plan_goal()` is pure (given
already-fetched roadmap text and open-work titles, no network calls, fully
unit-testable); `fetch_goal_plan()` is the thin GitHub/filesystem-reading
wrapper `devbot goal plan` actually calls. Neither ever writes anything -
this Task is read-only planning only; it does not create Issues, branches,
contracts, or PRs, and does not execute the plan it produces.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from devbot.github_client import GitHubClient, GitHubIssue, PullRequest
from devbot.models import RepositoryConfig

GoalDecision = Literal[
    "already_completed",
    "duplicate_open_work",
    "single_task",
    "multi_task",
    "ambiguous",
]

ROADMAP_PATH = Path("docs/00-roadmap.md")

# Grammatical stopwords - domain nouns and verbs (e.g. "release", "update",
# "reduce") stay significant tokens, since they carry the actual matching
# signal.
_GRAMMATICAL_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "should",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)

# Repo-specific low-signal words: this repo's own name and the fixed
# boilerplate vocabulary every Task Issue/PR body already contains
# (`devbot.planner.render_task_issue_body`/`render_pr_body`). Left in,
# these dominate the overlap score of almost *any* Goal against almost
# *any* open Issue/PR regardless of actual topic - discovered live
# ("Build a self-update mechanism for devbot." scored 0.6 overlap against
# an unrelated Issue purely via "devbot"/generic Task-template words).
_BOILERPLATE_STOPWORDS = frozenset(
    {
        "devbot",
        "task",
        "issue",
        "branch",
        "contract",
        "pull",
        "request",
        "result",
    }
)

_STOPWORDS = _GRAMMATICAL_STOPWORDS | _BOILERPLATE_STOPWORDS

# A Goal must contain at least one of these to be treated as an actionable
# request at all - otherwise it is ambiguous regardless of any keyword
# overlap (CP-038 ambiguity gate). English verbs are matched as whole
# tokens (`goal_tokens & _ACTIONABLE_VERBS_EN`); Korean verbs are matched as
# a substring of the normalized Goal text (`_ACTIONABLE_VERB_STEMS_KO`),
# since Korean is agglutinative - a verb stem like "구현" ("implement")
# always appears with a conjugated ending attached ("구현해", "구현하다",
# "구현해줘"), never as a bare token.
_ACTIONABLE_VERBS_EN = frozenset(
    {
        "add",
        "automate",
        "build",
        "create",
        "detect",
        "enable",
        "fix",
        "generate",
        "implement",
        "improve",
        "increase",
        "publish",
        "reduce",
        "remove",
        "support",
        "update",
    }
)

_ACTIONABLE_VERB_STEMS_KO: tuple[str, ...] = (
    "구현",  # implement
    "추가",  # add
    "생성",  # create/generate
    "개선",  # improve
    "수정",  # fix
    "제거",  # remove
    "지원",  # support
    "발행",  # publish (release)
    "게시",  # publish (post)
    "감소",  # reduce
    "줄이",  # reduce (줄이다/줄여)
    "자동화",  # automate
    "감지",  # detect
    "탐지",  # detect
    "활성화",  # enable
    "만들",  # build/make (만들다/만들어)
)

_MIN_SIGNIFICANT_TOKENS = 2
_OPEN_WORK_OVERLAP_THRESHOLD = 0.6
_ROADMAP_OVERLAP_THRESHOLD = 0.5

# `가-힣` is the precomposed Hangul syllable block, so a Korean
# Goal tokenizes into its space-separated words (each still carrying any
# attached grammatical particle/ending - see `_ACTIONABLE_VERB_STEMS_KO`'s
# docstring above) instead of contributing zero significant tokens.
_TOKEN_RE = re.compile(r"[a-z0-9]+|[가-힣]+")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(_normalize(text).lower())) - _STOPWORDS


def _has_actionable_verb(goal_tokens: frozenset[str], normalized_goal: str) -> bool:
    if goal_tokens & _ACTIONABLE_VERBS_EN:
        return True
    return any(stem in normalized_goal for stem in _ACTIONABLE_VERB_STEMS_KO)


def _overlap_score(goal_tokens: frozenset[str], candidate_text: str) -> float:
    """Fraction of `goal_tokens` also present in `candidate_text` - a
    deterministic, dependency-free containment score (no embeddings/ML),
    so results are exactly reproducible in tests."""
    if not goal_tokens:
        return 0.0
    candidate_tokens = _tokenize(candidate_text)
    if not candidate_tokens:
        return 0.0
    return len(goal_tokens & candidate_tokens) / len(goal_tokens)


@dataclass(frozen=True)
class PlannedTask:
    title: str
    objective: str
    dependencies: tuple[str, ...]
    expected_deliverables: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    order: int


@dataclass(frozen=True)
class GoalPlan:
    goal: str
    decision: GoalDecision
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    planned_tasks: tuple[PlannedTask, ...] = ()


def dependency_order_is_valid(tasks: Sequence[PlannedTask]) -> bool:
    """True iff every `PlannedTask.dependencies` entry names another task in
    `tasks` whose `order` is strictly smaller - i.e. `tasks` sorted by
    `order` is already a valid execution sequence with no forward
    reference. Used both as a catalog self-check and by callers that want
    to confirm a `GoalPlan.planned_tasks` sequence is safe to execute in
    `order`."""
    order_by_title = {task.title: task.order for task in tasks}
    return all(
        dependency in order_by_title and order_by_title[dependency] < task.order
        for task in tasks
        for dependency in task.dependencies
    )


@dataclass(frozen=True)
class _TaskTemplate:
    title: str
    objective: str
    expected_deliverables: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    depends_on: tuple[str, ...]
    order: int


@dataclass(frozen=True)
class CapabilityDomain:
    """One hand-curated, evidence-grounded entry in the planner's fixed
    catalog. `keywords` are phrases matched by substring containment against
    the normalized Goal text - deliberately specific multi-word phrases so
    an unrelated Goal sharing one common word does not false-match."""

    domain_id: str
    keywords: tuple[str, ...]
    implemented: bool
    evidence: str
    tasks: tuple[_TaskTemplate, ...] = ()


# ---------------------------------------------------------------------------
# The fixed capability catalog. Every `evidence` string and every planned
# Task's deliverables/acceptance criteria cite a real Task number or a real
# doc line that existed when this catalog was written - the planner does not
# generate this text from the Goal itself.
# ---------------------------------------------------------------------------

CAPABILITY_CATALOG: tuple[CapabilityDomain, ...] = (
    CapabilityDomain(
        domain_id="release_publish",
        keywords=(
            "publish the next stable release",
            "publish a stable release",
            "publish next release",
            "next stable release",
            "publish release",
            "release publish",
            "cut a release",
            "cut the release",
            "다음 안정 릴리스",
            "다음 stable release",
            "릴리스를 발행",
            "릴리스 발행",
            "릴리스를 게시",
            "릴리스 게시",
            "스테이블 릴리스 발행",
        ),
        implemented=True,
        evidence=(
            "Task 037 (`devbot release preview|publish|status`, "
            "tasks/037-release-operator-ux.md) already automates next-version, "
            "commit, and Release Notes determination and dispatches the existing "
            "Release workflow."
        ),
    ),
    CapabilityDomain(
        domain_id="release_notes_generation",
        keywords=(
            "generate release notes",
            "release notes generation",
            "bilingual release notes",
            "automatic release notes",
            "릴리스 노트 생성",
            "릴리스 노트를 생성",
            "릴리스 노트 자동 생성",
        ),
        implemented=True,
        evidence=(
            "Task 037 (`devbot.release.aggregate_release_notes`) already generates "
            "deterministic bilingual (Korean + English) Release Notes from merged "
            "Pull Request metadata."
        ),
    ),
    CapabilityDomain(
        domain_id="release_operator_ux",
        keywords=(
            "improve release ux",
            "release operator ux",
            "release ux",
            "improve release experience",
            "릴리스 ux를 개선",
            "릴리스 ux 개선",
            "릴리스 경험을 개선",
            "릴리스 경험 개선",
        ),
        implemented=True,
        evidence=(
            "Task 037 already delivered the release preview/publish/status operator "
            "CLI (tasks/037-release-operator-ux.md)."
        ),
    ),
    CapabilityDomain(
        domain_id="github_api_reliability",
        keywords=(
            "reduce github api failures",
            "github api failures",
            "github api reliability",
            "github api retry",
            "transient github failures",
            "flaky github api",
            "github api 실패를 감소",
            "github api 실패 감소",
            "github api 안정성",
            "github api 재시도",
        ),
        implemented=True,
        evidence=(
            "Task 030 (`src/devbot/github_retry.py`, "
            "results/030-github-api-transient-retry.md) already added classified "
            "retry handling for transient GitHub API failures."
        ),
    ),
    CapabilityDomain(
        domain_id="automatic_merge",
        keywords=(
            "automatic merge",
            "auto merge devbot",
            "automerge",
            "자동 머지",
            "자동 병합",
        ),
        implemented=True,
        evidence=(
            "B2 (자동 머지 안전 게이트, `src/devbot/automerge.py`) already implements "
            "the automatic merge safety gate."
        ),
    ),
    CapabilityDomain(
        domain_id="global_path_launcher",
        keywords=(
            "global path launcher",
            "install devbot on path",
            "global launcher",
            "path launcher",
            "글로벌 path 런처",
            "path에 devbot 설치",
            "전역 실행 파일",
            "전역 런처",
        ),
        implemented=False,
        evidence=(
            "Listed as Out of Scope in tasks/032-automated-release-pipeline.md "
            "(\"Global PATH launcher\") and not implemented since."
        ),
        tasks=(
            _TaskTemplate(
                title="Global PATH Launcher",
                objective=(
                    "Let an operator install the packaged DevBot artifact "
                    "(Task 034's self-contained release artifact) so a `devbot` "
                    "command is available on PATH without manually managing the "
                    "extracted archive location."
                ),
                expected_deliverables=(
                    "An install command or documented install step that places/"
                    "links the artifact's launcher on PATH",
                    "Operator documentation for install and uninstall",
                    "Tests covering the install/link behavior",
                ),
                acceptance_criteria=(
                    "`devbot --version` succeeds from a fresh shell after install "
                    "without the operator manually setting PATH",
                    "The existing release artifact contract (Task 032/034) is "
                    "unchanged",
                    "Uninstall/rollback is documented",
                ),
                depends_on=(),
                order=1,
            ),
        ),
    ),
    CapabilityDomain(
        domain_id="self_update_runtime",
        keywords=(
            "implement self update",
            "self update",
            "self-update",
            "auto update devbot",
            "automatic update",
            "runtime update",
            "update client",
            "셀프 업데이트",
            "셀프업데이트",
            "자체 업데이트",
            "자동 업데이트",
            "자동 갱신",
        ),
        implemented=False,
        evidence=(
            "docs/history.md Known Limitations: \"Runtime automatic update "
            "discovery ... remain out of scope\" - still true as of the current "
            "roadmap."
        ),
        tasks=(
            _TaskTemplate(
                title="Self-Update Discovery",
                objective=(
                    "Detect when a newer stable DevBot Release exists than the one "
                    "currently running, reusing Task 037's read-only latest-stable-"
                    "version lookup (`devbot release status`)."
                ),
                expected_deliverables=(
                    "A read-only check reporting current vs. latest stable version",
                    "Tests for up-to-date / update-available / GitHub-unreachable "
                    "cases",
                ),
                acceptance_criteria=(
                    "Never writes to GitHub or modifies the local installation",
                    "Reuses `devbot.release.SemanticVersion` for comparison rather "
                    "than a new version-comparison scheme",
                ),
                depends_on=(),
                order=1,
            ),
            _TaskTemplate(
                title="Self-Update Fetch and Verify",
                objective=(
                    "Download the newer Release's platform-specific artifact and "
                    "verify it against its published SHA256SUMS, reusing Task 037's "
                    "checksum-manifest validation approach."
                ),
                expected_deliverables=(
                    "Artifact download to a temporary/staging location",
                    "SHA-256 verification before the artifact is trusted",
                    "Tests for checksum-mismatch and download-failure cases",
                ),
                acceptance_criteria=(
                    "Refuses to proceed on any checksum mismatch (same fail-closed "
                    "policy as Task 037's `validate_published_release`)",
                    "Does not modify the currently-running installation",
                ),
                depends_on=("Self-Update Discovery",),
                order=2,
            ),
            _TaskTemplate(
                title="Self-Update Apply",
                objective=(
                    "Safely replace the running DevBot installation with the "
                    "verified newer artifact and restart, closing the \"Runtime "
                    "automatic update discovery\" known limitation."
                ),
                expected_deliverables=(
                    "An atomic install-swap mechanism (e.g. staged directory + "
                    "symlink swap)",
                    "A safe-restart path consistent with the existing `devbot "
                    "doctor`/`ProcessLock` daemon lifecycle",
                    "Rollback to the previous version on a failed swap",
                ),
                acceptance_criteria=(
                    "A failed apply leaves the previous, working installation "
                    "running",
                    "Never applies an artifact Self-Update Fetch and Verify has not "
                    "verified",
                    "A documented operator override disables automatic apply",
                ),
                depends_on=("Self-Update Fetch and Verify",),
                order=3,
            ),
        ),
    ),
)


def _match_catalog_domain(goal_text: str) -> CapabilityDomain | None:
    normalized = _normalize(goal_text).lower()
    for domain in CAPABILITY_CATALOG:
        if any(keyword in normalized for keyword in domain.keywords):
            return domain
    return None


def _templates_to_planned_tasks(templates: Iterable[_TaskTemplate]) -> tuple[PlannedTask, ...]:
    return tuple(
        PlannedTask(
            title=template.title,
            objective=template.objective,
            dependencies=template.depends_on,
            expected_deliverables=template.expected_deliverables,
            acceptance_criteria=template.acceptance_criteria,
            order=template.order,
        )
        for template in templates
    )


_ROADMAP_ENTRY_RE = re.compile(
    r"^- \[(?P<mark>[ x])\] Task (?P<number>\d+): (?P<title>.+)$"
)


@dataclass(frozen=True)
class RoadmapEntry:
    number: int
    title: str
    completed: bool
    text: str


def parse_roadmap(text: str) -> tuple[RoadmapEntry, ...]:
    """Parse `docs/00-roadmap.md`'s `- [x]/[ ] Task NNN: <title>` entries,
    including their indented continuation lines, into structured entries.
    Pure text parsing - no network, no filesystem access here."""
    entries: list[RoadmapEntry] = []
    current_lines: list[str] | None = None
    current_number = 0
    current_title = ""
    current_completed = False

    def _flush() -> None:
        if current_lines is not None:
            entries.append(
                RoadmapEntry(
                    number=current_number,
                    title=current_title,
                    completed=current_completed,
                    text=" ".join(current_lines),
                )
            )

    for line in text.splitlines():
        match = _ROADMAP_ENTRY_RE.match(line)
        if match:
            _flush()
            current_number = int(match.group("number"))
            # Roadmap entries are "<short title>. <longer description>..." on
            # one flowing line; keep only the short title, not the whole
            # first line, for a readable evidence citation.
            current_title = match.group("title").split(". ", 1)[0]
            current_completed = match.group("mark") == "x"
            current_lines = [line]
        elif current_lines is not None and line.startswith("      "):
            current_lines.append(line.strip())
        elif current_lines is not None and not line.strip():
            continue
        else:
            _flush()
            current_lines = None
    _flush()
    return tuple(entries)


def _best_roadmap_match(
    goal_tokens: frozenset[str], entries: Sequence[RoadmapEntry]
) -> tuple[RoadmapEntry, float] | None:
    best: tuple[RoadmapEntry, float] | None = None
    for entry in entries:
        if not entry.completed:
            continue
        score = _overlap_score(goal_tokens, entry.text)
        if best is None or score > best[1]:
            best = (entry, score)
    return best


def _best_open_work_match(
    goal_tokens: frozenset[str], titles_and_refs: Sequence[tuple[str, str]]
) -> tuple[str, float] | None:
    """`titles_and_refs` is `(searchable_text, human_reference)` pairs, e.g.
    `("Improve release UX ...", "Issue #12: Improve release UX")`."""
    best: tuple[str, float] | None = None
    for text, reference in titles_and_refs:
        score = _overlap_score(goal_tokens, text)
        if best is None or score > best[1]:
            best = (reference, score)
    return best


def plan_goal(
    goal: str,
    *,
    roadmap_entries: Sequence[RoadmapEntry] = (),
    open_work: Sequence[tuple[str, str]] = (),
) -> GoalPlan:
    """Pure decision core. `open_work` is `(searchable_text, reference)`
    pairs for every open Issue/PR (title + body). No network calls."""
    normalized_goal = _normalize(goal).lower()
    goal_tokens = _tokenize(goal)

    if len(goal_tokens) < _MIN_SIGNIFICANT_TOKENS or not _has_actionable_verb(
        goal_tokens, normalized_goal
    ):
        return GoalPlan(
            goal=goal,
            decision="ambiguous",
            reasons=(
                "the Goal has too few significant, actionable words to plan "
                "safely (need at least one recognizable action and at least "
                f"{_MIN_SIGNIFICANT_TOKENS} significant words)",
            ),
            evidence=(),
        )

    open_match = _best_open_work_match(goal_tokens, open_work)
    if open_match is not None and open_match[1] >= _OPEN_WORK_OVERLAP_THRESHOLD:
        reference, score = open_match
        return GoalPlan(
            goal=goal,
            decision="duplicate_open_work",
            reasons=(f"an open Issue/Pull Request already covers this Goal: {reference}",),
            evidence=(reference,),
        )

    domain = _match_catalog_domain(goal)
    if domain is not None:
        if domain.implemented:
            return GoalPlan(
                goal=goal,
                decision="already_completed",
                reasons=(f"capability domain '{domain.domain_id}' is already implemented",),
                evidence=(domain.evidence,),
            )
        planned = _templates_to_planned_tasks(domain.tasks)
        decision: GoalDecision = "single_task" if len(planned) == 1 else "multi_task"
        return GoalPlan(
            goal=goal,
            decision=decision,
            reasons=(
                f"capability domain '{domain.domain_id}' is not yet implemented; "
                f"{len(planned)} Task(s) are required to close it",
            ),
            evidence=(domain.evidence,),
            planned_tasks=planned,
        )

    roadmap_match = _best_roadmap_match(goal_tokens, roadmap_entries)
    if roadmap_match is not None and roadmap_match[1] >= _ROADMAP_OVERLAP_THRESHOLD:
        entry, score = roadmap_match
        return GoalPlan(
            goal=goal,
            decision="already_completed",
            reasons=(
                f"Task {entry.number:03d} ('{entry.title}') in docs/00-roadmap.md "
                "already appears to cover this Goal",
            ),
            evidence=(f"docs/00-roadmap.md Task {entry.number:03d}: {entry.title}",),
        )

    return GoalPlan(
        goal=goal,
        decision="ambiguous",
        reasons=(
            "no known capability domain or roadmap evidence matches this Goal; "
            "the planner cannot safely decompose it into Tasks without inventing "
            "scope not backed by source code or documentation",
        ),
        evidence=(),
    )


def fetch_goal_plan(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    goal: str,
    *,
    roadmap_path: Path = ROADMAP_PATH,
) -> GoalPlan:
    """Read `roadmap_path` from the local checkout and every open GitHub
    Issue/Pull Request, then delegate to the pure `plan_goal()`. Read-only:
    never writes to GitHub, never creates an Issue/branch/contract/PR."""
    roadmap_entries: tuple[RoadmapEntry, ...] = ()
    if roadmap_path.is_file():
        roadmap_entries = parse_roadmap(roadmap_path.read_text(encoding="utf-8"))

    open_issues: list[GitHubIssue] = github_client.list_issues(repository, state="open")
    open_prs: list[PullRequest] = github_client.list_pull_requests(repository, state="open")
    open_work = [
        (f"{issue.title} {issue.body}", f"Issue #{issue.number}: {issue.title}")
        for issue in open_issues
    ] + [(pr.body, f"Pull Request #{pr.number}") for pr in open_prs]

    return plan_goal(goal, roadmap_entries=roadmap_entries, open_work=open_work)
