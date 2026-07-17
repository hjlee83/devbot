"""Deterministic release pipeline policy helpers."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import stat
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
SUPPORTED_PLATFORMS: tuple[tuple[str, str], ...] = (("portable", "python"),)
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
    release_version = os.environ.get("DEVBOT_RELEASE_VERSION")
    if release_version:
        SemanticVersion.parse(release_version)
        return release_version
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
        "package_version": version,
        "artifact_contract": "portable-python-source",
        "os": os_name,
        "architecture": architecture,
    }


def _launcher_script(version: str) -> bytes:
    script = f"""#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
export DEVBOT_RELEASE_VERSION="{version}"
export PYTHONPATH="$ROOT_DIR/lib${{PYTHONPATH:+:$PYTHONPATH}}"
exec "${{PYTHON:-python3}}" -c 'from devbot.main import main; raise SystemExit(main())' "$@"
"""
    return script.encode()


def _iter_package_files(project_root: Path) -> Iterable[tuple[Path, str]]:
    package_root = project_root / "src" / "devbot"
    for path in sorted(package_root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path, f"devbot-release/lib/devbot/{path.relative_to(package_root)}"


def build_artifact(
    output_dir: Path,
    *,
    version: str,
    os_name: str,
    architecture: str,
    project_root: Path | str = ".",
) -> Artifact:
    name = release_artifact_name(version, os_name, architecture)
    project_root = Path(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    metadata_bytes = json.dumps(
        build_metadata(version, os_name, architecture),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

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
        _add_bytes(archive, "devbot-release/bin/devbot", _launcher_script(version), mode=0o755)
        _add_bytes(
            archive,
            "devbot-release/pyproject.toml",
            (project_root / "pyproject.toml").read_bytes(),
        )
        for source, archive_name in _iter_package_files(project_root):
            mode = stat.S_IMODE(source.stat().st_mode) or 0o644
            _add_bytes(archive, archive_name, source.read_bytes(), mode=mode)
    return Artifact(name=name, path=path, os_name=os_name, architecture=architecture)


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    archive.addfile(info, fileobj=io.BytesIO(data))


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
    return "\n".join(
        [
            f"## {PRODUCT_NAME} {version}",
            "",
            f"- {increment}: #{pr.number} {safe_title}",
            "",
        ]
    )


def release_plan_for_pr(
    pr: PullRequestMetadata,
    *,
    releases: Iterable[ReleaseRecord],
    main_commits: set[str],
    initial_version: str,
) -> ReleasePlan:
    increment = release_increment_for_pr(pr)
    base = latest_stable_version(
        releases,
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
    version = next_version(base, increment)
    return ReleasePlan(
        publish=True,
        previous_version=str(base),
        increment=increment,
        new_version=str(version),
        tag=version.tag,
        notes=release_notes(pr, increment, str(version)),
        reason="eligible merged main PR",
    )


def manual_release_plan(
    *,
    increment: ReleaseIncrement,
    releases: Iterable[ReleaseRecord],
    main_commits: set[str],
    initial_version: str,
) -> ReleasePlan:
    if increment == "none":
        raise ReleasePolicyError("manual release increment cannot be none")
    base = latest_stable_version(
        releases,
        main_commits=main_commits,
        initial_version=initial_version,
    )
    version = next_version(base, increment)
    notes = "\n".join([f"## {PRODUCT_NAME} {version}", "", f"- {increment}: manual release", ""])
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
) -> Literal["already-published", "create"]:
    releases = tuple(existing_releases)
    if existing_tags.get(tag) == target_commit and any(
        release.tag_name == tag and release.target_commitish == target_commit
        for release in releases
    ):
        return "already-published"
    assert_tag_and_release_can_be_created(
        tag=tag,
        target_commit=target_commit,
        existing_tags=existing_tags,
        existing_releases=releases,
    )
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
