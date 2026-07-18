"""Deterministic release pipeline policy helpers."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal

ReleaseIncrement = Literal["patch", "minor", "major", "none"]

PRODUCT_NAME = "devbot"
VERSION_SOURCE = "pyproject.toml"
RELEASE_LABELS: dict[str, ReleaseIncrement] = {
    "release:patch": "patch",
    "release:minor": "minor",
    "release:major": "major",
    "release:none": "none",
}
SUPPORTED_PLATFORMS: tuple[tuple[str, str], ...] = (("linux", "x86_64"), ("macos", "arm64"))
RELEASE_NOTE_SECTIONS: tuple[str, ...] = (
    "What's New",
    "Improvements",
    "Fixes",
    "Operational Changes",
    "Upgrade Notes",
    "Known Limitations",
)
# Task 037: Korean section headers, in the same order as `RELEASE_NOTE_SECTIONS`,
# so a bilingual release body can render both languages from one section walk.
KO_RELEASE_NOTE_SECTIONS: dict[str, str] = {
    "What's New": "새로운 기능",
    "Improvements": "개선 사항",
    "Fixes": "수정 사항",
    "Operational Changes": "운영 변경",
    "Upgrade Notes": "업그레이드 안내",
    "Known Limitations": "알려진 제한 사항",
}
# Which release note section a merged PR's change lands in, keyed by its
# `release:*` label. Deliberately coarse (3 of 6 sections) rather than
# inferring intent from PR titles - inferring "improvement" vs "new feature"
# from free text would risk describing a change that was not actually made.
_INCREMENT_SECTION: dict[ReleaseIncrement, str] = {
    "major": "What's New",
    "minor": "What's New",
    "patch": "Fixes",
    "none": "Operational Changes",
}
_INCREMENT_LABEL_EN: dict[ReleaseIncrement, str] = {
    "major": "major",
    "minor": "minor",
    "patch": "patch",
    "none": "internal",
}
_INCREMENT_LABEL_KO: dict[ReleaseIncrement, str] = {
    "major": "주요 변경",
    "minor": "기능 추가",
    "patch": "수정",
    "none": "내부 변경",
}
_SEMVER_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")


class ReleasePolicyError(ValueError):
    """Raised when a release operation would be ambiguous or unsafe."""


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = _SEMVER_RE.fullmatch(value)
        if match is None:
            raise ReleasePolicyError(f"invalid semantic version: {value!r}")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
        )

    @classmethod
    def parse_tag(cls, tag: str) -> SemanticVersion:
        if not tag.startswith("v"):
            raise ReleasePolicyError(f"release tag must start with 'v': {tag!r}")
        return cls.parse(tag[1:])

    def bump(self, increment: ReleaseIncrement) -> SemanticVersion:
        if increment == "major":
            return SemanticVersion(self.major + 1, 0, 0)
        if increment == "minor":
            return SemanticVersion(self.major, self.minor + 1, 0)
        if increment == "patch":
            return SemanticVersion(self.major, self.minor, self.patch + 1)
        if increment == "none":
            return self
        raise ReleasePolicyError(f"unsupported release increment: {increment!r}")

    @property
    def tag(self) -> str:
        return f"v{self}"

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class PullRequestMetadata:
    number: int
    title: str
    labels: tuple[str, ...]
    merged: bool
    base_ref: str
    merge_commit_sha: str


@dataclass(frozen=True)
class ChangeEntry:
    """One merged main PR folded into an aggregate (multi-PR) release, paired
    with the release increment its `release:*` label selects (Task 037)."""

    pr: PullRequestMetadata
    increment: ReleaseIncrement


@dataclass(frozen=True)
class ReleaseRecord:
    tag_name: str
    target_commitish: str
    draft: bool = False
    prerelease: bool = False


@dataclass(frozen=True)
class Artifact:
    name: str
    path: Path
    os_name: str
    architecture: str


@dataclass(frozen=True)
class ReleaseSummary:
    source_commit: str
    previous_version: str
    increment: ReleaseIncrement
    new_version: str
    tag: str
    artifact_names: tuple[str, ...]
    checksums: tuple[str, ...]
    release_url: str


@dataclass(frozen=True)
class ReleasePlan:
    publish: bool
    previous_version: str
    increment: ReleaseIncrement | None
    new_version: str
    tag: str
    notes: str
    reason: str


def authoritative_version(project_root: Path | str | None = None) -> str:
    if project_root is None:
        project_root = os.environ.get("DEVBOT_PROJECT_ROOT")
    if project_root is None:
        return metadata.version(PRODUCT_NAME)
    pyproject = Path(project_root) / VERSION_SOURCE
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def release_increment_for_pr(pr: PullRequestMetadata) -> ReleaseIncrement | None:
    if not pr.merged or pr.base_ref != "main":
        return None
    selected = [label for label in pr.labels if label in RELEASE_LABELS]
    if len(selected) != 1:
        raise ReleasePolicyError("merged main PR must have exactly one release:* label")
    increment = RELEASE_LABELS[selected[0]]
    return None if increment == "none" else increment


def latest_stable_version(
    releases: Iterable[ReleaseRecord],
    *,
    main_commits: set[str],
    initial_version: str,
) -> SemanticVersion:
    candidates: list[SemanticVersion] = []
    for release in releases:
        if release.draft or release.prerelease or release.target_commitish not in main_commits:
            continue
        try:
            candidates.append(SemanticVersion.parse_tag(release.tag_name))
        except ReleasePolicyError:
            continue
    return max(candidates) if candidates else SemanticVersion.parse(initial_version)


def has_stable_release(releases: Iterable[ReleaseRecord], *, main_commits: set[str]) -> bool:
    for release in releases:
        if release.draft or release.prerelease or release.target_commitish not in main_commits:
            continue
        try:
            SemanticVersion.parse_tag(release.tag_name)
        except ReleasePolicyError:
            continue
        return True
    return False


def next_version(base: str | SemanticVersion, increment: ReleaseIncrement) -> SemanticVersion:
    parsed = SemanticVersion.parse(base) if isinstance(base, str) else base
    return parsed.bump(increment)


def release_artifact_name(version: str, os_name: str, architecture: str) -> str:
    if (os_name, architecture) not in SUPPORTED_PLATFORMS:
        raise ReleasePolicyError(f"unsupported release platform: {os_name}/{architecture}")
    SemanticVersion.parse(version)
    return f"{PRODUCT_NAME}-{version}-{os_name}-{architecture}.tar.gz"


def expected_artifact_names(version: str) -> tuple[str, ...]:
    return tuple(
        release_artifact_name(version, os_name, arch) for os_name, arch in SUPPORTED_PLATFORMS
    )


def build_metadata(version: str, os_name: str, architecture: str) -> dict[str, str]:
    SemanticVersion.parse(version)
    return {
        "product": PRODUCT_NAME,
        "version": version,
        "os": os_name,
        "architecture": architecture,
    }


def build_artifact(
    output_dir: Path,
    *,
    version: str,
    os_name: str,
    architecture: str,
    project_root: Path | str = ".",
) -> Artifact:
    name = release_artifact_name(version, os_name, architecture)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    root = Path(project_root)
    metadata_bytes = json.dumps(
        build_metadata(version, os_name, architecture),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    launcher_text = (
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        "ROOT=$(CDPATH= cd -- \"$(dirname -- \"$0\")/..\" && pwd)\n"
        "PYTHON_BIN=\n"
        "for candidate in python3.13 python3 python; do\n"
        "  if command -v \"$candidate\" >/dev/null 2>&1 "
        "&& \"$candidate\" -c 'import sys; "
        "raise SystemExit(sys.version_info[:2] != (3, 13))'; then\n"
        "    PYTHON_BIN=$candidate\n"
        "    break\n"
        "  fi\n"
        "done\n"
        "if [ -z \"$PYTHON_BIN\" ]; then\n"
        "  echo 'devbot requires Python 3.13 on PATH' >&2\n"
        "  exit 127\n"
        "fi\n"
        "DEVBOT_PROJECT_ROOT=\"$ROOT\" "
        "PYTHONNOUSERSITE=1 "
        "PYTHONPATH=\"$ROOT/vendor:$ROOT/src\" "
        "exec \"$PYTHON_BIN\" -S -c 'from devbot.main import main; "
        "raise SystemExit(main())' \"$@\"\n"
    )
    launcher = launcher_text.encode()

    with (
        path.open("wb") as raw,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            compresslevel=9,
            mtime=0,
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        _add_bytes(archive, "devbot-release/metadata.json", metadata_bytes)
        _add_bytes(archive, "devbot-release/bin/devbot", launcher, mode=0o755)
        _add_project_file(
            archive, root / "pyproject.toml", "devbot-release/pyproject.toml", version=version
        )
        lockfile = root / "uv.lock"
        if lockfile.exists():
            _add_project_file(archive, lockfile, "devbot-release/uv.lock")
        for source in sorted((root / "src").rglob("*.py")):
            _add_project_file(archive, source, "devbot-release" / source.relative_to(root))
        _add_locked_runtime_dependencies(archive, root)
    return Artifact(name=name, path=path, os_name=os_name, architecture=architecture)


def _normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_runtime_dependencies(project_root: Path) -> dict[str, str]:
    lock_path = project_root / "uv.lock"
    if not lock_path.exists():
        raise ReleasePolicyError("release artifact requires uv.lock for locked dependencies")
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = {
        _normalize_package_name(str(package["name"])): package
        for package in lock.get("package", [])
        if isinstance(package, dict) and "name" in package
    }
    root_package = packages.get(PRODUCT_NAME)
    if root_package is None:
        raise ReleasePolicyError("uv.lock does not contain the devbot package")

    pending = [
        _normalize_package_name(str(dependency["name"]))
        for dependency in root_package.get("dependencies", [])
        if isinstance(dependency, dict) and "name" in dependency
    ]
    locked: dict[str, str] = {}
    while pending:
        name = pending.pop(0)
        if name in locked:
            continue
        package = packages.get(name)
        if package is None:
            raise ReleasePolicyError(f"uv.lock missing runtime dependency: {name}")
        locked[name] = str(package["version"])
        for dependency in package.get("dependencies", []):
            if isinstance(dependency, dict) and "name" in dependency:
                pending.append(_normalize_package_name(str(dependency["name"])))
    return dict(sorted(locked.items()))


def _add_locked_runtime_dependencies(archive: tarfile.TarFile, project_root: Path) -> None:
    for package_name, locked_version in _locked_runtime_dependencies(project_root).items():
        try:
            distribution = metadata.distribution(package_name)
        except metadata.PackageNotFoundError as exc:
            raise ReleasePolicyError(
                f"locked runtime dependency is not installed: {package_name}=={locked_version}"
            ) from exc
        if distribution.version != locked_version:
            raise ReleasePolicyError(
                f"installed runtime dependency {package_name}=={distribution.version} "
                f"does not match uv.lock {locked_version}"
            )
        if distribution.files is None:
            raise ReleasePolicyError(f"runtime dependency has no file manifest: {package_name}")
        for relative in sorted(distribution.files, key=lambda path: str(path)):
            relative_text = str(relative)
            if (
                ".." in relative.parts
                or "__pycache__" in relative.parts
                or relative_text.endswith((".pyc", ".pyo"))
            ):
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            _add_project_file(archive, source, Path("devbot-release/vendor") / relative_text)


def _add_bytes(
    archive: tarfile.TarFile, name: str | Path, data: bytes, *, mode: int = 0o644
) -> None:
    info = tarfile.TarInfo(str(name))
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    archive.addfile(info, fileobj=io.BytesIO(data))


def _add_project_file(
    archive: tarfile.TarFile,
    source: Path,
    archive_name: str | Path,
    *,
    version: str | None = None,
) -> None:
    data = source.read_bytes()
    if version is not None:
        text = data.decode()
        text = re.sub(r'(?m)^version = "[^"]+"$', f'version = "{version}"', text, count=1)
        data = text.encode()
    mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
    _add_bytes(archive, archive_name, data, mode=mode)


def checksum_manifest(artifacts: Iterable[Artifact], *, expected_names: Iterable[str]) -> str:
    by_name = {artifact.name: artifact for artifact in artifacts}
    expected = tuple(sorted(expected_names))
    if tuple(sorted(by_name)) != expected:
        raise ReleasePolicyError("checksum manifest requires exactly the expected artifacts")
    lines = []
    for name in expected:
        digest = hashlib.sha256(by_name[name].path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    return "\n".join(lines) + "\n"


def release_notes(pr: PullRequestMetadata, increment: ReleaseIncrement, version: str) -> str:
    safe_title = pr.title.replace("\r", " ").replace("\n", " ").strip()
    sections: dict[str, tuple[str, ...]] = {
        "What's New": (f"{increment}: #{pr.number} {safe_title}",),
        "Improvements": ("No additional improvements recorded for this release.",),
        "Fixes": ("No fixes recorded for this release.",),
        "Operational Changes": ("No operational changes recorded for this release.",),
        "Upgrade Notes": ("No upgrade notes recorded for this release.",),
        "Known Limitations": ("No new known limitations recorded for this release.",),
    }
    lines = [f"## {PRODUCT_NAME} {version}", ""]
    for section in RELEASE_NOTE_SECTIONS:
        lines.extend([f"### {section}", ""])
        lines.extend(f"- {item}" for item in sections[section])
        lines.append("")
    return "\n".join(lines)


def initial_release_notes(*, version: str, source_commit: str) -> str:
    SemanticVersion.parse(version)
    sections: dict[str, tuple[str, ...]] = {
        "What's New": (
            "GitHub Issue driven task intake, branch preparation, implementation, review, "
            "rework, and ready-to-merge orchestration.",
            "Role-aware Agent execution for IMPLEMENT, REWORK, and REVIEW with a shared "
            "PreparedWorkspace contract.",
            "Portable Python release artifact with embedded package metadata and a "
            "deterministic SHA-256 checksum manifest.",
        ),
        "Improvements": (
            "Timeline markers, queue summaries, structured failure categories, retry policy, "
            "and startup diagnostics make daemon operation auditable.",
            "Planner workflow artifacts now use a single Task Issue, branch, contract, Result, "
            "and Pull Request model.",
        ),
        "Fixes": (
            "Hardened state transitions, duplicate feedback handling, no-op rework handling, "
            "and transient GitHub API retry behavior.",
            "Prepared workspace validation prevents operator checkout state from leaking into "
            "Task execution.",
        ),
        "Operational Changes": (
            "Stable Releases are produced only from validated main commits after the Task 032 "
            "validation gate succeeds.",
            "Release publication is draft-first, checksum verified, idempotent, and refuses to "
            "move existing tags.",
        ),
        "Upgrade Notes": (
            "This is the first stable DevBot Release. Install or update from the published "
            "platform-specific artifact and verify it against SHA256SUMS.",
        ),
        "Known Limitations": (
            "Runtime automatic update discovery, package-manager distribution, launchd/systemd "
            "installers, and automatic merge remain out of scope.",
        ),
    }
    lines = [
        f"## {PRODUCT_NAME} {version}",
        "",
        f"Source commit: `{source_commit}`",
        "",
    ]
    for section in RELEASE_NOTE_SECTIONS:
        lines.extend([f"### {section}", ""])
        lines.extend(f"- {item}" for item in sections[section])
        lines.append("")
    return "\n".join(lines)


def aggregate_release_notes(
    entries: Iterable[ChangeEntry],
    *,
    version: str,
    previous_version: str,
    source_commit: str,
) -> str:
    """Deterministic bilingual (Korean + English) Release body for an
    operator-triggered "publish the next stable release" (Task 037).

    Source of truth is exactly `entries` - each merged main PR's real number,
    title, and `release:*` label. No section is populated from anything else,
    so the two language halves necessarily describe the identical set of
    changes and nothing is invented.
    """
    entries_tuple = tuple(entries)
    if not entries_tuple:
        raise ReleasePolicyError("cannot generate release notes from zero merged PRs")

    by_section: dict[str, list[ChangeEntry]] = {section: [] for section in RELEASE_NOTE_SECTIONS}
    for entry in entries_tuple:
        by_section[_INCREMENT_SECTION[entry.increment]].append(entry)

    def render(*, korean: bool) -> list[str]:
        lines: list[str] = []
        for section in RELEASE_NOTE_SECTIONS:
            lines.extend([f"### {KO_RELEASE_NOTE_SECTIONS[section] if korean else section}", ""])
            if section == "Upgrade Notes":
                lines.append(
                    f"- `{previous_version}` 에서 `{version}` 으로 "
                    "업그레이드하고, 다운로드한 "
                    "아티팩트를 `SHA256SUMS` 로 검증하세요."
                    if korean
                    else f"- Upgrade from `{previous_version}` to `{version}`. Verify "
                    "downloaded artifacts against `SHA256SUMS`."
                )
            elif section == "Known Limitations":
                lines.append(
                    "- 이번 릴리스에 새로 기록된 알려진 "
                    "제한 사항이 없습니다."
                    if korean
                    else "- No new known limitations recorded for this release."
                )
            elif by_section[section]:
                for entry in by_section[section]:
                    safe_title = entry.pr.title.replace("\r", " ").replace("\n", " ").strip()
                    label = (
                        _INCREMENT_LABEL_KO[entry.increment]
                        if korean
                        else _INCREMENT_LABEL_EN[entry.increment]
                    )
                    lines.append(f"- {label}: #{entry.pr.number} {safe_title}")
            else:
                lines.append(
                    "- 이번 릴리스에 기록된 변경 사항이 "
                    "없습니다."
                    if korean
                    else "- No changes recorded for this release."
                )
            lines.append("")
        return lines

    lines = [
        f"## {PRODUCT_NAME} {version}",
        "",
        f"Source commit: `{source_commit}`",
        "",
        "# 한국어",
        "",
    ]
    lines.extend(render(korean=True))
    lines.extend(["---", "", "# English", ""])
    lines.extend(render(korean=False))
    return "\n".join(lines).rstrip() + "\n"


def release_for_target_commit(
    releases: Iterable[ReleaseRecord], target_commit: str
) -> ReleaseRecord | None:
    matches = [release for release in releases if release.target_commitish == target_commit]
    if not matches:
        return None
    stable = [release for release in matches if not release.draft and not release.prerelease]
    if stable:
        return max(stable, key=lambda release: SemanticVersion.parse_tag(release.tag_name))
    drafts = [release for release in matches if release.draft and not release.prerelease]
    if drafts:
        return max(drafts, key=lambda release: SemanticVersion.parse_tag(release.tag_name))
    return None


def release_plan_for_pr(
    pr: PullRequestMetadata,
    *,
    releases: Iterable[ReleaseRecord],
    main_commits: set[str],
    initial_version: str,
    target_commit: str | None = None,
) -> ReleasePlan:
    releases_tuple = tuple(releases)
    if target_commit is not None and pr.merge_commit_sha != target_commit:
        raise ReleasePolicyError("release PR merge commit does not match target commit")
    existing_for_target = (
        release_for_target_commit(releases_tuple, target_commit)
        if target_commit is not None
        else None
    )
    if existing_for_target is not None:
        version = SemanticVersion.parse_tag(existing_for_target.tag_name)
        return ReleasePlan(
            publish=existing_for_target.draft,
            previous_version=str(version),
            increment=None,
            new_version=str(version),
            tag=existing_for_target.tag_name,
            notes="",
            reason="existing release for target commit",
        )
    increment = release_increment_for_pr(pr)
    base = latest_stable_version(
        releases_tuple,
        main_commits=main_commits,
        initial_version=initial_version,
    )
    if increment is None:
        return ReleasePlan(
            publish=False,
            previous_version=str(base),
            increment=None,
            new_version=str(base),
            tag=base.tag,
            notes="",
            reason="release:none or ineligible PR",
        )
    is_first_stable = not has_stable_release(releases_tuple, main_commits=main_commits)
    version = base if is_first_stable else next_version(base, increment)
    source_commit = target_commit if target_commit is not None else pr.merge_commit_sha
    notes = (
        initial_release_notes(version=str(version), source_commit=source_commit)
        if is_first_stable
        else release_notes(pr, increment, str(version))
    )
    return ReleasePlan(
        publish=True,
        previous_version=str(base),
        increment=increment,
        new_version=str(version),
        tag=version.tag,
        notes=notes,
        reason="eligible merged main PR",
    )


def manual_release_plan(
    *,
    increment: ReleaseIncrement,
    releases: Iterable[ReleaseRecord],
    main_commits: set[str],
    initial_version: str,
    target_commit: str | None = None,
    notes_override: str | None = None,
) -> ReleasePlan:
    if increment == "none":
        raise ReleasePolicyError("manual release increment cannot be none")
    if notes_override is not None and not notes_override.strip():
        raise ReleasePolicyError("manual release notes override cannot be empty")
    releases_tuple = tuple(releases)
    existing_for_target = (
        release_for_target_commit(releases_tuple, target_commit)
        if target_commit is not None
        else None
    )
    if existing_for_target is not None:
        version = SemanticVersion.parse_tag(existing_for_target.tag_name)
        return ReleasePlan(
            publish=existing_for_target.draft,
            previous_version=str(version),
            increment=None,
            new_version=str(version),
            tag=existing_for_target.tag_name,
            notes="",
            reason="existing release for target commit",
        )
    base = latest_stable_version(
        releases_tuple,
        main_commits=main_commits,
        initial_version=initial_version,
    )
    version = (
        base
        if not has_stable_release(releases_tuple, main_commits=main_commits)
        else next_version(base, increment)
    )
    notes = notes_override if notes_override is not None else "\n".join(
        [f"## {PRODUCT_NAME} {version}", "", f"- {increment}: manual release", ""]
    )
    return ReleasePlan(
        publish=True,
        previous_version=str(base),
        increment=increment,
        new_version=str(version),
        tag=version.tag,
        notes=notes,
        reason="manual workflow dispatch",
    )


def assert_tag_and_release_can_be_created(
    *,
    tag: str,
    target_commit: str,
    existing_tags: dict[str, str],
    existing_releases: Iterable[ReleaseRecord],
) -> None:
    if tag in existing_tags:
        if existing_tags[tag] == target_commit:
            raise ReleasePolicyError(f"release tag already exists for this commit: {tag}")
        raise ReleasePolicyError(f"refusing to move existing release tag: {tag}")
    if any(release.tag_name == tag for release in existing_releases):
        raise ReleasePolicyError(f"release already exists: {tag}")


def release_rerun_result(
    *,
    tag: str,
    target_commit: str,
    existing_tags: dict[str, str],
    existing_releases: Iterable[ReleaseRecord],
) -> Literal["already-published", "resume-draft", "resume-with-tag", "create"]:
    releases = tuple(existing_releases)
    tag_target = existing_tags.get(tag)
    release = next((candidate for candidate in releases if candidate.tag_name == tag), None)
    if tag_target is not None and tag_target != target_commit:
        raise ReleasePolicyError(f"refusing to move existing release tag: {tag}")
    if release is not None and release.target_commitish != target_commit:
        raise ReleasePolicyError(f"release targets a different commit: {tag}")
    if tag_target == target_commit and release is not None and not release.draft:
        return "already-published"
    if tag_target == target_commit and release is not None and release.draft:
        return "resume-draft"
    if tag_target == target_commit:
        return "resume-with-tag"
    if release is not None:
        raise ReleasePolicyError(f"release exists without matching tag: {tag}")
    return "create"


def validate_release_summary(summary: ReleaseSummary) -> str:
    payload = {
        "source_commit": summary.source_commit,
        "previous_version": summary.previous_version,
        "increment": summary.increment,
        "new_version": summary.new_version,
        "tag": summary.tag,
        "artifact_names": list(summary.artifact_names),
        "checksums": list(summary.checksums),
        "release_url": summary.release_url,
    }
    return json.dumps(payload, sort_keys=True, indent=2)
