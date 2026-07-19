"""Task 048: Release Preparation.

Turns a Task 047 `ReleaseRecommendation` and the repository's current
version into a reviewable, local change set - never a release itself.
`prepare_release()` reads `pyproject.toml` (the authoritative version
source) and cross-checks the DevBot `[[package]]` entry in `uv.lock`,
computes the next semantic version, and updates both files to the same
target version. No Git tag, GitHub Release, package publication, commit,
push, or network call is ever made here - execution belongs to a later
task.

**Fail closed, validate before writing.** All reading, cross-checking, and
version calculation happens before either file is touched
(`plan_release_preparation`, also usable standalone for a dry-run preview).
A pre-existing mismatch between `pyproject.toml` and `uv.lock`'s versions
is never silently repaired - it always raises `VersionSourceMismatchError`.

**All-or-nothing writes.** Both new file contents are staged to temporary
files in the same directory before either real file is replaced via
`os.replace` (atomic on the same filesystem). Each individual replace is
atomic, but the pair is not a single filesystem transaction - if the
second `os.replace` fails after the first already succeeded,
`pyproject.toml` is rolled back to its exact original content via another
atomic replace before the exception propagates, so the two real files are
never left with mismatched versions between them.

**Formatting-preserving.** Only the exact matched version line is replaced
in each file via a precisely bounded text splice - everything else in
`pyproject.toml`/`uv.lock` (comments, ordering, unrelated packages) is left
byte-for-byte untouched. No TOML-writer dependency is introduced; `tomllib`
(standard library, read-only) is used only to read and validate.
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from devbot.release_classification import ReleaseRecommendation

PYPROJECT_FILENAME = "pyproject.toml"
UV_LOCK_FILENAME = "uv.lock"
_DEVBOT_PACKAGE_NAME = "devbot"

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_TOP_LEVEL_TABLE_HEADER_RE = re.compile(r"(?m)^\[[^\[\]]+\]\s*$")
_PACKAGE_HEADER_RE = re.compile(r"(?m)^\[\[package\]\]\s*$")
_PACKAGE_NAME_LINE_RE = re.compile(r'(?m)^name\s*=\s*"([^"]*)"\s*$')


class ReleasePreparationError(RuntimeError):
    """Base for a release that cannot be safely prepared."""


class NoReleaseRequiredError(ReleasePreparationError):
    """`ReleaseRecommendation.NONE` was passed - there is nothing to
    prepare, and no version is ever guessed for it."""


class MalformedProjectVersionError(ReleasePreparationError):
    """A version string is not a stable three-component numeric version
    (`X.Y.Z`) - pre-release identifiers and build metadata are out of
    scope for Task 048."""


class VersionSourceMismatchError(ReleasePreparationError):
    """`pyproject.toml`'s version and `uv.lock`'s `devbot` package version
    disagree. Never silently repaired - the operator must reconcile them
    first."""


class VersionSourceNotFoundError(ReleasePreparationError):
    """A required version entry is missing, duplicated, or otherwise not
    uniquely resolvable in `pyproject.toml` or `uv.lock`."""


@dataclass(frozen=True)
class ReleasePreparationResult:
    recommendation: ReleaseRecommendation
    old_version: str
    new_version: str
    changed_paths: tuple[str, ...]


def _validate_stable_version_format(version: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.match(version.strip())
    if match is None:
        raise MalformedProjectVersionError(
            f"only stable three-component numeric versions are supported, found {version!r}"
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def calculate_next_version(current: str, recommendation: ReleaseRecommendation) -> str:
    """Pure: `X.Y.Z` -> the next `X.Y.Z` per `recommendation`.
    `ReleaseRecommendation.NONE` always raises `NoReleaseRequiredError` -
    there is no next version for "no release required"."""
    if recommendation is ReleaseRecommendation.NONE:
        raise NoReleaseRequiredError(
            "ReleaseRecommendation.NONE requires no release preparation"
        )

    major, minor, patch = _validate_stable_version_format(current)

    if recommendation is ReleaseRecommendation.MAJOR:
        return f"{major + 1}.0.0"
    if recommendation is ReleaseRecommendation.MINOR:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _read_pyproject_version(text: str) -> str:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MalformedProjectVersionError(f"pyproject.toml is not valid TOML: {exc}") from exc

    project = data.get("project")
    if not isinstance(project, dict) or "version" not in project:
        raise VersionSourceNotFoundError("pyproject.toml has no [project].version")

    version = project["version"]
    if not isinstance(version, str):
        raise MalformedProjectVersionError(
            f"[project].version must be a string, found {version!r}"
        )
    return version


def _read_uv_lock_devbot_version(text: str) -> str:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MalformedProjectVersionError(f"uv.lock is not valid TOML: {exc}") from exc

    packages = data.get("package")
    if not isinstance(packages, list):
        raise VersionSourceNotFoundError("uv.lock has no [[package]] entries")

    matches = [
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == _DEVBOT_PACKAGE_NAME
    ]
    if not matches:
        raise VersionSourceNotFoundError(
            f"uv.lock has no [[package]] entry for {_DEVBOT_PACKAGE_NAME!r}"
        )
    if len(matches) > 1:
        raise VersionSourceNotFoundError(
            f"uv.lock has multiple [[package]] entries for {_DEVBOT_PACKAGE_NAME!r}"
        )

    version = matches[0].get("version")
    if not isinstance(version, str):
        raise MalformedProjectVersionError(
            f"devbot [[package]].version must be a string, found {version!r}"
        )
    return version


def _project_section_span(text: str) -> tuple[int, int]:
    headers = list(_TOP_LEVEL_TABLE_HEADER_RE.finditer(text))
    for index, header in enumerate(headers):
        if header.group(0).strip() == "[project]":
            start = header.start()
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            return start, end
    raise VersionSourceNotFoundError("pyproject.toml has no [project] section")


def _devbot_package_span(text: str) -> tuple[int, int]:
    headers = list(_PACKAGE_HEADER_RE.finditer(text))
    if not headers:
        raise VersionSourceNotFoundError("uv.lock has no [[package]] entries")

    matches: list[tuple[int, int]] = []
    for index, header in enumerate(headers):
        start = header.start()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        name_match = _PACKAGE_NAME_LINE_RE.search(text[start:end])
        if name_match is not None and name_match.group(1) == _DEVBOT_PACKAGE_NAME:
            matches.append((start, end))

    if not matches:
        raise VersionSourceNotFoundError(
            f"uv.lock has no [[package]] entry for {_DEVBOT_PACKAGE_NAME!r}"
        )
    if len(matches) > 1:
        raise VersionSourceNotFoundError(
            f"uv.lock has multiple [[package]] entries for {_DEVBOT_PACKAGE_NAME!r}"
        )
    return matches[0]


def _replace_version_in_span(
    text: str, start: int, end: int, old_version: str, new_version: str
) -> str:
    span_text = text[start:end]
    line_pattern = re.compile(
        r'(?m)^(\s*version\s*=\s*)"' + re.escape(old_version) + r'"([ \t]*)$'
    )
    match = line_pattern.search(span_text)
    if match is None:
        raise VersionSourceNotFoundError(
            f"could not locate 'version = \"{old_version}\"' to update in the expected section"
        )
    new_span_text = (
        span_text[: match.start()]
        + f'{match.group(1)}"{new_version}"{match.group(2)}'
        + span_text[match.end() :]
    )
    return text[:start] + new_span_text + text[end:]


def read_current_version(project_root: Path) -> str:
    """Reads and cross-validates the current version: `pyproject.toml`'s
    `[project].version` must exist and exactly match `uv.lock`'s `devbot`
    package entry. Read-only, no version calculation. Raises
    `VersionSourceNotFoundError`/`MalformedProjectVersionError`/
    `VersionSourceMismatchError` on any inconsistency. Shared by
    `plan_release_preparation` (Task 048) and Task 049's release-publish
    boundary, which must read the already-prepared version without
    duplicating this cross-check."""
    pyproject_path = project_root / PYPROJECT_FILENAME
    uv_lock_path = project_root / UV_LOCK_FILENAME
    if not pyproject_path.is_file():
        raise VersionSourceNotFoundError(f"{pyproject_path} does not exist")
    if not uv_lock_path.is_file():
        raise VersionSourceNotFoundError(f"{uv_lock_path} does not exist")

    pyproject_version = _read_pyproject_version(pyproject_path.read_text(encoding="utf-8"))
    uv_lock_version = _read_uv_lock_devbot_version(uv_lock_path.read_text(encoding="utf-8"))

    _validate_stable_version_format(pyproject_version)

    if pyproject_version != uv_lock_version:
        raise VersionSourceMismatchError(
            f"pyproject.toml version {pyproject_version!r} does not match uv.lock "
            f"devbot version {uv_lock_version!r}"
        )
    return pyproject_version


def plan_release_preparation(
    project_root: Path, recommendation: ReleaseRecommendation
) -> ReleasePreparationResult:
    """Pure read-only planning: validates `pyproject.toml`/`uv.lock`
    consistency and computes the target version, without writing anything.
    `prepare_release` calls this first; a CLI `--dry-run` can call this
    directly to preview without applying."""
    if recommendation is ReleaseRecommendation.NONE:
        raise NoReleaseRequiredError(
            "ReleaseRecommendation.NONE requires no release preparation"
        )

    pyproject_path = project_root / PYPROJECT_FILENAME
    uv_lock_path = project_root / UV_LOCK_FILENAME
    current_version = read_current_version(project_root)
    new_version = calculate_next_version(current_version, recommendation)

    return ReleasePreparationResult(
        recommendation=recommendation,
        old_version=current_version,
        new_version=new_version,
        changed_paths=(str(pyproject_path), str(uv_lock_path)),
    )


def _stage_temp_file(target_path: Path, content: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(
        dir=target_path.parent, prefix=f".{target_path.name}.", suffix=".tmp"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Path(tmp_name)


def prepare_release(
    project_root: Path, recommendation: ReleaseRecommendation
) -> ReleasePreparationResult:
    """Validates via `plan_release_preparation`, then updates
    `pyproject.toml` and the `devbot` entry in `uv.lock` to the same
    target version. Both writes are staged to temporary files first. If
    the second `os.replace` fails after the first one already succeeded,
    `pyproject.toml` is rolled back to its exact original content (via
    another atomic replace) before the exception propagates - the two
    real files are never left with a version mismatch between them: it is
    all-or-nothing, not just each individual file write being atomic. No
    Git, GitHub, or network operation is ever performed."""
    plan = plan_release_preparation(project_root, recommendation)

    pyproject_path = project_root / PYPROJECT_FILENAME
    uv_lock_path = project_root / UV_LOCK_FILENAME

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    uv_lock_text = uv_lock_path.read_text(encoding="utf-8")

    project_start, project_end = _project_section_span(pyproject_text)
    new_pyproject_text = _replace_version_in_span(
        pyproject_text, project_start, project_end, plan.old_version, plan.new_version
    )

    package_start, package_end = _devbot_package_span(uv_lock_text)
    new_uv_lock_text = _replace_version_in_span(
        uv_lock_text, package_start, package_end, plan.old_version, plan.new_version
    )

    pyproject_tmp = _stage_temp_file(pyproject_path, new_pyproject_text)
    try:
        uv_lock_tmp = _stage_temp_file(uv_lock_path, new_uv_lock_text)
    except BaseException:
        pyproject_tmp.unlink(missing_ok=True)
        raise

    try:
        os.replace(pyproject_tmp, pyproject_path)
    except BaseException:
        pyproject_tmp.unlink(missing_ok=True)
        uv_lock_tmp.unlink(missing_ok=True)
        raise

    try:
        os.replace(uv_lock_tmp, uv_lock_path)
    except BaseException:
        # The first replace already succeeded - restore pyproject.toml to
        # its exact original content via another atomic replace, so no
        # version mismatch between the two real files is ever left behind.
        uv_lock_tmp.unlink(missing_ok=True)
        rollback_tmp = _stage_temp_file(pyproject_path, pyproject_text)
        os.replace(rollback_tmp, pyproject_path)
        raise

    return plan
