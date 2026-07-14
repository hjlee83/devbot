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
from devbot.models import DevBotConfig, IssueComment, RepositoryConfig
from devbot.observability import LOG_LEVELS, install_secret_filter, log_startup
from devbot.polling import PollingService, PollingStatus, run_forever
from devbot.review import ReviewService
from devbot.rework import ReworkService
from devbot.timeline import TimelineError, TimelineService
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
    if result.failed:
        message = result.message or f"AgentRunner exited with code {result.returncode}"
        raise RuntimeError(message)


def _add_timeline_common_args(sub_parser: argparse.ArgumentParser) -> None:
    sub_parser.add_argument("--issue", type=int, required=True, help="대상 GitHub Issue 번호.")
    sub_parser.add_argument(
        "--repo",
        default=None,
        help="owner/repo 형식. 생략하면 config/repositories.yaml의 단일 enabled 저장소를 씁니다.",
    )


def _build_timeline_parser(subparsers: argparse._SubParsersAction) -> None:
    timeline_parser = subparsers.add_parser(
        "timeline", help="GitHub Status Timeline(Task 017 프로토콜)을 수동으로 기록/조회합니다."
    )
    timeline_subparsers = timeline_parser.add_subparsers(dest="timeline_command", required=True)

    start_parser = timeline_subparsers.add_parser("start", help="phase 시작 이벤트를 기록합니다.")
    _add_timeline_common_args(start_parser)
    start_parser.add_argument("--phase", choices=["dev", "review"], required=True)
    start_parser.add_argument("--actor", required=True, help="이 phase를 시작한 Agent/사람 식별자.")
    start_parser.add_argument("--pr", type=int, default=None)

    end_parser = timeline_subparsers.add_parser("end", help="phase 종료 이벤트를 기록합니다.")
    _add_timeline_common_args(end_parser)
    end_parser.add_argument("--phase", choices=["dev", "review"], required=True)
    end_parser.add_argument("--actor", required=True, help="이 phase를 종료한 Agent/사람 식별자.")
    end_parser.add_argument(
        "--result",
        required=True,
        help="예: pushed, manual-action, blocked, merge-ready, request-changes, done",
    )
    end_parser.add_argument("--pr", type=int, default=None)

    status_parser = timeline_subparsers.add_parser("status", help="Status Card를 조회합니다.")
    _add_timeline_common_args(status_parser)


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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="LOG_LEVEL 설정과 무관하게 이번 실행에서만 DEBUG 수준 로그를 켭니다.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _build_timeline_parser(subparsers)
    return parser.parse_args(argv)


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _apply_log_level(logger: logging.Logger, config_log_level: str, *, verbose: bool) -> None:
    """`--verbose` overrides `LOG_LEVEL` to DEBUG for this process only - it
    never writes back to `.env` or the process environment (Task 013 동작
    규칙 #7)."""
    level = logging.DEBUG if verbose else LOG_LEVELS[config_log_level]
    logger.setLevel(level)


def _resolve_repository(config: DevBotConfig, repo_arg: str | None) -> RepositoryConfig:
    """Pick the `RepositoryConfig` a `timeline` command targets: an
    explicit `--repo owner/repo`, or the sole enabled repository when there
    is exactly one (every current deployment - `config/repositories.yaml`
    - manages a single repository)."""
    if repo_arg is not None:
        owner, separator, repo = repo_arg.partition("/")
        if not separator:
            raise ConfigError(f"--repo는 owner/repo 형식이어야 합니다: {repo_arg!r}")
        for candidate in config.repositories:
            if candidate.owner == owner and candidate.repo == repo:
                return candidate
        raise ConfigError(f"config/repositories.yaml에 없는 저장소입니다: {repo_arg!r}")

    enabled = config.enabled_repositories
    if len(enabled) != 1:
        raise ConfigError(
            "timeline 명령은 대상 저장소를 하나로 특정할 수 없습니다 "
            f"(enabled repositories: {len(enabled)}개). --repo owner/repo를 지정하세요."
        )
    return enabled[0]


def _run_timeline_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    service = TimelineService(
        read_client=GitHubClient(config.github_token),
        write_client=GitHubWriteClient(config.github_token),
        dry_run=config.dry_run,
    )

    try:
        if args.timeline_command == "start":
            outcome = service.start(
                repository, args.issue, phase=args.phase, actor=args.actor, pr=args.pr
            )
        elif args.timeline_command == "end":
            outcome = service.end(
                repository,
                args.issue,
                phase=args.phase,
                actor=args.actor,
                result=args.result,
                pr=args.pr,
            )
        else:
            outcome = service.status(repository, args.issue)
    except TimelineError as exc:
        print(f"timeline 오류: {exc}", file=sys.stderr)
        return 1

    print(outcome.status_card)
    return 0


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

    _apply_log_level(logger, config.log_level, verbose=args.verbose)
    install_secret_filter(logger, [config.github_token])

    if args.command == "timeline":
        return _run_timeline_command(args, config)

    try:
        with ProcessLock(config.lock_file):
            log_startup(logger, config)
            write_client = GitHubWriteClient(config.github_token)
            implementer_runner = build_agent_runner(
                config.implementer_agent, dry_run=config.dry_run
            )
            reviewer_runner = build_agent_runner(config.reviewer_agent, dry_run=config.dry_run)
            state_writer = IssueStateWriter(
                client=write_client, dry_run=config.dry_run, logger=logger
            )
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
                review_service=ReviewService(
                    state_writer=state_writer,
                    write_client=write_client,
                    reviewer_runner=reviewer_runner,
                    dry_run=config.dry_run,
                ),
                logger=logger,
            )

            if args.once:
                results = polling_service.run_cycle()
                for result in results:
                    logger.info("1회 실행 완료: %s", result.status.value)
                return 1 if any(result.status in _FAILURE_STATUSES for result in results) else 0

            run_forever(polling_service, config.poll_interval_seconds, logger=logger)
            return 0
    except LockAcquisitionError as exc:
        print(f"락 오류: {exc}", file=sys.stderr)
        return 1


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
