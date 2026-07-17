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
from importlib.metadata import version as package_version
from pathlib import Path

from devbot.agents import build_agent_runner
from devbot.agents.base import AgentRunner, AgentSessionLimitError, is_session_limit_output
from devbot.config import ConfigError, load_config
from devbot.delivery import DeliveryService
from devbot.doctor import build_doctor_report, render_doctor_report
from devbot.github_client import GitHubClient, GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.models import DevBotConfig, IssueComment, RepositoryConfig
from devbot.observability import (
    LOG_LEVELS,
    install_secret_filter,
    log_startup,
    log_startup_validation,
)
from devbot.polling import PollingService, PollingStatus, run_forever
from devbot.review import ReviewService
from devbot.rework import ReworkService
from devbot.startup import run_startup_checks
from devbot.timeline import TimelineError, TimelineService
from devbot.workspace import build_agent_prompt
from devbot.worktree import WorkspacePreparationError, WorktreeManager

_LOGGER_NAME = "devbot"

_FAILURE_STATUSES = {
    PollingStatus.WORKSPACE_INVALID,
    PollingStatus.WORKSPACE_PREPARATION_FAILED,
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
        if is_session_limit_output(message):
            # Task 019 CP-019-9: a dedicated exception type lets
            # `ReworkService.process()` (`devbot.rework`) classify this
            # distinctly (a clear recovery hint) while still being caught by
            # its existing generic `except (Exception, KeyboardInterrupt)`.
            raise AgentSessionLimitError(message)
        raise RuntimeError(message)


def _add_timeline_common_args(sub_parser: argparse.ArgumentParser) -> None:
    sub_parser.add_argument("--issue", type=int, required=True, help="대상 GitHub Issue 번호.")
    sub_parser.add_argument(
        "--repo",
        default=None,
        help="owner/repo 형식. 생략하면 config/repositories.yaml의 단일 enabled 저장소를 씁니다.",
    )


def _add_timeline_write_args(sub_parser: argparse.ArgumentParser) -> None:
    """`start`/`end`에만 붙는 자체 `--dry-run`. `devbot`(daemon) 최상위의
    `--dry-run`/`DRY_RUN` 환경 변수와는 별개다 - `timeline start/end`는
    사람이 그 순간 명시적으로 실행한 1회성 기록 커맨드이므로 daemon의
    "기본은 안전하게 dry-run" 정책을 그대로 물려받으면 계약(Task 018
    Goal/Scope 2, CP-018-2/3/4)이 요구하는 "실행하면 실제로 GitHub에
    기록한다"를 기본 배포 설정에서 지키지 못한다. 미리보기가 필요하면
    이 플래그로 명시적으로 opt-in한다."""
    sub_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제로 GitHub에 쓰지 않고 계산된 Status Card만 출력합니다.",
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
    _add_timeline_write_args(start_parser)

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
    _add_timeline_write_args(end_parser)

    status_parser = timeline_subparsers.add_parser("status", help="Status Card를 조회합니다.")
    _add_timeline_common_args(status_parser)


def _build_worktree_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 023 Scope §8: an explicit operator command for Job worktree
    lifecycle management - `devbot worktree` never runs automatically as
    part of a polling cycle."""
    worktree_parser = subparsers.add_parser(
        "worktree", help="DevBot가 준비한 격리 Job worktree를 조회/정리합니다."
    )
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command", required=True)

    status_parser = worktree_subparsers.add_parser(
        "status", help="worktree 상태(active/stale/conflicting)를 조회합니다 (읽기 전용)."
    )
    status_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )

    cleanup_parser = worktree_subparsers.add_parser(
        "cleanup", help="지정한 Issue의 worktree를 명시적으로 제거합니다."
    )
    cleanup_parser.add_argument("--issue", type=int, required=True, help="대상 GitHub Issue 번호.")
    cleanup_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )
    cleanup_parser.add_argument(
        "--force",
        action="store_true",
        default=True,
        help="미커밋 변경이 있어도 강제로 제거합니다 (기본값).",
    )


def _run_worktree_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    manager = WorktreeManager(workspace_root=config.workspace_root)

    if args.worktree_command == "status":
        report = manager.health(repository)
        print(f"operator_checkout: {report.operator_checkout_path}")
        print(f"operator_branch: {report.operator_branch or 'unknown'}")
        print(f"worktree_root: {report.worktree_root}")
        print(f"active ({len(report.active)}):")
        for path in report.active:
            print(f"  - {path}")
        print(f"stale ({len(report.stale)}):")
        for path in report.stale:
            print(f"  - {path}")
        print(f"conflicting ({len(report.conflicting)}):")
        for path in report.conflicting:
            print(f"  - {path}")
        print(f"safe_to_start: {'yes' if report.safe_to_start else 'no'}")
        return 0

    try:
        manager.cleanup(repository, args.issue, force=args.force)
    except WorkspacePreparationError as exc:
        print(f"worktree cleanup 오류: [{exc.category.value}] {exc}", file=sys.stderr)
        return 1
    print(f"worktree 제거 완료: issue #{args.issue}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="devbot")
    parser.add_argument(
        "--version",
        action="store_true",
        help="DevBot 패키지 버전을 출력하고 종료합니다.",
    )
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
    _build_worktree_parser(subparsers)
    subparsers.add_parser(
        "doctor",
        help=(
            "설정/워크스페이스/GitHub 연결/Lock 상태를 점검하고 데몬 시작 가능 여부를 "
            "보고합니다 (읽기 전용, GitHub에 쓰지 않음)."
        ),
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

    # `getattr(..., False)`: only `start`/`end` define their own `--dry-run`
    # (`status` never writes, so it has none). Deliberately *not*
    # `config.dry_run` - see `_add_timeline_write_args`'s docstring for why
    # this command must default to a real write regardless of the
    # deployment's global `DRY_RUN` value.
    service = TimelineService(
        read_client=GitHubClient(config.github_token),
        write_client=GitHubWriteClient(config.github_token),
        dry_run=getattr(args, "dry_run", False),
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


def _run_doctor_command(config: DevBotConfig) -> int:
    """`devbot doctor` (Task 019 CP-019-5): read-only, never acquires the
    daemon lock (`devbot.doctor.check_daemon_lock` only probes it), so it
    is safe to run alongside an already-running DevBot process."""
    report = build_doctor_report(config)
    print(render_doctor_report(report))
    return 0 if report.safe_to_start else 1


def main(
    argv: Sequence[str] | None = None,
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
) -> int:
    args = _parse_args(argv)
    if args.version:
        print(f"devbot {package_version('devbot')}")
        return 0

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

    if args.command == "worktree":
        return _run_worktree_command(args, config)

    if args.command == "doctor":
        return _run_doctor_command(config)

    try:
        with ProcessLock(config.lock_file):
            log_startup(logger, config)
            # Task 019 CP-019-4: informational only (see
            # `devbot.startup`'s module docstring) - the two genuinely
            # fatal startup conditions (bad config, a duplicate daemon
            # instance) already stopped `main()` above this point via
            # `ConfigError`/`LockAcquisitionError`, before any GitHub
            # write client exists (CP-019-8).
            log_startup_validation(logger, run_startup_checks(config))
            write_client = GitHubWriteClient(config.github_token)
            github_client = GitHubClient(config.github_token)
            implementer_runner = build_agent_runner(
                config.implementer_agent, dry_run=config.dry_run
            )
            reviewer_runner = build_agent_runner(config.reviewer_agent, dry_run=config.dry_run)
            state_writer = IssueStateWriter(
                client=write_client, dry_run=config.dry_run, logger=logger
            )
            # Task 023: host-managed workspace preparation - every IMPLEMENT/
            # REWORK Job runs in its own isolated Git worktree under
            # `config.workspace_root`, resolved and synchronized by DevBot
            # itself before the Agent ever runs (see `devbot.worktree`).
            worktree_manager = WorktreeManager(workspace_root=config.workspace_root)
            # Task 024: automatic Timeline recording. `dry_run=config.dry_run`
            # (not the manual `timeline` CLI's own `False` default - see
            # `_run_timeline_command` above) so automatic writes obey the
            # same global safety switch as every other daemon write path.
            timeline_service = TimelineService(
                read_client=github_client,
                write_client=write_client,
                dry_run=config.dry_run,
            )
            polling_service = PollingService(
                config=config,
                github_client=github_client,
                implementer_runner=implementer_runner,
                reviewer_runner=reviewer_runner,
                prepare_workspace=worktree_manager.prepare,
                validate_review_integration=worktree_manager.validate_review_integration,
                state_writer=state_writer,
                delivery=DeliveryService(client=write_client, dry_run=config.dry_run),
                rework_service=ReworkService(
                    state_writer=state_writer,
                    write_client=write_client,
                    apply_changes=lambda repository, issue, comment: _apply_rework_changes(
                        implementer_runner, repository, issue, comment
                    ),
                    dry_run=config.dry_run,
                    timeline=timeline_service,
                    actor=config.implementer_agent,
                    logger=logger,
                ),
                review_service=ReviewService(
                    state_writer=state_writer,
                    write_client=write_client,
                    reviewer_runner=reviewer_runner,
                    dry_run=config.dry_run,
                    timeline=timeline_service,
                    actor=config.reviewer_agent,
                    logger=logger,
                    current_head_sha=lambda repository, pull_request: next(
                        candidate.head_sha
                        for candidate in github_client.list_pull_requests(repository)
                        if candidate.number == pull_request.number
                    ),
                ),
                timeline=timeline_service,
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
