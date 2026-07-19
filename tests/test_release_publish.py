from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubAPIError, GitHubClient, GitHubNotFoundError
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig
from devbot.release_preparation import MalformedProjectVersionError, VersionSourceMismatchError
from devbot.release_publish import (
    ConflictingTagError,
    DirtyWorktreeError,
    MissingReleaseNotesError,
    PartialPublicationError,
    PublishOutcome,
    ReleaseState,
    StaleMainError,
    TagState,
    preview_release_publish,
    publish_prepared_release,
)

# --------------------------------------------------------------------------
# Fixtures: a real, throwaway local git repo + a real, throwaway local bare
# repo as its `origin` remote. `git tag`/`git push` exercise real git
# behaviour, but nothing here ever touches a real GitHub remote or the
# actual devbot repository - both are deleted with `tmp_path`.
# --------------------------------------------------------------------------


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result


def _init_repo_with_remote(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    local = tmp_path / "local"
    remote = tmp_path / "remote.git"
    local.mkdir()
    _run(local, "init", "-q")
    _run(local, "config", "user.email", "test@example.com")
    _run(local, "config", "user.name", "Test")
    (local / "pyproject.toml").write_text(
        f'[project]\nname = "devbot"\nversion = "{version}"\n', encoding="utf-8"
    )
    (local / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "devbot"\nversion = "{version}"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    _run(local, "add", "-A")
    _run(local, "commit", "-q", "-m", "init")
    _run(local, "branch", "-M", "main")
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _run(local, "remote", "add", "origin", str(remote))
    _run(local, "push", "-q", "origin", "main")
    return local


def _rev_parse(path: Path, ref: str) -> str:
    return _run(path, "rev-parse", ref).stdout.strip()


def _local_tags(path: Path) -> list[str]:
    return [line for line in _run(path, "tag", "-l").stdout.splitlines() if line]


def _repository(local_path: Path) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=local_path, default_branch="main"
    )


def _fake_github_client(refs: dict[str, str], *, release: object | None = None) -> MagicMock:
    client = MagicMock(spec=GitHubClient)

    def get_commit_sha(repository: RepositoryConfig, ref: str) -> str:
        if ref in refs:
            return refs[ref]
        raise GitHubNotFoundError(f"no ref {ref!r}")

    client.get_commit_sha.side_effect = get_commit_sha
    client.get_release_by_tag.return_value = release
    return client


def _fake_release(
    target_commitish: str, *, html_url: str = "https://example.invalid/r"
) -> MagicMock:
    release = MagicMock()
    release.target_commitish = target_commitish
    release.html_url = html_url
    return release


def _fake_write_client(
    *, fail: bool = False, html_url: str = "https://example.invalid/r"
) -> MagicMock:
    client = MagicMock(spec=GitHubWriteClient)
    if fail:
        client.create_release.side_effect = GitHubAPIError("simulated Release API failure")
    else:
        info = MagicMock()
        info.html_url = html_url
        client.create_release.return_value = info
    return client


# --------------------------------------------------------------------------
# Dry-run preview: read-only, no writes
# --------------------------------------------------------------------------


def test_dry_run_preview_performs_no_writes(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})

    preview = preview_release_publish(
        github_client, _repository(local), "notes", local_checkout_path=local
    )

    assert preview.version == "1.2.3"
    assert preview.tag == "v1.2.3"
    assert preview.title == "v1.2.3"
    assert preview.target_sha == main_sha
    assert preview.tag_state is TagState.ABSENT
    assert preview.release_state is ReleaseState.ABSENT
    assert _local_tags(local) == []


# --------------------------------------------------------------------------
# Successful publish
# --------------------------------------------------------------------------


def test_successful_publish_creates_tag_and_release(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})
    write_client = _fake_write_client(html_url="https://example.invalid/releases/tag/v1.2.3")

    result = publish_prepared_release(
        github_client, write_client, _repository(local), "notes", local_checkout_path=local
    )

    assert result.outcome is PublishOutcome.PUBLISHED
    assert result.tag == "v1.2.3"
    assert result.target_sha == main_sha
    assert result.release_url == "https://example.invalid/releases/tag/v1.2.3"
    assert _local_tags(local) == ["v1.2.3"]
    remote_tags = subprocess.run(
        ["git", "ls-remote", "--tags", "origin"], cwd=local, capture_output=True, text=True
    ).stdout
    assert "refs/tags/v1.2.3" in remote_tags
    write_client.create_release.assert_called_once_with(
        _repository(local),
        tag_name="v1.2.3",
        target_commitish=main_sha,
        name="v1.2.3",
        body="notes",
    )


# --------------------------------------------------------------------------
# Version-source problems (reused from release_preparation, not duplicated)
# --------------------------------------------------------------------------


def test_version_source_mismatch_raises_and_creates_no_tag(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path, version="1.2.3")
    (local / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "devbot"\nversion = "1.2.4"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    _run(local, "commit", "-am", "mismatch")
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})

    with pytest.raises(VersionSourceMismatchError):
        preview_release_publish(
            github_client, _repository(local), "notes", local_checkout_path=local
        )

    assert _local_tags(local) == []


def test_malformed_version_raises(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path, version="not-a-version")
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})

    with pytest.raises(MalformedProjectVersionError):
        preview_release_publish(
            github_client, _repository(local), "notes", local_checkout_path=local
        )


# --------------------------------------------------------------------------
# Unsafe local/remote state
# --------------------------------------------------------------------------


def test_dirty_worktree_raises(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    (local / "pyproject.toml").write_text(
        '[project]\nname = "devbot"\nversion = "1.2.3"\n# dirty\n', encoding="utf-8"
    )
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})

    with pytest.raises(DirtyWorktreeError):
        preview_release_publish(
            github_client, _repository(local), "notes", local_checkout_path=local
        )


def test_stale_main_raises(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    # Remote main reports a SHA that disagrees with the local checkout.
    github_client = _fake_github_client({"main": "0" * 40})

    with pytest.raises(StaleMainError):
        preview_release_publish(
            github_client, _repository(local), "notes", local_checkout_path=local
        )


def test_empty_release_notes_raises(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})

    with pytest.raises(MissingReleaseNotesError):
        preview_release_publish(github_client, _repository(local), "   ", local_checkout_path=local)


# --------------------------------------------------------------------------
# Idempotency: matching tag+release, matching tag only, conflicting tag
# --------------------------------------------------------------------------


def test_existing_matching_tag_and_release_is_already_published(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    _run(local, "tag", "-a", "v1.2.3", "-m", "Release v1.2.3", main_sha)
    _run(local, "push", "-q", "origin", "v1.2.3")
    github_client = _fake_github_client(
        {"main": main_sha, "v1.2.3": main_sha}, release=_fake_release(main_sha)
    )
    write_client = _fake_write_client()

    result = publish_prepared_release(
        github_client, write_client, _repository(local), "notes", local_checkout_path=local
    )

    assert result.outcome is PublishOutcome.ALREADY_PUBLISHED
    write_client.create_release.assert_not_called()
    assert _local_tags(local) == ["v1.2.3"]


def test_existing_matching_tag_missing_release_completes_release_only(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    _run(local, "tag", "-a", "v1.2.3", "-m", "Release v1.2.3", main_sha)
    _run(local, "push", "-q", "origin", "v1.2.3")
    github_client = _fake_github_client({"main": main_sha, "v1.2.3": main_sha}, release=None)
    write_client = _fake_write_client()

    result = publish_prepared_release(
        github_client, write_client, _repository(local), "notes", local_checkout_path=local
    )

    assert result.outcome is PublishOutcome.COMPLETED_MISSING_RELEASE
    write_client.create_release.assert_called_once()
    assert _local_tags(local) == ["v1.2.3"]


def test_existing_tag_at_wrong_sha_raises_conflicting_tag_error(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    wrong_sha = _rev_parse(local, "main")
    # Advance main so the "target" is a different commit than the tag.
    (local / "README.md").write_text("more\n", encoding="utf-8")
    _run(local, "add", "-A")
    _run(local, "commit", "-q", "-m", "advance")
    _run(local, "push", "-q", "origin", "main")
    _run(local, "tag", "-a", "v1.2.3", "-m", "Release v1.2.3", wrong_sha)
    _run(local, "push", "-q", "origin", "v1.2.3")
    new_main_sha = _rev_parse(local, "main")
    assert new_main_sha != wrong_sha
    github_client = _fake_github_client({"main": new_main_sha, "v1.2.3": wrong_sha})

    with pytest.raises(ConflictingTagError):
        preview_release_publish(
            github_client, _repository(local), "notes", local_checkout_path=local
        )

    # The tag must never be moved.
    assert _rev_parse(local, "v1.2.3^{commit}") == wrong_sha


# --------------------------------------------------------------------------
# Partial publication and safe retry
# --------------------------------------------------------------------------


def test_release_creation_failure_after_tag_push_raises_partial_publication_error(
    tmp_path: Path,
) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})
    write_client = _fake_write_client(fail=True)

    with pytest.raises(PartialPublicationError) as excinfo:
        publish_prepared_release(
            github_client, write_client, _repository(local), "notes", local_checkout_path=local
        )

    assert excinfo.value.tag == "v1.2.3"
    assert excinfo.value.target_sha == main_sha
    # The tag was pushed and is intentionally NOT rolled back.
    assert _local_tags(local) == ["v1.2.3"]
    remote_tags = subprocess.run(
        ["git", "ls-remote", "--tags", "origin"], cwd=local, capture_output=True, text=True
    ).stdout
    assert "refs/tags/v1.2.3" in remote_tags


def test_retry_after_partial_publication_completes_safely_without_moving_tag(
    tmp_path: Path,
) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    failing_github_client = _fake_github_client({"main": main_sha})
    failing_write_client = _fake_write_client(fail=True)
    with pytest.raises(PartialPublicationError):
        publish_prepared_release(
            failing_github_client,
            failing_write_client,
            _repository(local),
            "notes",
            local_checkout_path=local,
        )
    tag_sha_after_failure = _rev_parse(local, "v1.2.3^{commit}")

    working_github_client = _fake_github_client({"main": main_sha, "v1.2.3": main_sha})
    working_write_client = _fake_write_client()
    result = publish_prepared_release(
        working_github_client,
        working_write_client,
        _repository(local),
        "notes",
        local_checkout_path=local,
    )

    assert result.outcome is PublishOutcome.COMPLETED_MISSING_RELEASE
    working_write_client.create_release.assert_called_once()
    assert _local_tags(local) == ["v1.2.3"]
    assert _rev_parse(local, "v1.2.3^{commit}") == tag_sha_after_failure


# --------------------------------------------------------------------------
# Version files are never touched; no force git operation is ever used
# --------------------------------------------------------------------------


def test_pyproject_and_uv_lock_remain_unchanged(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    pyproject_before = (local / "pyproject.toml").read_text(encoding="utf-8")
    uv_lock_before = (local / "uv.lock").read_text(encoding="utf-8")
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})
    write_client = _fake_write_client()

    publish_prepared_release(
        github_client, write_client, _repository(local), "notes", local_checkout_path=local
    )

    assert (local / "pyproject.toml").read_text(encoding="utf-8") == pyproject_before
    assert (local / "uv.lock").read_text(encoding="utf-8") == uv_lock_before


def test_no_force_flag_used_in_any_git_call(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path)
    main_sha = _rev_parse(local, "main")
    github_client = _fake_github_client({"main": main_sha})
    write_client = _fake_write_client()
    recorded_calls: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded_calls.append(list(args))
        return real_run(args, **kwargs)  # type: ignore[arg-type]

    import devbot.release_publish as release_publish_module

    original = release_publish_module.subprocess.run
    release_publish_module.subprocess.run = recording_run  # type: ignore[assignment]
    try:
        publish_prepared_release(
            github_client, write_client, _repository(local), "notes", local_checkout_path=local
        )
    finally:
        release_publish_module.subprocess.run = original

    git_calls = [call for call in recorded_calls if call and call[0] == "git"]
    assert git_calls, "expected at least one git subprocess call"
    for call in git_calls:
        assert "-f" not in call
        assert "--force" not in call


def test_source_never_contains_a_force_git_flag() -> None:
    source = Path("src/devbot/release_publish.py").read_text(encoding="utf-8")
    assert "--force" not in source
    assert '"-f"' not in source
