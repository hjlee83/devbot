from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import venv
from pathlib import Path

import pytest
import yaml

from devbot.release import (
    RELEASE_NOTE_SECTIONS,
    PullRequestMetadata,
    ReleasePolicyError,
    ReleaseRecord,
    ReleaseSummary,
    SemanticVersion,
    assert_tag_and_release_can_be_created,
    authoritative_version,
    build_artifact,
    checksum_manifest,
    expected_artifact_names,
    initial_release_notes,
    latest_stable_version,
    manual_release_plan,
    next_version,
    release_artifact_name,
    release_increment_for_pr,
    release_notes,
    release_plan_for_pr,
    release_rerun_result,
    validate_release_summary,
)


def _clean_python_env(*, path: str | None = None, pythonpath: str | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONNOUSERSITE"] = "1"
    if path is not None:
        env["PATH"] = path
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    return env


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts" if sys.platform == "win32" else "bin") / "python"


def _venv_path(environment: Path) -> str:
    bin_dir = environment / ("Scripts" if sys.platform == "win32" else "bin")
    return f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _pr(
    *,
    labels: tuple[str, ...],
    merged: bool = True,
    base_ref: str = "main",
    merge_commit_sha: str = "abc123",
) -> PullRequestMetadata:
    return PullRequestMetadata(
        number=67,
        title="Task 032: Automated Release Pipeline",
        labels=labels,
        merged=merged,
        base_ref=base_ref,
        merge_commit_sha=merge_commit_sha,
    )



def test_release_pipeline_closed_unmerged_pr_is_not_treated_as_merged() -> None:
    spec = importlib.util.spec_from_file_location(
        "release_pipeline", Path("scripts/release_pipeline.py")
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pr = module._pr_metadata(
        {
            "pull_request": {
                "number": 67,
                "title": "Task 032",
                "labels": [{"name": "release:patch"}],
                "state": "closed",
                "merged": False,
                "merged_at": None,
                "base": {"ref": "main"},
                "merge_commit_sha": "commit-a",
            }
        }
    )

    assert pr.merged is False

def test_runtime_and_package_version_use_authoritative_version_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from devbot.main import main

    version = authoritative_version(Path.cwd())
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"devbot {version}\n"


def test_release_tag_and_embedded_version_must_match(tmp_path: Path) -> None:
    version = authoritative_version(Path.cwd())
    artifact = build_artifact(tmp_path, version=version, os_name="portable", architecture="python")

    assert SemanticVersion.parse_tag(f"v{version}") == SemanticVersion.parse(version)
    with tarfile.open(artifact.path) as archive:
        metadata = archive.extractfile("devbot-release/metadata.json")
        assert metadata is not None
        assert f'"version":"{version}"' in metadata.read().decode()


def test_release_increment_is_selected_from_merged_pr_label() -> None:
    assert release_increment_for_pr(_pr(labels=("release:patch",))) == "patch"
    assert release_increment_for_pr(_pr(labels=("release:minor",))) == "minor"
    assert release_increment_for_pr(_pr(labels=("release:major",))) == "major"
    assert release_increment_for_pr(_pr(labels=("release:patch",), merged=False)) is None
    assert release_increment_for_pr(_pr(labels=("release:patch",), base_ref="feature")) is None


def test_missing_or_conflicting_release_labels_fail_closed() -> None:
    with pytest.raises(ReleasePolicyError):
        release_increment_for_pr(_pr(labels=()))
    with pytest.raises(ReleasePolicyError):
        release_increment_for_pr(_pr(labels=("release:patch", "release:minor")))


def test_release_none_skips_publication() -> None:
    assert release_increment_for_pr(_pr(labels=("release:none",))) is None
    plan = release_plan_for_pr(
        _pr(labels=("release:none",)),
        releases=(ReleaseRecord("v0.2.0", "sha"),),
        main_commits={"sha"},
        initial_version="0.1.0",
    )
    assert plan.publish is False
    assert plan.new_version == "0.2.0"


def test_next_semantic_version_is_calculated_from_latest_stable_tag() -> None:
    stable = latest_stable_version(
        (
            ReleaseRecord("v0.2.9", "a"),
            ReleaseRecord("v0.3.0", "b"),
            ReleaseRecord("v0.2.10", "c"),
        ),
        main_commits={"a", "b", "c"},
        initial_version="0.1.0",
    )
    assert str(stable) == "0.3.0"
    assert str(next_version(stable, "patch")) == "0.3.1"
    assert str(next_version(stable, "minor")) == "0.4.0"
    assert str(next_version(stable, "major")) == "1.0.0"


def test_prerelease_draft_and_malformed_tags_are_ignored() -> None:
    stable = latest_stable_version(
        (
            ReleaseRecord("v9.0.0", "draft", draft=True),
            ReleaseRecord("v8.0.0", "pre", prerelease=True),
            ReleaseRecord("1.2.3", "main"),
            ReleaseRecord("v0.4.0-rc.1", "main"),
            ReleaseRecord("v0.3.0", "feature"),
        ),
        main_commits={"main"},
        initial_version="0.1.0",
    )
    assert str(stable) == "0.1.0"


def test_release_tag_targets_validated_main_commit() -> None:
    assert_tag_and_release_can_be_created(
        tag="v0.2.0",
        target_commit="main-sha",
        existing_tags={},
        existing_releases=(),
    )


def test_duplicate_tag_or_release_is_rejected_without_mutation() -> None:
    with pytest.raises(ReleasePolicyError):
        assert_tag_and_release_can_be_created(
            tag="v0.2.0",
            target_commit="new",
            existing_tags={"v0.2.0": "old"},
            existing_releases=(),
        )
    with pytest.raises(ReleasePolicyError):
        assert_tag_and_release_can_be_created(
            tag="v0.2.0",
            target_commit="new",
            existing_tags={},
            existing_releases=(ReleaseRecord("v0.2.0", "old"),),
        )


def test_release_artifact_names_are_deterministic() -> None:
    assert expected_artifact_names("0.2.0") == ("devbot-0.2.0-portable-python.tar.gz",)
    assert (
        release_artifact_name("0.2.0", "portable", "python")
        == "devbot-0.2.0-portable-python.tar.gz"
    )


def test_packaged_cli_reports_release_version(tmp_path: Path) -> None:
    artifact = build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")
    extract_dir = tmp_path / "extract"
    with tarfile.open(artifact.path) as archive:
        archive.extractall(extract_dir, filter="data")
    completed = subprocess.run(
        [str(extract_dir / "devbot-release" / "bin" / "devbot"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_python_env(),
    )
    assert completed.returncode == 0
    assert completed.stdout == "devbot 0.2.0\n"


def test_release_artifact_vendors_locked_runtime_dependencies(tmp_path: Path) -> None:
    artifact = build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")

    with tarfile.open(artifact.path) as archive:
        names = set(archive.getnames())

    assert "devbot-release/vendor/dotenv/__init__.py" in names
    assert "devbot-release/vendor/python_dotenv-1.2.2.dist-info/METADATA" in names
    assert "devbot-release/vendor/requests/__init__.py" in names
    assert "devbot-release/vendor/requests-2.34.2.dist-info/METADATA" in names
    assert "devbot-release/vendor/yaml/__init__.py" in names
    assert "devbot-release/vendor/pyyaml-6.0.3.dist-info/METADATA" in names


def test_packaged_cli_runs_in_clean_virtualenv_without_runtime_dependencies(tmp_path: Path) -> None:
    artifact = build_artifact(tmp_path, version="0.1.0", os_name="portable", architecture="python")
    extract_dir = tmp_path / "extract-clean"
    environment = tmp_path / "clean-venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = _venv_python(environment)

    missing = subprocess.run(
        [
            str(python),
            "-S",
            "-c",
            (
                "import importlib.util; "
                "raise SystemExit(importlib.util.find_spec('dotenv') is not None)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_python_env(path=_venv_path(environment)),
    )
    assert missing.returncode == 0

    with tarfile.open(artifact.path) as archive:
        archive.extractall(extract_dir, filter="data")
    completed = subprocess.run(
        [str(extract_dir / "devbot-release" / "bin" / "devbot"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_python_env(path=_venv_path(environment)),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "devbot 0.1.0\n"


def test_packaged_cli_uses_artifact_vendor_before_host_packages(tmp_path: Path) -> None:
    artifact = build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")
    extract_dir = tmp_path / "extract-vendor-first"
    fake_host = tmp_path / "fake-host"
    fake_host.mkdir()
    (fake_host / "dotenv.py").write_text(
        "raise RuntimeError('host dotenv used')\n", encoding="utf-8"
    )

    with tarfile.open(artifact.path) as archive:
        archive.extractall(extract_dir, filter="data")
    completed = subprocess.run(
        [str(extract_dir / "devbot-release" / "bin" / "devbot"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_python_env(pythonpath=str(fake_host)),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "devbot 0.2.0\n"


def test_release_artifact_contains_real_package_code_and_version_metadata(tmp_path: Path) -> None:
    artifact = build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")
    extract_dir = tmp_path / "extract-real"
    with tarfile.open(artifact.path) as archive:
        names = set(archive.getnames())
        archive.extractall(extract_dir, filter="data")

    assert "devbot-release/src/devbot/main.py" in names
    assert "devbot-release/src/devbot/release.py" in names
    pyproject = (extract_dir / "devbot-release" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in pyproject
    completed = subprocess.run(
        [str(extract_dir / "devbot-release" / "bin" / "devbot"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_python_env(),
    )
    assert completed.returncode == 0
    assert completed.stdout == "devbot 0.2.0\n"


def test_release_artifact_generation_is_reproducible(tmp_path: Path) -> None:
    first = build_artifact(
        tmp_path / "first",
        version="0.2.0",
        os_name="portable",
        architecture="python",
    )
    second = build_artifact(
        tmp_path / "second",
        version="0.2.0",
        os_name="portable",
        architecture="python",
    )

    assert first.path.read_bytes() == second.path.read_bytes()


def test_checksum_manifest_covers_every_release_artifact(tmp_path: Path) -> None:
    artifacts = [
        build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")
    ]
    manifest = checksum_manifest(artifacts, expected_names=expected_artifact_names("0.2.0"))

    for name in expected_artifact_names("0.2.0"):
        assert f"  {name}\n" in manifest


def test_checksum_manifest_is_deterministic(tmp_path: Path) -> None:
    artifacts = [
        build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python"),
    ]
    expected = expected_artifact_names("0.2.0")

    assert checksum_manifest(artifacts, expected_names=expected) == checksum_manifest(
        reversed(artifacts), expected_names=expected
    )
    with pytest.raises(ReleasePolicyError):
        checksum_manifest([], expected_names=expected)


def _assert_standard_release_note_sections(notes: str) -> None:
    for section in RELEASE_NOTE_SECTIONS:
        assert f"### {section}\n" in notes
    assert notes.index("### What's New") < notes.index("### Improvements")
    assert notes.index("### Improvements") < notes.index("### Fixes")
    assert notes.index("### Fixes") < notes.index("### Operational Changes")
    assert notes.index("### Operational Changes") < notes.index("### Upgrade Notes")
    assert notes.index("### Upgrade Notes") < notes.index("### Known Limitations")


def test_release_note_generation_is_deterministic() -> None:
    notes = release_notes(_pr(labels=("release:minor",)), "minor", "0.2.0")

    assert notes.startswith("## devbot 0.2.0\n\n")
    _assert_standard_release_note_sections(notes)
    assert "- minor: #67 Task 032: Automated Release Pipeline" in notes
    assert "No additional improvements recorded for this release." in notes


def test_release_plan_uses_pr_label_and_latest_stable_release() -> None:
    plan = release_plan_for_pr(
        _pr(labels=("release:minor",)),
        releases=(
            ReleaseRecord("v0.2.0", "old"),
            ReleaseRecord("v0.3.0", "draft", draft=True),
        ),
        main_commits={"old", "new"},
        initial_version="0.1.0",
    )

    assert plan.publish is True
    assert plan.previous_version == "0.2.0"
    assert plan.increment == "minor"
    assert plan.new_version == "0.3.0"
    assert plan.tag == "v0.3.0"


def test_release_plan_bootstraps_first_stable_release_from_authoritative_initial_version() -> None:
    plan = release_plan_for_pr(
        _pr(labels=("release:minor",), merge_commit_sha="commit-a"),
        releases=(
            ReleaseRecord("v0.1.0-alpha.1", "commit-zero", prerelease=True),
            ReleaseRecord("v0.0.9", "feature-commit"),
        ),
        main_commits={"commit-zero", "commit-a"},
        initial_version="0.1.0",
        target_commit="commit-a",
    )

    assert plan.publish is True
    assert plan.previous_version == "0.1.0"
    assert plan.increment == "minor"
    assert plan.new_version == "0.1.0"
    assert plan.tag == "v0.1.0"
    _assert_standard_release_note_sections(plan.notes)
    assert "Source commit: `commit-a`" in plan.notes
    assert "Portable Python release artifact" in plan.notes
    assert "Runtime automatic update discovery" in plan.notes
    assert "- minor: #67 Task 032: Automated Release Pipeline" not in plan.notes


def test_release_plan_bootstraps_first_stable_release_regardless_of_increment_label() -> None:
    plan = release_plan_for_pr(
        _pr(labels=("release:major",), merge_commit_sha="commit-major"),
        releases=(ReleaseRecord("v0.1.0-alpha.1", "commit-zero", prerelease=True),),
        main_commits={"commit-zero", "commit-major"},
        initial_version="0.1.0",
        target_commit="commit-major",
    )

    assert plan.publish is True
    assert plan.increment == "major"
    assert plan.new_version == "0.1.0"
    assert plan.tag == "v0.1.0"
    assert "Source commit: `commit-major`" in plan.notes


def test_manual_release_plan_bootstraps_first_stable_from_initial_version() -> None:
    plan = manual_release_plan(
        increment="patch",
        releases=(ReleaseRecord("v0.1.0-alpha.1", "commit-zero", prerelease=True),),
        main_commits={"commit-zero", "commit-a"},
        initial_version="0.1.0",
        target_commit="commit-a",
    )

    assert plan.publish is True
    assert plan.previous_version == "0.1.0"
    assert plan.increment == "patch"
    assert plan.new_version == "0.1.0"
    assert plan.tag == "v0.1.0"



def test_release_plan_reuses_existing_stable_release_for_target_commit() -> None:
    plan = release_plan_for_pr(
        _pr(labels=("release:patch",), merge_commit_sha="commit-a"),
        releases=(
            ReleaseRecord("v0.1.0", "commit-zero"),
            ReleaseRecord("v0.1.1", "commit-a"),
        ),
        main_commits={"commit-zero", "commit-a"},
        initial_version="0.1.0",
        target_commit="commit-a",
    )

    assert plan.publish is False
    assert plan.increment is None
    assert plan.previous_version == "0.1.1"
    assert plan.new_version == "0.1.1"
    assert plan.tag == "v0.1.1"
    assert plan.reason == "existing release for target commit"


def test_release_plan_recovers_existing_draft_release_for_target_commit() -> None:
    plan = release_plan_for_pr(
        _pr(labels=("release:patch",), merge_commit_sha="commit-a"),
        releases=(
            ReleaseRecord("v0.1.0", "commit-zero"),
            ReleaseRecord("v0.1.1", "commit-a", draft=True),
        ),
        main_commits={"commit-zero", "commit-a"},
        initial_version="0.1.0",
        target_commit="commit-a",
    )

    assert plan.publish is True
    assert plan.increment is None
    assert plan.new_version == "0.1.1"
    assert plan.tag == "v0.1.1"


def test_release_plan_bumps_next_commit_after_existing_release() -> None:
    plan = release_plan_for_pr(
        _pr(labels=("release:patch",), merge_commit_sha="commit-b"),
        releases=(ReleaseRecord("v0.1.1", "commit-a"),),
        main_commits={"commit-a", "commit-b"},
        initial_version="0.1.0",
        target_commit="commit-b",
    )

    assert plan.publish is True
    assert plan.previous_version == "0.1.1"
    assert plan.increment == "patch"
    assert plan.new_version == "0.1.2"
    assert plan.tag == "v0.1.2"


def test_release_plan_rejects_target_commit_mismatch() -> None:
    with pytest.raises(ReleasePolicyError, match="merge commit does not match"):
        release_plan_for_pr(
            _pr(labels=("release:patch",), merge_commit_sha="commit-other"),
            releases=(),
            main_commits={"commit-a"},
            initial_version="0.1.0",
            target_commit="commit-a",
        )

def test_release_rerun_is_idempotent_for_same_commit() -> None:
    assert (
        release_rerun_result(
            tag="v0.2.0",
            target_commit="sha",
            existing_tags={"v0.2.0": "sha"},
            existing_releases=(ReleaseRecord("v0.2.0", "sha"),),
        )
        == "already-published"
    )
    assert (
        release_rerun_result(
            tag="v0.2.0",
            target_commit="sha",
            existing_tags={"v0.2.0": "sha"},
            existing_releases=(),
        )
        == "resume-with-tag"
    )
    assert (
        release_rerun_result(
            tag="v0.2.0",
            target_commit="sha",
            existing_tags={"v0.2.0": "sha"},
            existing_releases=(ReleaseRecord("v0.2.0", "sha", draft=True),),
        )
        == "resume-draft"
    )
    with pytest.raises(ReleasePolicyError):
        release_rerun_result(
            tag="v0.2.0",
            target_commit="sha",
            existing_tags={"v0.2.0": "other"},
            existing_releases=(),
        )


def test_release_workflow_resumes_partial_publication_states() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text(encoding="utf-8"))
    publish_steps = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["publish-release"]["steps"]
    )
    step_conditions = [
        step.get("if", "") for step in workflow["jobs"]["publish-release"]["steps"]
    ]

    assert "release_state=already-published" in publish_steps
    assert "release_state=resume-with-tag" in publish_steps
    assert "release_state=resume-draft" in publish_steps
    assert "--clobber" in publish_steps
    assert "Stable release $TAG exists but assets are incomplete" in publish_steps
    assert "env.release_state == 'create'" in step_conditions
    assert "env.release_state != 'already-published'" in step_conditions


def test_release_workflow_uses_full_history_target_commit_plan_and_ancestry_check() -> None:
    workflow_text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow_text
    assert "git merge-base --is-ancestor" in workflow_text
    assert "git branch -r --contains" not in workflow_text
    assert "--target-commit" in workflow_text


def test_safe_summary_fixture_contains_audit_fields_without_credentials() -> None:
    rendered = validate_release_summary(
        ReleaseSummary(
            source_commit="sha",
            previous_version="0.1.0",
            increment="patch",
            new_version="0.1.1",
            tag="v0.1.1",
            artifact_names=("devbot-0.1.1-portable-python.tar.gz",),
            checksums=("abc  devbot-0.1.1-portable-python.tar.gz",),
            release_url="https://github.com/hjlee83/devbot/releases/tag/v0.1.1",
        )
    )
    assert "source_commit" in rendered
    assert "authorization" not in rendered.lower()
    assert "token" not in rendered.lower()


def test_release_workflow_structure_enforces_validation_before_publication() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text(encoding="utf-8"))

    assert "pull_request" not in workflow[True]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["jobs"]["publish-release"]["needs"] == [
        "validate-main",
        "plan-release",
        "build-artifacts",
    ]
    assert workflow["jobs"]["publish-release"]["permissions"] == {"contents": "write"}
    assert "gh api" in "\n".join(
        step.get("run", "") for step in workflow["jobs"]["plan-release"]["steps"]
    )
    assert "scripts/release_pipeline.py plan" in "\n".join(
        step.get("run", "") for step in workflow["jobs"]["plan-release"]["steps"]
    )


def test_release_workflow_uses_portable_artifact_and_manual_dispatch() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text(encoding="utf-8"))
    build_job = workflow["jobs"]["build-artifacts"]
    build_steps = "\n".join(step.get("run", "") for step in build_job["steps"])

    assert "strategy" not in build_job
    assert "--os-name portable" in build_steps
    assert "--architecture python" in build_steps
    assert "Smoke packaged DevBot" in [step.get("name") for step in build_job["steps"]]
    assert workflow[True]["workflow_dispatch"]["inputs"]["increment"]["options"] == [
        "patch",
        "minor",
        "major",
    ]


def test_release_pipeline_plan_command_writes_github_outputs(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    releases = tmp_path / "releases.json"
    main_commits = tmp_path / "main-commits.json"
    output = tmp_path / "plan.json"
    github_output = tmp_path / "github-output.txt"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 67,
                    "title": "Task 032: Automated Release Pipeline",
                    "labels": [{"name": "release:patch"}],
                    "merged": True,
                    "base": {"ref": "main"},
                    "merge_commit_sha": "new",
                }
            }
        ),
        encoding="utf-8",
    )
    releases.write_text(
        json.dumps([{"tag_name": "v0.2.0", "target_commitish": "old"}]),
        encoding="utf-8",
    )
    main_commits.write_text(json.dumps(["old", "new"]), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/release_pipeline.py",
            "plan",
            "--event-name",
            "push",
            "--event-json",
            str(event),
            "--releases-json",
            str(releases),
            "--main-commits-json",
            str(main_commits),
            "--initial-version",
            "0.1.0",
            "--target-commit",
            "new",
            "--output",
            str(output),
            "--github-output",
            str(github_output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["publish"] is True
    assert plan["previous_version"] == "0.2.0"
    assert plan["increment"] == "patch"
    assert plan["version"] == "0.2.1"
    assert plan["tag"] == "v0.2.1"
    assert "publish=true" in github_output.read_text(encoding="utf-8")


def test_release_pipeline_plan_command_bootstraps_first_stable_release(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    releases = tmp_path / "releases.json"
    main_commits = tmp_path / "main-commits.json"
    output = tmp_path / "plan.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 69,
                    "title": "Task 033: Bootstrap Initial Release",
                    "labels": [{"name": "release:minor"}],
                    "merged": True,
                    "base": {"ref": "main"},
                    "merge_commit_sha": "initial-main",
                }
            }
        ),
        encoding="utf-8",
    )
    releases.write_text(
        json.dumps(
            [{"tag_name": "v0.1.0-alpha.1", "target_commitish": "old", "prerelease": True}]
        ),
        encoding="utf-8",
    )
    main_commits.write_text(json.dumps(["old", "initial-main"]), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/release_pipeline.py",
            "plan",
            "--event-name",
            "push",
            "--event-json",
            str(event),
            "--releases-json",
            str(releases),
            "--main-commits-json",
            str(main_commits),
            "--initial-version",
            "0.1.0",
            "--target-commit",
            "initial-main",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["publish"] is True
    assert plan["previous_version"] == "0.1.0"
    assert plan["increment"] == "minor"
    assert plan["version"] == "0.1.0"
    assert plan["tag"] == "v0.1.0"


def test_initial_release_notes_use_standard_future_sections() -> None:
    notes = initial_release_notes(version="0.1.0", source_commit="6526cfe")

    assert notes.startswith("## devbot 0.1.0\n")
    assert "Source commit: `6526cfe`" in notes
    for section in RELEASE_NOTE_SECTIONS:
        assert f"### {section}\n" in notes
    assert notes.index("### What's New") < notes.index("### Improvements")
    assert "Portable Python release artifact" in notes
    assert "Runtime automatic update discovery" in notes


def test_history_marks_initial_release_pending_and_preserves_required_milestones() -> None:
    history = Path("docs/history.md").read_text(encoding="utf-8")

    expected_sections = [
        "## Release Notes Format",
        "## Stable Releases",
        "## Development Milestones Through Task 032",
        "## Initial Release Notes",
    ]
    for section in expected_sections:
        assert section in history

    release_note_sections = [
        f"{index}. {section}" for index, section in enumerate(RELEASE_NOTE_SECTIONS, 1)
    ]
    assert "\n".join(release_note_sections) in history

    assert "pending operator-controlled publication" in history
    assert "No official stable GitHub Release has been published yet." in history
    assert "this document does not claim that" in history
    assert "- Intended Release URL:" in history
    assert "- Release URL:" not in history
    assert "retrospective stable\nRelease tags" in history

    for milestone in (
        "Task 000",
        "Tasks 001-005",
        "Tasks 006-009",
        "Tasks 010-012",
        "Tasks 013-020",
        "Tasks 021-027",
        "Task 028",
        "Task 029",
        "Task 030",
        "Task 031",
        "Task 032",
    ):
        assert milestone in history


def test_first_stable_release_uses_authoritative_initial_version_and_artifact_contract(
    tmp_path: Path,
) -> None:
    version = authoritative_version(Path.cwd())
    assert version == "0.1.0"
    assert SemanticVersion.parse(version).tag == "v0.1.0"

    artifact = build_artifact(tmp_path, version=version, os_name="portable", architecture="python")
    manifest = checksum_manifest([artifact], expected_names=expected_artifact_names(version))

    assert artifact.name == "devbot-0.1.0-portable-python.tar.gz"
    assert f"  {artifact.name}\n" in manifest


def test_initial_release_rejects_prior_stable_release_or_moved_tag() -> None:
    with pytest.raises(ReleasePolicyError, match="release already exists"):
        assert_tag_and_release_can_be_created(
            tag="v0.1.0",
            target_commit="6526cfe",
            existing_tags={},
            existing_releases=(ReleaseRecord("v0.1.0", "old-main"),),
        )
    with pytest.raises(ReleasePolicyError, match="refusing to move"):
        assert_tag_and_release_can_be_created(
            tag="v0.1.0",
            target_commit="6526cfe",
            existing_tags={"v0.1.0": "old-main"},
            existing_releases=(),
        )
