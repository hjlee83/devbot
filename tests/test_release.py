from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from devbot.release import (
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
    latest_stable_version,
    next_version,
    release_artifact_name,
    release_increment_for_pr,
    release_notes,
    release_plan_for_pr,
    release_rerun_result,
    validate_release_summary,
)


def _pr(
    *, labels: tuple[str, ...], merged: bool = True, base_ref: str = "main"
) -> PullRequestMetadata:
    return PullRequestMetadata(
        number=67,
        title="Task 032: Automated Release Pipeline",
        labels=labels,
        merged=merged,
        base_ref=base_ref,
        merge_commit_sha="abc123",
    )


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
    assert expected_artifact_names("0.2.0") == (
        "devbot-0.2.0-portable-python.tar.gz",
    )
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
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "devbot 0.2.0\n"
    help_completed = subprocess.run(
        [str(extract_dir / "devbot-release" / "bin" / "devbot"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_completed.returncode == 0, help_completed.stderr
    assert "--once" in help_completed.stdout



def test_release_artifact_contains_real_package_code(tmp_path: Path) -> None:
    artifact = build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")

    with tarfile.open(artifact.path) as archive:
        names = set(archive.getnames())

    assert "devbot-release/lib/devbot/main.py" in names
    assert "devbot-release/lib/devbot/release.py" in names
    assert "devbot-release/pyproject.toml" in names


def test_release_version_override_unifies_runtime_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVBOT_RELEASE_VERSION", "0.2.0")

    assert authoritative_version() == "0.2.0"

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
        build_artifact(tmp_path, version="0.2.0", os_name="portable", architecture="python")
    ]
    expected = expected_artifact_names("0.2.0")

    assert checksum_manifest(artifacts, expected_names=expected) == checksum_manifest(
        reversed(artifacts), expected_names=expected
    )
    with pytest.raises(ReleasePolicyError):
        checksum_manifest(artifacts[:-1], expected_names=expected)


def test_release_note_generation_is_deterministic() -> None:
    assert release_notes(_pr(labels=("release:minor",)), "minor", "0.2.0") == (
        "## devbot 0.2.0\n\n"
        "- minor: #67 Task 032: Automated Release Pipeline\n"
    )


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


def test_release_workflow_matrix_and_manual_dispatch_are_declared() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["build-artifacts"]["strategy"]["matrix"]["include"]

    assert matrix == [
        {"os_name": "portable", "architecture": "python"},
    ]
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
