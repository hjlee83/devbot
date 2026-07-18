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
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from devbot.agent_execution import AgentExecutionContext
from devbot.agent_outcome import AgentOutcomeError, classify_agent_outcome
from devbot.agents import build_agent_runner
from devbot.agents.base import AgentRunner, AgentSessionLimitError
from devbot.config import ConfigError, load_config
from devbot.delivery import DeliveryService
from devbot.doctor import build_doctor_report, render_doctor_report
from devbot.github_client import GitHubClient, GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.models import AgentOutcome, DevBotConfig, IssueComment, RepositoryConfig
from devbot.observability import (
    LOG_LEVELS,
    install_secret_filter,
    log_startup,
    log_startup_validation,
)
from devbot.polling import PollingService, PollingStatus, run_forever
from devbot.release import authoritative_version
from devbot.review import ReviewService
from devbot.rework import ReworkService
from devbot.startup import (
    STARTUP_SELF_UPDATE_ENV,
    StartupSelfUpdateError,
    run_startup_checks,
    run_startup_self_update,
)
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
    execution_context: AgentExecutionContext | None = None,
) -> None:
    prompt = build_agent_prompt(
        repository,
        issue,
        [IssueComment(author=comment.author, body=comment.body)],
    )
    if execution_context is not None:
        result = implementer_runner.run_context(execution_context, prompt)
    else:
        result = implementer_runner.run(repository, prompt)

    # CP-B0: classify via the same `classify_agent_outcome()` the initial
    # IMPLEMENT job uses (`devbot.polling`), instead of only checking
    # `result.failed`. This closes an Issue #41-class false-success gap on
    # the rework path specifically: an Agent that exits 0 but whose own
    # output says it needs approval (or is network-blocked, etc.) was
    # previously treated as a successful rework with nothing having changed.
    classification = classify_agent_outcome(result)
    if classification.outcome is AgentOutcome.IMPLEMENTATION_COMPLETED:
        return
    message = result.message or f"AgentRunner exited with code {result.returncode}"
    if classification.outcome is AgentOutcome.SESSION_LIMIT:
        # Task 019 CP-019-9: a dedicated exception type lets
        # `ReworkService.process()` (`devbot.rework`) classify this
        # distinctly (a clear recovery hint) while still being caught by
        # its existing generic `except (Exception, KeyboardInterrupt)`.
        raise AgentSessionLimitError(message)
    raise AgentOutcomeError(classification, message)


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
    doctor_parser = subparsers.add_parser(
        "doctor",
        help=(
            "설정/워크스페이스/GitHub 연결/Lock 상태를 점검하고 데몬 시작 가능 여부를 "
            "보고합니다 (읽기 전용, GitHub에 쓰지 않음)."
        ),
    )
    doctor_parser.add_argument(
        "--ci",
        action="store_true",
        help="CI 검증용으로 Agent 실행 파일/사용자 로그인 검사를 생략합니다.",
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


def _run_doctor_command(config: DevBotConfig, *, ci: bool = False) -> int:
    """`devbot doctor` (Task 019 CP-019-5): read-only, never acquires the
    daemon lock (`devbot.doctor.check_daemon_lock` only probes it), so it
    is safe to run alongside an already-running DevBot process."""
    report = build_doctor_report(config, ci=ci)
    print(render_doctor_report(report))
    return 0 if report.safe_to_start else 1


def _sweep_stuck_working_issues(
    config: DevBotConfig,
    github_client: GitHubClient,
    state_writer: IssueStateWriter,
    logger: logging.Logger,
) -> None:
    """CP-B1: successfully acquiring `ProcessLock` (the caller's `with`
    block) guarantees no other process sharing this deployment's lock file
    can be mid-job right now - so any Issue still labeled `devbot:working`
    at this exact moment must be a crash remnant (the kernel releases
    `flock` on an unclean exit, but nothing else ever re-evaluates the
    label). Left alone, `polling.PollingService._collect_job_candidates`
    treats ANY `devbot:working` Issue as reason to exclude every other
    Issue in that repository too (`REPOSITORY_BUSY`), stalling the whole
    repository's queue indefinitely. Must live here, inside the daemon's
    `ProcessLock` block - not in `devbot.startup`, which `devbot doctor`
    also calls without ever holding the lock.
    """
    for repository in config.enabled_repositories:
        try:
            stuck = github_client.list_issues(
                repository, state="open", labels=["devbot:working"]
            )
        except Exception as exc:  # noqa: BLE001 - must never abort startup
            logger.error(
                "stuck-working sweep: 이슈 조회 실패 (%s): %s", repository.full_name, exc
            )
            continue
        for issue in stuck:
            try:
                state_writer.block(
                    repository,
                    issue,
                    "daemon 재시작 사이 devbot:working 상태로 남아있었습니다 "
                    "(이전 프로세스 크래시로 추정). 운영자 검토 후 devbot:ready로 "
                    "되돌리세요.",
                    job_type=None,
                )
            except Exception as exc:  # noqa: BLE001 - one failure must not abort the sweep
                logger.error(
                    "stuck-working sweep: %s #%d block 실패: %s",
                    repository.full_name,
                    issue.number,
                    exc,
                )


def _restart_after_startup_update(final_sha: str) -> None:
    if os.environ.get(STARTUP_SELF_UPDATE_ENV) == final_sha:
        return
    env = os.environ.copy()
    env[STARTUP_SELF_UPDATE_ENV] = final_sha
    os.execvpe(sys.executable, [sys.executable, *sys.argv], env)


def _run_startup_self_update(
    config: DevBotConfig, logger: logging.Logger, *, allow_dirty_skip: bool = False
) -> bool:
    try:
        results = run_startup_self_update(config)
    except StartupSelfUpdateError as exc:
        result = exc.result
        # CP-B0-1: only a dirty *operator checkout* is safe to treat as
        # non-fatal, and only when the caller opted in (daemon/--once under
        # --dry-run) - a developer's uncommitted local edits shouldn't stop
        # a supervised, no-real-effects run. Every other reason_code (wrong
        # branch, fetch/switch/pull failure, ...) stays fatal regardless,
        # since those indicate a more serious problem an operator should see
        # immediately rather than silently run past.
        if allow_dirty_skip and result.reason_code == "dirty_checkout":
            logger.warning(
                "startup self-update skipped: dirty operator checkout allowed under "
                "dry-run (repository=%s current_sha=%s skip_reason=%s)",
                result.repository,
                result.current_sha,
                result.skip_reason,
            )
            return True
        logger.error(
            "startup self-update failed: repository=%s current_sha=%s latest_sha=%s "
            "final_sha=%s result=%s skip_reason=%s",
            result.repository,
            result.current_sha,
            result.latest_sha,
            result.final_sha,
            result.result,
            result.skip_reason,
        )
        return False
    for result in results:
        logger.info(
            "startup self-update: repository=%s current_sha=%s latest_sha=%s "
            "final_sha=%s result=%s skip_reason=%s",
            result.repository,
            result.current_sha,
            result.latest_sha,
            result.final_sha,
            result.result,
            result.skip_reason,
        )
        if result.current_sha != result.final_sha:
            _restart_after_startup_update(result.final_sha)
    return True


def main(
    argv: Sequence[str] | None = None,
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
) -> int:
    args = _parse_args(argv)
    if args.version:
        print(f"devbot {authoritative_version()}")
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
        if not args.ci and not _run_startup_self_update(config, logger):
            return 1
        return _run_doctor_command(config, ci=args.ci)

    try:
        with ProcessLock(config.lock_file):
            log_startup(logger, config)
            if config.enabled_repositories and not _run_startup_self_update(
                config, logger, allow_dirty_skip=config.dry_run
            ):
                return 1
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
            _sweep_stuck_working_issues(config, github_client, state_writer, logger)
            # Task 023: host-managed workspace preparation - every IMPLEMENT/
            # REWORK Job runs in its own isolated Git worktree under
            # `config.workspace_root`, resolved and synchronized by DevBot
            # itself before the Agent ever runs (see `devbot.worktree`).
            worktree_manager = WorktreeManager(
                workspace_root=config.workspace_root, dry_run=config.dry_run
            )
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
                    review_loop_limit=config.review_loop_limit,
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
