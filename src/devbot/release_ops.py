"""Task 037: Operator-facing release orchestration.

`devbot release preview|publish|status` so an operator's entire interaction
with releasing DevBot can be "publish the next stable release" - never a
manually chosen version number, commit SHA, or hand-written Release body.

This module is the only place that talks to GitHub for that purpose. It
combines real GitHub reads (`devbot.github_client.GitHubClient`) and writes
(`devbot.github_write_client.GitHubWriteClient`) with the pure policy
functions in `devbot.release`, which stays free of network calls. Every
network-calling function here takes an already constructed client so tests
can inject a fake; the readiness/version/notes computation itself
(`build_release_preview`) is pure and takes a `ReleaseContext` value, not a
client, so it needs no mocking to test.

Publishing never creates a tag or a GitHub Release directly - it only
dispatches the existing `.github/workflows/release.yml` `workflow_dispatch`
event and waits. A failed or timed-out run is reported, never worked
around.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from devbot.github_client import GitHubClient, GitHubClientError, GitHubRelease, WorkflowRun
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig
from devbot.release import (
    ChangeEntry,
    PullRequestMetadata,
    ReleaseIncrement,
    ReleasePolicyError,
    ReleaseRecord,
    SemanticVersion,
    aggregate_release_notes,
    authoritative_version,
    expected_artifact_names,
    latest_stable_version,
    next_version,
    release_for_target_commit,
    release_increment_for_pr,
)
from devbot.startup import resolve_operator_checkout

RELEASE_WORKFLOW_FILE = "release.yml"
CI_WORKFLOW_FILE = "ci.yml"
CHECKSUM_MANIFEST_NAME = "SHA256SUMS"

_INCREMENT_RANK: dict[ReleaseIncrement, int] = {"none": 0, "patch": 1, "minor": 2, "major": 3}
_SHA256_LINE_RE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>.+)$")


class ReleaseOpsError(RuntimeError):
    """Raised when a `devbot release` command cannot safely proceed."""


@dataclass(frozen=True)
class ReleaseReadiness:
    ready: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleasePreview:
    previous_version: str
    next_version: str
    increment: ReleaseIncrement | None
    target_commit: str
    target_commit_validated: bool
    previous_release_commit: str | None
    readiness: ReleaseReadiness
    expected_assets: tuple[str, ...]
    changes: tuple[ChangeEntry, ...]
    notes: str


@dataclass(frozen=True)
class ReleaseContext:
    """Everything `build_release_preview` needs, already fetched from
    GitHub - kept separate from the fetch itself so preview computation is
    pure and unit-testable without a network double."""

    target_commit: str
    target_commit_validated: bool
    release_records: tuple[ReleaseRecord, ...]
    main_commits: frozenset[str]
    initial_version: str
    commit_range_prs: tuple[PullRequestMetadata | None, ...]
    target_commit_validation_error: str | None = None


@dataclass(frozen=True)
class ReleaseStatus:
    latest_stable_version: str | None
    latest_release_url: str | None
    latest_release_published_at: datetime | None
    last_published_commit: str | None
    latest_workflow_run: WorkflowRun | None
    publication_state: str


@dataclass(frozen=True)
class PublishOutcome:
    preview: ReleasePreview
    workflow_run: WorkflowRun
    release_url: str
    tag: str
    validated_assets: tuple[str, ...]


def local_checkout_is_dirty(path: Path) -> bool | None:
    """`git status --porcelain` in `path`. Returns `None` (skip, not a
    blocker) when `path` is not a usable Git checkout - release publication
    itself never reads from local disk, so an unresolvable checkout is not
    grounds to refuse; a genuinely dirty one is (safety rule: "main is
    dirty")."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())


def _previous_release_commit(
    release_records: Iterable[ReleaseRecord],
    base_version: SemanticVersion,
    main_commits: Iterable[str],
) -> str | None:
    main_commits_set = set(main_commits)
    for record in release_records:
        if (
            not record.draft
            and not record.prerelease
            and record.tag_name == base_version.tag
            and record.target_commitish in main_commits_set
        ):
            return record.target_commitish
    return None


def target_commit_is_ci_validated(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    target_commit: str,
    *,
    workflow_file: str = CI_WORKFLOW_FILE,
) -> bool:
    """A main commit counts as CI-validated only when `workflow_file` (the
    configured CI workflow) has a run whose head SHA exactly matches
    `target_commit`, triggered by a `push` event, with `status ==
    "completed"` and `conclusion == "success"` (Task 039).

    Deliberately does *not* use `GitHubClient.list_check_runs_for_ref` -
    `GET .../commits/{sha}/check-runs` returns check runs from *every*
    workflow that ran for the commit, not just `workflow_file`. Since
    `.github/workflows/release.yml` also triggers on every push to `main`,
    its own jobs' check runs (including ones that are legitimately
    `skipped`, or that can fail for reasons unrelated to CI health - see
    Task 039's Result doc) were being folded into an "every check run must
    be success" requirement, so a green CI run could still be reported
    unvalidated. Querying the CI workflow's own runs, filtered to the exact
    commit and a `push` event, cannot be confused by another workflow's
    check runs or by a pull-request run against a different (pre-merge)
    head SHA.
    """
    runs = github_client.list_workflow_runs(
        repository, workflow_file, event="push", head_sha=target_commit
    )
    return any(
        run.head_sha == target_commit
        and run.event == "push"
        and run.status == "completed"
        and run.conclusion == "success"
        for run in runs
    )


def gather_release_context(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    *,
    ci_workflow_file: str = CI_WORKFLOW_FILE,
) -> ReleaseContext:
    """Fetch every piece of GitHub state `build_release_preview` needs.
    Read-only: lists releases/commits/workflow runs, never writes."""
    target_commit = github_client.get_commit_sha(repository, repository.default_branch)
    target_commit_validation_error: str | None = None
    try:
        target_commit_validated = target_commit_is_ci_validated(
            github_client, repository, target_commit, workflow_file=ci_workflow_file
        )
    except GitHubClientError as exc:
        # A permission/connectivity failure answering "is this commit
        # validated?" is not the same claim as "it failed validation" - and
        # must not silently abort the rest of the (still useful) preview.
        # Treated as not-validated with a distinguishable reason.
        target_commit_validated = False
        target_commit_validation_error = str(exc)

    raw_releases = github_client.list_releases(repository)
    release_records = tuple(
        ReleaseRecord(
            tag_name=release.tag_name,
            target_commitish=release.target_commitish,
            draft=release.draft,
            prerelease=release.prerelease,
        )
        for release in raw_releases
    )

    main_commit_shas = github_client.list_commits(repository, sha=target_commit)
    main_commits = frozenset({*main_commit_shas, target_commit})

    initial_version = authoritative_version()
    base_version = latest_stable_version(
        release_records, main_commits=set(main_commits), initial_version=initial_version
    )
    previous_release_commit = _previous_release_commit(release_records, base_version, main_commits)

    if previous_release_commit == target_commit:
        commit_range: tuple[str, ...] = ()
    elif previous_release_commit is not None:
        commit_range = tuple(
            github_client.compare_commits(repository, previous_release_commit, target_commit)
        )
    else:
        # No prior stable release reachable from main: everything reachable
        # from `target_commit` is in scope, oldest first.
        commit_range = tuple(reversed(main_commit_shas))

    commit_range_prs = tuple(
        github_client.get_commit_pull_request_metadata(repository, sha) for sha in commit_range
    )

    return ReleaseContext(
        target_commit=target_commit,
        target_commit_validated=target_commit_validated,
        release_records=release_records,
        main_commits=main_commits,
        initial_version=initial_version,
        commit_range_prs=commit_range_prs,
        target_commit_validation_error=target_commit_validation_error,
    )


def build_release_preview(
    context: ReleaseContext, *, local_dirty: bool | None = None
) -> ReleasePreview:
    """Pure computation of the next stable release plan from already-fetched
    `context`. No network calls, no GitHub mutation - this is what
    `devbot release preview` renders directly, and what `devbot release
    publish` must see `readiness.ready` from before it dispatches anything."""
    blockers: list[str] = []

    if local_dirty:
        blockers.append(
            "operator checkout has uncommitted changes (git status --porcelain is non-empty)"
        )

    if not context.target_commit_validated:
        if context.target_commit_validation_error is not None:
            blockers.append(
                f"could not verify Check Runs for {context.target_commit}: "
                f"{context.target_commit_validation_error}"
            )
        else:
            blockers.append(f"latest main commit {context.target_commit} is not CI-validated")

    existing = release_for_target_commit(context.release_records, context.target_commit)
    if existing is not None and not existing.draft:
        blockers.append(
            f"a stable release already exists for {context.target_commit}: {existing.tag_name}"
        )

    base_version = latest_stable_version(
        context.release_records,
        main_commits=set(context.main_commits),
        initial_version=context.initial_version,
    )
    previous_release_commit = _previous_release_commit(
        context.release_records, base_version, context.main_commits
    )

    changes: list[ChangeEntry] = []
    if previous_release_commit != context.target_commit:
        for pr in context.commit_range_prs:
            if pr is None:
                blockers.append(
                    "a commit since the last stable release has no associated merged Pull Request"
                )
                continue
            try:
                increment = release_increment_for_pr(pr)
            except ReleasePolicyError as exc:
                blockers.append(f"PR #{pr.number}: {exc}")
                continue
            changes.append(ChangeEntry(pr=pr, increment=increment if increment else "none"))

    increment_candidates = [entry.increment for entry in changes if entry.increment != "none"]
    aggregate_increment: ReleaseIncrement | None = None
    if increment_candidates:
        aggregate_increment = max(increment_candidates, key=lambda inc: _INCREMENT_RANK[inc])

    if not blockers and aggregate_increment is None:
        blockers.append(
            "no eligible merged Pull Requests (release:patch/minor/major) since the last "
            "stable release"
        )

    next_ver = (
        next_version(base_version, aggregate_increment) if aggregate_increment else base_version
    )

    notes = ""
    if not blockers:
        try:
            notes = aggregate_release_notes(
                changes,
                version=str(next_ver),
                previous_version=str(base_version),
                source_commit=context.target_commit,
            )
        except ReleasePolicyError as exc:
            blockers.append(str(exc))
        else:
            if not notes.strip():
                blockers.append("generated release notes are empty")

    return ReleasePreview(
        previous_version=str(base_version),
        next_version=str(next_ver),
        increment=aggregate_increment,
        target_commit=context.target_commit,
        target_commit_validated=context.target_commit_validated,
        previous_release_commit=previous_release_commit,
        readiness=ReleaseReadiness(ready=not blockers, blockers=tuple(blockers)),
        expected_assets=(
            (*expected_artifact_names(str(next_ver)), CHECKSUM_MANIFEST_NAME)
            if aggregate_increment
            else ()
        ),
        changes=tuple(changes),
        notes=notes,
    )


def fetch_release_preview(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    *,
    local_checkout_path: Path | None = None,
    ci_workflow_file: str = CI_WORKFLOW_FILE,
) -> ReleasePreview:
    """`gather_release_context` + `build_release_preview` in one call - the
    entry point `devbot release preview`/`publish` actually use."""
    context = gather_release_context(github_client, repository, ci_workflow_file=ci_workflow_file)
    path = local_checkout_path
    if path is None:
        try:
            path = resolve_operator_checkout()
        except Exception:  # noqa: BLE001 - best-effort; unresolved checkout is not a blocker
            path = None
    local_dirty = local_checkout_is_dirty(path) if path is not None else None
    return build_release_preview(context, local_dirty=local_dirty)


def dispatch_release(
    write_client: GitHubWriteClient,
    repository: RepositoryConfig,
    preview: ReleasePreview,
    *,
    workflow_file: str = RELEASE_WORKFLOW_FILE,
) -> None:
    """Trigger the existing Release workflow for `preview`'s computed
    increment/commit/notes. Refuses when `preview` is not ready - this is
    the single safety choke point every `devbot release publish` path goes
    through before anything reaches GitHub."""
    if not preview.readiness.ready:
        raise ReleaseOpsError(
            "refusing to publish: " + "; ".join(preview.readiness.blockers)
        )
    if preview.increment is None:
        raise ReleaseOpsError("refusing to publish: no eligible release increment")
    write_client.dispatch_workflow(
        repository,
        workflow_file,
        ref=repository.default_branch,
        inputs={
            "increment": preview.increment,
            "commit_sha": preview.target_commit,
            "notes": preview.notes,
        },
    )


def wait_for_dispatched_run(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    *,
    dispatched_after: datetime,
    workflow_file: str = RELEASE_WORKFLOW_FILE,
    poll_interval_seconds: float = 15.0,
    timeout_seconds: float = 1800.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> WorkflowRun:
    """Poll for the `workflow_dispatch` run created after `dispatched_after`,
    then poll that run until it completes. Times out rather than guessing;
    never falls back to creating a tag/Release itself."""
    deadline = now() + timedelta(seconds=timeout_seconds)
    run: WorkflowRun | None = None
    while run is None:
        candidates = [
            candidate
            for candidate in github_client.list_workflow_runs(
                repository, workflow_file, event="workflow_dispatch"
            )
            if candidate.created_at >= dispatched_after
        ]
        if candidates:
            run = max(candidates, key=lambda candidate: candidate.created_at)
            break
        if now() >= deadline:
            raise ReleaseOpsError(
                f"timed out waiting for the {workflow_file} workflow_dispatch run to appear"
            )
        sleep(poll_interval_seconds)

    while run.status != "completed":
        if now() >= deadline:
            raise ReleaseOpsError(
                f"timed out waiting for workflow run {run.html_url} to complete "
                f"(last status: {run.status})"
            )
        sleep(poll_interval_seconds)
        run = github_client.get_workflow_run(repository, run.id)

    if run.conclusion != "success":
        raise ReleaseOpsError(
            f"release workflow run {run.html_url} finished with conclusion={run.conclusion!r}"
        )
    return run


def _validate_checksum_manifest(text: str, *, expected_names: Iterable[str]) -> None:
    seen: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _SHA256_LINE_RE.match(line)
        if match is None:
            raise ReleaseOpsError(f"{CHECKSUM_MANIFEST_NAME} has a malformed line: {line!r}")
        seen[match.group("name")] = match.group("digest")
    expected = {name for name in expected_names if name != CHECKSUM_MANIFEST_NAME}
    if set(seen) != expected:
        raise ReleaseOpsError(
            f"{CHECKSUM_MANIFEST_NAME} does not cover exactly the expected artifacts "
            f"(found={sorted(seen)}, expected={sorted(expected)})"
        )


def validate_published_release(
    github_client: GitHubClient, repository: RepositoryConfig, preview: ReleasePreview
) -> GitHubRelease:
    """Confirm the tag, Release, assets, and checksum manifest match
    `preview` after the workflow reports success. Raises `ReleaseOpsError`
    on any mismatch (tag mismatch, missing assets, malformed/incomplete
    checksums) - these are exactly the Task 037 safety-gate failures."""
    tag = SemanticVersion.parse(preview.next_version).tag
    release = github_client.get_release_by_tag(repository, tag)
    if release is None:
        raise ReleaseOpsError(f"no GitHub Release found for tag {tag} after the workflow completed")
    if release.draft:
        raise ReleaseOpsError(f"release {tag} is still a draft")
    if release.target_commitish != preview.target_commit:
        raise ReleaseOpsError(
            f"release {tag} targets {release.target_commitish}, expected {preview.target_commit}"
        )

    actual_assets = {asset.name for asset in release.assets}
    expected_assets = set(preview.expected_assets)
    if actual_assets != expected_assets:
        raise ReleaseOpsError(
            f"release {tag} assets mismatch "
            f"(missing={sorted(expected_assets - actual_assets)}, "
            f"extra={sorted(actual_assets - expected_assets)})"
        )

    sums_asset = next(asset for asset in release.assets if asset.name == CHECKSUM_MANIFEST_NAME)
    sums_text = github_client.download_release_asset(repository, sums_asset.id).decode("utf-8")
    _validate_checksum_manifest(sums_text, expected_names=preview.expected_assets)
    return release


def publish_release(
    github_client: GitHubClient,
    write_client: GitHubWriteClient,
    repository: RepositoryConfig,
    *,
    preview: ReleasePreview | None = None,
    workflow_file: str = RELEASE_WORKFLOW_FILE,
    ci_workflow_file: str = CI_WORKFLOW_FILE,
    local_checkout_path: Path | None = None,
    poll_interval_seconds: float = 15.0,
    timeout_seconds: float = 1800.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PublishOutcome:
    """End to end `devbot release publish`: compute the plan (unless a
    caller already has a fresh one via `preview=`), dispatch the existing
    Release workflow, wait for it, then validate the result. Any
    safety-gate failure raises `ReleaseOpsError` before dispatch; any
    workflow/validation failure raises it after, without ever creating a
    tag or Release directly."""
    if preview is None:
        preview = fetch_release_preview(
            github_client,
            repository,
            local_checkout_path=local_checkout_path,
            ci_workflow_file=ci_workflow_file,
        )
    dispatched_at = now()
    dispatch_release(write_client, repository, preview, workflow_file=workflow_file)
    run = wait_for_dispatched_run(
        github_client,
        repository,
        dispatched_after=dispatched_at,
        workflow_file=workflow_file,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        sleep=sleep,
        now=now,
    )
    release = validate_published_release(github_client, repository, preview)
    return PublishOutcome(
        preview=preview,
        workflow_run=run,
        release_url=release.html_url,
        tag=release.tag_name,
        validated_assets=tuple(sorted(asset.name for asset in release.assets)),
    )


def _latest_stable_release(releases: Iterable[GitHubRelease]) -> GitHubRelease | None:
    candidates: list[tuple[SemanticVersion, GitHubRelease]] = []
    for release in releases:
        if release.draft or release.prerelease:
            continue
        try:
            version = SemanticVersion.parse_tag(release.tag_name)
        except ReleasePolicyError:
            continue
        candidates.append((version, release))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def build_release_status(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    *,
    workflow_file: str = RELEASE_WORKFLOW_FILE,
) -> ReleaseStatus:
    """`devbot release status`: read-only summary of the latest stable
    Release and the latest Release workflow run - never dispatches
    anything."""
    releases = github_client.list_releases(repository)
    latest = _latest_stable_release(releases)

    runs = github_client.list_workflow_runs(repository, workflow_file)
    latest_run = runs[0] if runs else None

    if latest_run is None:
        publication_state = "never-run"
    elif latest_run.status != "completed":
        publication_state = "in-progress"
    elif latest_run.conclusion == "success":
        publication_state = "up-to-date"
    else:
        publication_state = "failed"

    return ReleaseStatus(
        latest_stable_version=latest.tag_name.lstrip("v") if latest else None,
        latest_release_url=latest.html_url if latest else None,
        latest_release_published_at=latest.published_at if latest else None,
        last_published_commit=latest.target_commitish if latest else None,
        latest_workflow_run=latest_run,
        publication_state=publication_state,
    )
