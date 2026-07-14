"""DevBot CLI entry point.

`uv run devbot --once` loads configuration, acquires the single-process
lock, runs exactly one polling iteration, prints a summary, and exits with
a status-appropriate code.

`uv run devbot` (no flags) runs the same iteration continuously on
`POLL_INTERVAL_SECONDS`, until SIGINT/SIGTERM requests a safe shutdown.

`--dry-run` forces dry-run regardless of the `DRY_RUN` environment
variable, so a smoke test never depends on how the deployment's `.env` is
configured.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from devbot.agents import build_agent_runner
from devbot.agents.base import AgentRunner
from devbot.config import ConfigError, load_config
from devbot.delivery import DeliveryService
from devbot.github_client import GitHubClient, GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.models import IssueComment, RepositoryConfig
from devbot.polling import PollingService, PollingStatus, run_forever
from devbot.rework import ReworkService
from devbot.workspace import build_agent_prompt

_LOGGER_NAME = "devbot"

_FAILURE_STATUSES = {
    PollingStatus.WORKSPACE_INVALID,
    PollingStatus.AGENT_FAILED,
    PollingStatus.BLOCKED,
    PollingStatus.ITERATION_ERROR,
}


def _apply_rework_changes(
    implementer_runner: AgentRunner,
    repository: RepositoryConfig,
    issue: GitHubIssue,
    comment: PullRequestComment,
) -> None:
    prompt = build_agent_prompt(
        repository,
        issue,
        [IssueComment(author=comment.author, body=comment.body)],
    )
    result = implementer_runner.run(repository, prompt)
    if result.returncode not in (None, 0):
        message = result.message or f"AgentRunner exited with code {result.returncode}"
        raise RuntimeError(message)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="devbot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="한 번만 폴링하고 종료합니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DRY_RUN 환경 변수 값과 무관하게 강제로 dry-run으로 실행합니다.",
    )
    return parser.parse_args(argv)


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def main(
    argv: Sequence[str] | None = None,
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
) -> int:
    args = _parse_args(argv)
    logger = _configure_logging()

    try:
        config = load_config(env_path=env_path, repositories_path=repositories_path)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    if args.dry_run and not config.dry_run:
        config = replace(config, dry_run=True)

    try:
        with ProcessLock(config.lock_file):
            logger.info(
                "실행 구성: implementer=%s reviewer=%s dry_run=%s",
                config.implementer_agent,
                config.reviewer_agent,
                config.dry_run,
            )
            write_client = GitHubWriteClient(config.github_token)
            implementer_runner = build_agent_runner(
                config.implementer_agent, dry_run=config.dry_run
            )
            reviewer_runner = build_agent_runner(config.reviewer_agent, dry_run=config.dry_run)
            state_writer = IssueStateWriter(client=write_client, dry_run=config.dry_run)
            polling_service = PollingService(
                config=config,
                github_client=GitHubClient(config.github_token),
                implementer_runner=implementer_runner,
                reviewer_runner=reviewer_runner,
                state_writer=state_writer,
                delivery=DeliveryService(client=write_client, dry_run=config.dry_run),
                rework_service=ReworkService(
                    state_writer=state_writer,
                    write_client=write_client,
                    apply_changes=lambda repository, issue, comment: _apply_rework_changes(
                        implementer_runner, repository, issue, comment
                    ),
                    dry_run=config.dry_run,
                ),
                logger=logger,
            )

            if args.once:
                result = polling_service.run_once()
                logger.info("1회 실행 완료: %s", result.status.value)
                return 1 if result.status in _FAILURE_STATUSES else 0

            run_forever(polling_service, config.poll_interval_seconds, logger=logger)
            return 0
    except LockAcquisitionError as exc:
        print(f"락 오류: {exc}", file=sys.stderr)
        return 1


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
