from __future__ import annotations

from pathlib import Path

import pytest

from devbot.release_classification import ReleaseRecommendation
from devbot.release_preparation import (
    MalformedProjectVersionError,
    NoReleaseRequiredError,
    ReleasePreparationError,
    VersionSourceMismatchError,
    VersionSourceNotFoundError,
    calculate_next_version,
    plan_release_preparation,
    prepare_release,
)

_PYPROJECT_TEMPLATE = """[project]
name = "devbot"
version = "{version}"
description = "Local GitHub-issue-driven coding agent orchestrator"
readme = "README.md"
requires-python = ">=3.13,<3.14"
dependencies = [
    "pyyaml>=6.0",
]

[project.scripts]
devbot = "devbot.main:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
"""

_UV_LOCK_TEMPLATE = """version = 1
revision = 3
requires-python = "==3.13.*"

[[package]]
name = "idna"
version = "3.18"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://example.invalid/idna.tar.gz", hash = "sha256:abc" }}

[[package]]
name = "devbot"
version = "{version}"
source = {{ editable = "." }}
dependencies = [
    {{ name = "pyyaml" }},
]

[package.metadata]
requires-dist = [
    {{ name = "pyyaml", specifier = ">=6.0" }},
]
"""


def _write_project(
    tmp_path: Path,
    *,
    pyproject_version: str = "0.1.2",
    uv_lock_version: str | None = None,
    pyproject_text: str | None = None,
    uv_lock_text: str | None = None,
) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        pyproject_text
        if pyproject_text is not None
        else _PYPROJECT_TEMPLATE.format(version=pyproject_version),
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        uv_lock_text
        if uv_lock_text is not None
        else _UV_LOCK_TEMPLATE.format(version=uv_lock_version or pyproject_version),
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------
# calculate_next_version: pure, no filesystem
# --------------------------------------------------------------------------


def test_patch_calculation() -> None:
    assert calculate_next_version("0.1.2", ReleaseRecommendation.PATCH) == "0.1.3"


def test_minor_calculation() -> None:
    assert calculate_next_version("0.1.2", ReleaseRecommendation.MINOR) == "0.2.0"


def test_major_calculation() -> None:
    assert calculate_next_version("0.1.2", ReleaseRecommendation.MAJOR) == "1.0.0"


def test_none_recommendation_raises() -> None:
    with pytest.raises(NoReleaseRequiredError):
        calculate_next_version("0.1.2", ReleaseRecommendation.NONE)


@pytest.mark.parametrize(
    "malformed", ["1.2", "1.2.3.4", "1.2.x", "", "v1.2.3", "1.2.3-alpha", "1.2.3+build"]
)
def test_malformed_version_raises(malformed: str) -> None:
    with pytest.raises(MalformedProjectVersionError):
        calculate_next_version(malformed, ReleaseRecommendation.PATCH)


# --------------------------------------------------------------------------
# plan_release_preparation: read-only, validates, never writes
# --------------------------------------------------------------------------


def test_plan_release_preparation_does_not_write_anything(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")
    pyproject_before = (root / "pyproject.toml").read_text(encoding="utf-8")
    uv_lock_before = (root / "uv.lock").read_text(encoding="utf-8")

    plan = plan_release_preparation(root, ReleaseRecommendation.PATCH)

    assert plan.old_version == "0.1.2"
    assert plan.new_version == "0.1.3"
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == pyproject_before
    assert (root / "uv.lock").read_text(encoding="utf-8") == uv_lock_before


def test_plan_release_preparation_none_raises() -> None:
    with pytest.raises(NoReleaseRequiredError):
        plan_release_preparation(Path("/nonexistent"), ReleaseRecommendation.NONE)


def test_plan_release_preparation_missing_pyproject_raises(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(_UV_LOCK_TEMPLATE.format(version="0.1.2"), encoding="utf-8")

    with pytest.raises(VersionSourceNotFoundError):
        plan_release_preparation(tmp_path, ReleaseRecommendation.PATCH)


def test_plan_release_preparation_missing_uv_lock_raises(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        _PYPROJECT_TEMPLATE.format(version="0.1.2"), encoding="utf-8"
    )

    with pytest.raises(VersionSourceNotFoundError):
        plan_release_preparation(tmp_path, ReleaseRecommendation.PATCH)


def test_missing_project_version_raises(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        pyproject_text='[project]\nname = "devbot"\n',
        uv_lock_text=_UV_LOCK_TEMPLATE.format(version="0.1.2"),
    )

    with pytest.raises(VersionSourceNotFoundError):
        plan_release_preparation(root, ReleaseRecommendation.PATCH)


def test_missing_uv_lock_devbot_entry_raises(tmp_path: Path) -> None:
    uv_lock_without_devbot = """version = 1
requires-python = "==3.13.*"

[[package]]
name = "idna"
version = "3.18"
source = { registry = "https://pypi.org/simple" }
"""
    root = _write_project(
        tmp_path,
        pyproject_text=_PYPROJECT_TEMPLATE.format(version="0.1.2"),
        uv_lock_text=uv_lock_without_devbot,
    )

    with pytest.raises(VersionSourceNotFoundError):
        plan_release_preparation(root, ReleaseRecommendation.PATCH)


def test_duplicate_uv_lock_devbot_entry_raises(tmp_path: Path) -> None:
    uv_lock_with_duplicate = """version = 1
requires-python = "==3.13.*"

[[package]]
name = "devbot"
version = "0.1.2"
source = { editable = "." }

[[package]]
name = "devbot"
version = "0.1.2"
source = { editable = "." }
"""
    root = _write_project(
        tmp_path,
        pyproject_text=_PYPROJECT_TEMPLATE.format(version="0.1.2"),
        uv_lock_text=uv_lock_with_duplicate,
    )

    with pytest.raises(VersionSourceNotFoundError):
        plan_release_preparation(root, ReleaseRecommendation.PATCH)


def test_malformed_pyproject_version_raises(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        pyproject_text=_PYPROJECT_TEMPLATE.format(version="not-a-version"),
        uv_lock_text=_UV_LOCK_TEMPLATE.format(version="not-a-version"),
    )

    with pytest.raises(MalformedProjectVersionError):
        plan_release_preparation(root, ReleaseRecommendation.PATCH)


def test_version_source_mismatch_raises(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2", uv_lock_version="0.1.1")

    with pytest.raises(VersionSourceMismatchError):
        plan_release_preparation(root, ReleaseRecommendation.PATCH)


def test_version_source_mismatch_does_not_write_either_file(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2", uv_lock_version="0.1.1")
    pyproject_before = (root / "pyproject.toml").read_text(encoding="utf-8")
    uv_lock_before = (root / "uv.lock").read_text(encoding="utf-8")

    with pytest.raises(VersionSourceMismatchError):
        prepare_release(root, ReleaseRecommendation.PATCH)

    assert (root / "pyproject.toml").read_text(encoding="utf-8") == pyproject_before
    assert (root / "uv.lock").read_text(encoding="utf-8") == uv_lock_before


# --------------------------------------------------------------------------
# prepare_release: successful two-file preparation
# --------------------------------------------------------------------------


def test_prepare_release_patch_updates_both_files(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")

    result = prepare_release(root, ReleaseRecommendation.PATCH)

    assert result.old_version == "0.1.2"
    assert result.new_version == "0.1.3"
    assert result.recommendation is ReleaseRecommendation.PATCH
    assert 'version = "0.1.3"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.3"' in (root / "uv.lock").read_text(encoding="utf-8")


def test_prepare_release_minor_updates_both_files(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")

    result = prepare_release(root, ReleaseRecommendation.MINOR)

    assert result.new_version == "0.2.0"


def test_prepare_release_major_updates_both_files(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")

    result = prepare_release(root, ReleaseRecommendation.MAJOR)

    assert result.new_version == "1.0.0"


def test_prepare_release_none_raises_before_touching_files(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")
    pyproject_before = (root / "pyproject.toml").read_text(encoding="utf-8")
    uv_lock_before = (root / "uv.lock").read_text(encoding="utf-8")

    with pytest.raises(NoReleaseRequiredError):
        prepare_release(root, ReleaseRecommendation.NONE)

    assert (root / "pyproject.toml").read_text(encoding="utf-8") == pyproject_before
    assert (root / "uv.lock").read_text(encoding="utf-8") == uv_lock_before


def test_prepare_release_reports_changed_paths(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")

    result = prepare_release(root, ReleaseRecommendation.PATCH)

    assert str(root / "pyproject.toml") in result.changed_paths
    assert str(root / "uv.lock") in result.changed_paths
    assert len(result.changed_paths) == 2


def test_prepare_release_preserves_unrelated_content(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")
    pyproject_before_lines = (root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    uv_lock_before_lines = (root / "uv.lock").read_text(encoding="utf-8").splitlines()

    prepare_release(root, ReleaseRecommendation.PATCH)

    pyproject_after_lines = (root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    uv_lock_after_lines = (root / "uv.lock").read_text(encoding="utf-8").splitlines()

    assert len(pyproject_after_lines) == len(pyproject_before_lines)
    pyproject_diff = [
        (before, after)
        for before, after in zip(pyproject_before_lines, pyproject_after_lines, strict=True)
        if before != after
    ]
    assert pyproject_diff == [('version = "0.1.2"', 'version = "0.1.3"')]

    assert len(uv_lock_after_lines) == len(uv_lock_before_lines)
    uv_lock_diff = [
        (before, after)
        for before, after in zip(uv_lock_before_lines, uv_lock_after_lines, strict=True)
        if before != after
    ]
    assert uv_lock_diff == [('version = "0.1.2"', 'version = "0.1.3"')]


def test_prepare_release_does_not_touch_other_packages_version_in_uv_lock(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")

    prepare_release(root, ReleaseRecommendation.PATCH)

    assert 'version = "3.18"' in (root / "uv.lock").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Repeated preparation: no corruption, explicit behavior
# --------------------------------------------------------------------------


def test_repeated_preparation_advances_again_without_corruption(tmp_path: Path) -> None:
    root = _write_project(tmp_path, pyproject_version="0.1.2")

    first = prepare_release(root, ReleaseRecommendation.PATCH)
    assert first.old_version == "0.1.2"
    assert first.new_version == "0.1.3"

    second = prepare_release(root, ReleaseRecommendation.PATCH)
    assert second.old_version == "0.1.3"
    assert second.new_version == "0.1.4"

    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    uv_lock_text = (root / "uv.lock").read_text(encoding="utf-8")
    assert 'version = "0.1.4"' in pyproject_text
    assert 'version = "0.1.4"' in uv_lock_text
    assert pyproject_text.count('version = "0.1.4"') == 1
    assert uv_lock_text.count('version = "0.1.4"') == 1


# --------------------------------------------------------------------------
# Error taxonomy
# --------------------------------------------------------------------------


def test_all_errors_are_release_preparation_errors() -> None:
    for error_type in (
        NoReleaseRequiredError,
        MalformedProjectVersionError,
        VersionSourceMismatchError,
        VersionSourceNotFoundError,
    ):
        assert issubclass(error_type, ReleasePreparationError)
