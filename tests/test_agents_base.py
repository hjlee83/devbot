from devbot.agents.base import AgentRunResult


def test_dry_run_no_op_is_not_failed() -> None:
    result = AgentRunResult(executed=False, dry_run=True, message="[dry-run] would run")
    assert result.failed is False


def test_successful_execution_is_not_failed() -> None:
    result = AgentRunResult(executed=True, dry_run=False, message="ok", returncode=0)
    assert result.failed is False


def test_nonzero_returncode_is_failed() -> None:
    result = AgentRunResult(executed=True, dry_run=False, message="boom", returncode=1)
    assert result.failed is True


def test_unexecuted_non_dry_run_result_is_failed() -> None:
    """Regression: `ClaudeRunner` reports a missing CLI or a timeout as
    `executed=False, dry_run=False, returncode=None` (see
    `devbot.agents.claude`) - that must not be mistaken for the dry-run
    no-op case just because `returncode` happens to be `None` in both."""
    result = AgentRunResult(
        executed=False, dry_run=False, message="Claude CLI가 설치되어 있지 않습니다."
    )
    assert result.failed is True
