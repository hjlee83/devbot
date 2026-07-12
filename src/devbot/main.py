"""DevBot CLI entry point.

`uv run devbot --once` loads configuration, acquires the single-process
lock, runs exactly one polling iteration, prints a summary, and exits with
a status-appropriate code.

`uv run devbot` (no flags) runs the same iteration continuously on
`POLL_INTERVAL_SECONDS`, until SIGINT/SIGTERM requests a safe shutdown.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from devbot.agents.codex import CodexRunner
from devbot.config import ConfigError, load_config
from devbot.github_client import GitHubClient
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.polling import PollingService, PollingStatus, run_forever

_LOGGER_NAME = "devbot"

_FAILURE_STATUSES = {
    PollingStatus.WORKSPACE_INVALID,
    PollingStatus.AGENT_FAILED,
    PollingStatus.ITERATION_ERROR,
}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="devbot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="한 번만 폴링하고 종료합니다.",
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

    try:
        with ProcessLock(config.lock_file):
            polling_service = PollingService(
                config=config,
                github_client=GitHubClient(config.github_token),
                agent_runner=CodexRunner(dry_run=config.dry_run),
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
