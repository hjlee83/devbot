"""Deterministic release pipeline policy helpers."""

from __future__ import annotations

import hashlib
import io
import json
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
SUPPORTED_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("macos", "arm64"),
    ("macos", "x86_64"),
    ("linux", "x86_64"),
    ("linux", "arm64"),
)
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


def authoritative_version(project_root: Path | str | None = None) -> str:
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
        "os": os_name,
        "architecture": architecture,
    }


def build_artifact(
    output_dir: Path,
    *,
    version: str,
    os_name: str,
    architecture: str,
) -> Artifact:
    name = release_artifact_name(version, os_name, architecture)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    metadata_bytes = json.dumps(
        build_metadata(version, os_name, architecture),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    version_script = f"#!/usr/bin/env sh\nprintf 'devbot {version}\\n'\n".encode()

    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=9) as archive:
        _add_bytes(archive, "devbot-release/metadata.json", metadata_bytes)
        _add_bytes(archive, "devbot-release/bin/devbot", version_script, mode=0o755)
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
