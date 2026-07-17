"""Task 018: Timeline CLI - required Checkpoint tests (CP-018-1..11).

CP-018-12 (Result/문서 정합성) has no dedicated unit test - it is verified by
reviewers reading `results/018-timeline-cli.md` against this diff, matching
`docs/09-task-contract-standard.md`'s own PR Evidence requirement.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.github_client import GitHubClient
from devbot.github_retry import GitHubTransientError
from devbot.github_write_client import GitHubWriteClient
from devbot.main import _parse_args, main
from devbot.models import RepositoryConfig
from devbot.timeline import (
    COMMENT_MARKER,
    TimelineMissingStartError,
    TimelineOutcome,
    TimelineOverlappingStartError,
    TimelineService,
    parse_events,
    safe_end,
    safe_ready,
    safe_start,
)


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=Path("/tmp/workspace/myrepo")
    )


def _mock_response(*, status_code: int = 200, json_data: object = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data
    response.text = ""
    return response


class _FakeGitHubBackend:
    """A minimal in-memory stand-in for the GitHub Issue + its comments,
    wired into `GitHubClient`/`GitHubWriteClient` via a mocked `requests`
    session so a sequence of `TimelineService` calls sees the same state a
    real GitHub Issue would across `start`/`end`/`status` calls."""

    def __init__(self, *, issue_number: int, labels: list[str]) -> None:
        self.issue_number = issue_number
        self.labels = labels
        self.comments: dict[int, str] = {}
        self.patch_should_fail = False
        self._next_id = 1000

    def _issue_payload(self) -> dict:
        return {
            "number": self.issue_number,
            "title": "타임라인 테스트 Issue",
            "state": "open",
            "labels": [{"name": name} for name in self.labels],
            "created_at": "2026-01-01T00:00:00Z",
            "body": "",
        }

    def _comments_payload(self) -> list[dict]:
        return [
            {
                "id": comment_id,
                "user": {"login": "devbot"},
                "body": body,
                "created_at": "2026-01-01T00:00:00Z",
                "reactions": {"url": "-", "total_count": 0},
            }
            for comment_id, body in self.comments.items()
        ]

    def seed_comment(self, body: str) -> int:
        comment_id = self._next_id
        self._next_id += 1
        self.comments[comment_id] = body
        return comment_id

    def build_clients(self) -> tuple[GitHubClient, GitHubWriteClient]:
        def fake_get(url: str, headers=None, params=None, timeout=None):
            if url.endswith(f"/issues/{self.issue_number}/comments"):
                return _mock_response(json_data=self._comments_payload())
            if url.endswith(f"/issues/{self.issue_number}"):
                return _mock_response(json_data=self._issue_payload())
            raise AssertionError(f"unexpected GET {url}")

        read_session = MagicMock()
        read_session.get.side_effect = fake_get
        read_client = GitHubClient("token123", session=read_session)

        def fake_post(url: str, headers=None, json=None, timeout=None):
            if url.endswith("/comments"):
                comment_id = self._next_id
                self._next_id += 1
                self.comments[comment_id] = json["body"]
                return _mock_response(json_data={"id": comment_id})
            raise AssertionError(f"unexpected POST {url}")

        def fake_patch(url: str, headers=None, json=None, timeout=None):
            comment_id = int(url.rsplit("/", 1)[-1])
            if self.patch_should_fail:
                return _mock_response(status_code=500, json_data={"message": "boom"})
            self.comments[comment_id] = json["body"]
            return _mock_response(json_data={})

        write_session = MagicMock()
        write_session.post.side_effect = fake_post
        write_session.patch.side_effect = fake_patch
        write_client = GitHubWriteClient("token123", session=write_session)

        return read_client, write_client, read_session, write_session


def _service(backend: _FakeGitHubBackend, *, clock: datetime, dry_run: bool = False):
    read_client, write_client, read_session, write_session = backend.build_clients()
    service = TimelineService(
        read_client=read_client,
        write_client=write_client,
        dry_run=dry_run,
        clock=lambda: clock,
    )
    return service, read_session, write_session


# --- CP-018-1 ---------------------------------------------------------


def test_timeline_cli_parses_start_end_status() -> None:
    start_args = _parse_args(
        ["timeline", "start", "--issue", "34", "--phase", "dev", "--actor", "claude"]
    )
    assert start_args.command == "timeline"
    assert start_args.timeline_command == "start"
    assert start_args.issue == 34
    assert start_args.phase == "dev"
    assert start_args.actor == "claude"
    assert start_args.pr is None

    end_args = _parse_args(
        [
            "timeline",
            "end",
            "--issue",
            "34",
            "--phase",
            "dev",
            "--actor",
            "claude",
            "--result",
            "pushed",
            "--pr",
            "35",
        ]
    )
    assert end_args.timeline_command == "end"
    assert end_args.result == "pushed"
    assert end_args.pr == 35

    status_args = _parse_args(["timeline", "status", "--issue", "34"])
    assert status_args.timeline_command == "status"
    assert status_args.issue == 34


# --- CP-018-2 -----------------------------------------------------------


def test_timeline_start_creates_single_comment_when_missing() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 15, 9, 30, tzinfo=UTC)
    )

    outcome = service.start(_repository(), 34, phase="dev", actor="claude")

    assert not outcome.idempotent
    assert len(backend.comments) == 1
    write_session.post.assert_called_once()
    write_session.patch.assert_not_called()
    (body,) = backend.comments.values()
    assert COMMENT_MARKER in body
    events = parse_events(body)
    assert len(events) == 1
    assert events[0].phase == "dev"
    assert events[0].event == "start"
    assert events[0].actor == "claude"


# --- CP-018-3 -----------------------------------------------------------


def test_timeline_start_updates_existing_comment() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 15, 10, 25, tzinfo=UTC)
    )
    # Pre-existing comment: cycle 1 dev already completed.
    seed_events = (
        "<!-- devbot-timeline:v1 issue=34 pr=- cycle=1 phase=dev event=start "
        "result=- actor=claude at=2026-07-15T09:30:00Z -->\n"
        "<!-- devbot-timeline:v1 issue=34 pr=35 cycle=1 phase=dev event=end "
        "result=pushed actor=claude at=2026-07-15T10:10:00Z -->"
    )
    comment_id = backend.seed_comment(f"{COMMENT_MARKER}\n{seed_events}\n")

    outcome = service.start(_repository(), 34, phase="review", actor="codex", pr=35)

    assert not outcome.idempotent
    assert len(backend.comments) == 1, "새 comment를 만들지 않고 기존 comment만 수정해야 한다"
    write_session.post.assert_not_called()
    write_session.patch.assert_called_once()
    body = backend.comments[comment_id]
    events = parse_events(body)
    assert len(events) == 3
    assert events[-1].phase == "review"
    assert events[-1].event == "start"
    assert events[-1].actor == "codex"


# --- CP-018-4 -----------------------------------------------------------


def test_timeline_end_appends_marker_and_renders_status_card() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    service, _read_session, _write_session = _service(
        backend, clock=datetime(2026, 7, 15, 9, 30, tzinfo=UTC)
    )
    service.start(_repository(), 34, phase="dev", actor="claude")

    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 15, 10, 10, tzinfo=UTC)
    )
    outcome = service.end(
        _repository(), 34, phase="dev", actor="claude", result="pushed", pr=35
    )

    assert "<!--" not in outcome.status_card, "Status Card에는 raw marker를 노출하지 않는다"
    assert "Dev: " in outcome.status_card
    assert "(40m)" in outcome.status_card

    (body,) = backend.comments.values()
    events = parse_events(body)
    assert len(events) == 2
    assert events[-1].event == "end"
    assert events[-1].result == "pushed"
    assert events[-1].pr == 35


# --- CP-018-5 -----------------------------------------------------------


def test_timeline_request_changes_increments_next_dev_cycle() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:rework"])
    steps = [
        (datetime(2026, 7, 15, 9, 30, tzinfo=UTC), "dev", "start", "claude", None, None),
        (datetime(2026, 7, 15, 10, 10, tzinfo=UTC), "dev", "end", "claude", "pushed", 35),
        (datetime(2026, 7, 15, 10, 25, tzinfo=UTC), "review", "start", "codex", None, 35),
        (
            datetime(2026, 7, 15, 10, 40, tzinfo=UTC),
            "review",
            "end",
            "codex",
            "request-changes",
            35,
        ),
        (datetime(2026, 7, 15, 10, 42, tzinfo=UTC), "dev", "start", "claude", None, None),
    ]

    for at, phase, event_type, actor, result, pr in steps:
        service, _read_session, _write_session = _service(backend, clock=at)
        if event_type == "start":
            service.start(_repository(), 34, phase=phase, actor=actor, pr=pr)
        else:
            service.end(_repository(), 34, phase=phase, actor=actor, result=result, pr=pr)

    (body,) = backend.comments.values()
    events = parse_events(body)
    last_dev_start = [e for e in events if e.phase == "dev" and e.event == "start"][-1]
    assert last_dev_start.cycle == 2


# --- CP-018-6 -----------------------------------------------------------


def test_timeline_status_calculates_active_and_waiting_durations() -> None:
    backend = _FakeGitHubBackend(issue_number=31, labels=["devbot:review"])
    seed_events = "\n".join(
        [
            "<!-- devbot-timeline:v1 issue=31 pr=- cycle=1 phase=queue event=ready "
            "result=- at=2026-07-14T21:23:00Z -->",
            "<!-- devbot-timeline:v1 issue=31 pr=- cycle=1 phase=dev event=start "
            "result=- actor=claude at=2026-07-14T21:30:00Z -->",
            "<!-- devbot-timeline:v1 issue=31 pr=30 cycle=1 phase=dev event=end "
            "result=pushed actor=claude at=2026-07-14T22:10:00Z -->",
            "<!-- devbot-timeline:v1 issue=31 pr=30 cycle=1 phase=review event=start "
            "result=- actor=codex at=2026-07-14T22:25:00Z -->",
        ]
    )
    backend.seed_comment(f"{COMMENT_MARKER}\n{seed_events}\n")

    service, _read_session, _write_session = _service(
        backend, clock=datetime(2026, 7, 14, 22, 37, tzinfo=UTC)
    )
    outcome = service.status(_repository(), 31)
    card = outcome.status_card

    assert "Queue: " in card and "(7m)" in card
    assert "Dev: " in card and "(40m)" in card
    assert "Wait reviewer: " in card and "(15m)" in card
    assert "Review: " in card and "0h 12m 경과" in card
    assert "Total active: 52m (진행 중)" in card
    assert "Total waiting: 22m" in card
    assert "Total elapsed: 1h 14m" in card


# --- CP-018-7 -----------------------------------------------------------


def test_timeline_status_shows_actor_for_each_phase() -> None:
    backend = _FakeGitHubBackend(issue_number=31, labels=["devbot:review"])
    seed_events = "\n".join(
        [
            "<!-- devbot-timeline:v1 issue=31 pr=- cycle=1 phase=dev event=start "
            "result=- actor=claude at=2026-07-14T21:30:00Z -->",
            "<!-- devbot-timeline:v1 issue=31 pr=30 cycle=1 phase=dev event=end "
            "result=pushed actor=claude at=2026-07-14T22:10:00Z -->",
            "<!-- devbot-timeline:v1 issue=31 pr=30 cycle=1 phase=review event=start "
            "result=- actor=codex at=2026-07-14T22:25:00Z -->",
        ]
    )
    backend.seed_comment(f"{COMMENT_MARKER}\n{seed_events}\n")

    service, _read_session, _write_session = _service(
        backend, clock=datetime(2026, 7, 14, 22, 30, tzinfo=UTC)
    )
    card = service.status(_repository(), 31).status_card

    assert "(actor: claude)" in card
    assert "(actor: codex)" in card


# --- CP-018-8 -----------------------------------------------------------


def test_timeline_duplicate_start_is_idempotent() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    clock = datetime(2026, 7, 15, 9, 30, tzinfo=UTC)

    service, _read_session, write_session = _service(backend, clock=clock)
    first = service.start(_repository(), 34, phase="dev", actor="claude")
    assert not first.idempotent
    assert write_session.post.call_count == 1

    service, _read_session, write_session = _service(backend, clock=clock)
    second = service.start(_repository(), 34, phase="dev", actor="claude")

    assert second.idempotent
    assert write_session.post.call_count == 0
    assert write_session.patch.call_count == 0
    assert len(backend.comments) == 1
    (body,) = backend.comments.values()
    assert len(parse_events(body)) == 1


# --- CP-018-9 (overlapping start) ----------------------------------------


def test_timeline_rejects_overlapping_same_phase_start() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    service, _read_session, _write_session = _service(
        backend, clock=datetime(2026, 7, 15, 9, 30, tzinfo=UTC)
    )
    service.start(_repository(), 34, phase="dev", actor="claude")

    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 15, 9, 35, tzinfo=UTC)
    )
    with pytest.raises(TimelineOverlappingStartError):
        service.start(_repository(), 34, phase="dev", actor="codex")

    write_session.post.assert_not_called()
    write_session.patch.assert_not_called()
    (body,) = backend.comments.values()
    assert len(parse_events(body)) == 1


# --- CP-018-9 (end without start) ----------------------------------------


def test_timeline_rejects_end_without_start() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 15, 9, 30, tzinfo=UTC)
    )

    with pytest.raises(TimelineMissingStartError):
        service.end(_repository(), 34, phase="review", actor="codex", result="merge-ready")

    write_session.post.assert_not_called()
    write_session.patch.assert_not_called()
    assert backend.comments == {}


# --- CP-018-10 -------------------------------------------------------------


def test_timeline_update_failure_preserves_existing_comment() -> None:
    backend = _FakeGitHubBackend(issue_number=34, labels=["devbot:working"])
    original_body = (
        f"{COMMENT_MARKER}\n"
        "<!-- devbot-timeline:v1 issue=34 pr=- cycle=1 phase=dev event=start "
        "result=- actor=claude at=2026-07-15T09:30:00Z -->\n"
    )
    comment_id = backend.seed_comment(original_body)
    backend.patch_should_fail = True

    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 15, 10, 10, tzinfo=UTC)
    )

    with pytest.raises(GitHubTransientError):
        service.end(_repository(), 34, phase="dev", actor="claude", result="pushed", pr=35)

    assert write_session.patch.call_count == 3
    # 실패한 PATCH는 comment를 훼손하면 안 된다.
    assert backend.comments[comment_id] == original_body


# --- CP-018-11 -------------------------------------------------------------


def test_existing_devbot_once_cli_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lock_file = tmp_path / "devbot.lock"

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\nDEVBOT_LOCK_FILE={lock_file}\n",
        encoding="utf-8",
    )

    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with patch("devbot.main._run_startup_self_update", return_value=True):
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0


# --- 리뷰 피드백 회귀 (PR #35, hjlee83 REQUEST CHANGES Blocker 2) ----------
#
# 저장소 기본값 DRY_RUN=true 환경에서도 `timeline start/end`는 실제로
# GitHub에 기록해야 한다(Task 018 Goal, CP-018-2/3/4). 전역 DRY_RUN 대신
# 서브커맨드 자체의 `--dry-run` opt-in만 `TimelineService.dry_run`을
# 결정해야 한다.


def _write_env(tmp_path: Path, *, dry_run: str) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\nDRY_RUN={dry_run}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: true\n",
        encoding="utf-8",
    )
    return env_path, repositories_path


class _SpyTimelineService:
    """Records the `dry_run` a `timeline` CLI invocation wires into
    `TimelineService`, without making any real GitHub call."""

    last_dry_run: bool | None = None

    def __init__(self, *, read_client, write_client, dry_run: bool) -> None:
        type(self).last_dry_run = dry_run

    def start(self, *args, **kwargs) -> TimelineOutcome:
        return TimelineOutcome(status_card="ok")

    def end(self, *args, **kwargs) -> TimelineOutcome:
        return TimelineOutcome(status_card="ok")

    def status(self, *args, **kwargs) -> TimelineOutcome:
        return TimelineOutcome(status_card="ok")


def test_timeline_start_ignores_global_dry_run_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("devbot.main.TimelineService", _SpyTimelineService)
    env_path, repositories_path = _write_env(tmp_path, dry_run="true")

    exit_code = main(
        ["timeline", "start", "--issue", "1", "--phase", "dev", "--actor", "claude"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    assert _SpyTimelineService.last_dry_run is False


def test_timeline_start_dry_run_flag_opts_into_preview_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("devbot.main.TimelineService", _SpyTimelineService)
    env_path, repositories_path = _write_env(tmp_path, dry_run="false")

    exit_code = main(
        [
            "timeline",
            "start",
            "--issue",
            "1",
            "--phase",
            "dev",
            "--actor",
            "claude",
            "--dry-run",
        ],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    assert _SpyTimelineService.last_dry_run is True


# --- Task 024: automatic Timeline recording -------------------------------


def test_ready_records_exactly_one_event() -> None:
    """CP-024-1 (unit level): `TimelineService.ready()` writes a marker the
    first time, and is a no-op on every later replay regardless of which
    cycle is currently active."""
    backend = _FakeGitHubBackend(issue_number=40, labels=["devbot:working"])
    service, _read_session, write_session = _service(
        backend, clock=datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    )

    first = service.ready(_repository(), 40)
    assert not first.idempotent
    write_session.post.assert_called_once()

    second = service.ready(_repository(), 40)
    assert second.idempotent
    write_session.post.assert_called_once()
    write_session.patch.assert_not_called()

    (body,) = backend.comments.values()
    events = parse_events(body)
    assert len(events) == 1
    assert events[0].phase == "queue"
    assert events[0].event == "ready"


def test_automatic_timeline_recording_is_idempotent() -> None:
    """CP-024-8: retrying the same lifecycle boundary through the daemon's
    `safe_start`/`safe_end` helpers does not append duplicate markers -
    `TimelineService`'s own idempotency (CP-018-8) is what every automatic
    call site (`devbot.polling`/`devbot.review`/`devbot.rework`) relies on."""
    backend = _FakeGitHubBackend(issue_number=41, labels=["devbot:working"])
    clock = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)

    service, _read_session, write_session = _service(backend, clock=clock)
    safe_start(service, _repository(), 41, phase="dev", actor="claude")
    safe_start(service, _repository(), 41, phase="dev", actor="claude")

    assert write_session.post.call_count == 1, "재시도된 dev:start가 중복 comment를 만들면 안 된다"
    (body,) = backend.comments.values()
    assert len(parse_events(body)) == 1

    service, _read_session, write_session = _service(backend, clock=clock)
    safe_end(service, _repository(), 41, phase="dev", actor="claude", result="pushed")
    safe_end(service, _repository(), 41, phase="dev", actor="claude", result="pushed")

    (body,) = backend.comments.values()
    assert len(parse_events(body)) == 2, "재시도된 dev:end가 중복 marker를 추가하면 안 된다"


def test_safe_helpers_swallow_timeline_write_failures() -> None:
    """CP-024-10 (unit level): `safe_ready`/`safe_start`/`safe_end` never
    raise even when the underlying `TimelineService` call fails - this is
    the single implementation choke point every automatic call site relies
    on to preserve its own primary outcome."""
    timeline = MagicMock(spec=TimelineService)
    timeline.ready.side_effect = RuntimeError("boom")
    timeline.start.side_effect = RuntimeError("boom")
    timeline.end.side_effect = RuntimeError("boom")
    logger = logging.getLogger("devbot-test-safe-helpers")

    safe_ready(timeline, _repository(), 1, logger=logger)
    safe_start(timeline, _repository(), 1, phase="dev", actor="claude", logger=logger)
    safe_end(
        timeline, _repository(), 1, phase="dev", actor="claude", result="blocked", logger=logger
    )

    # `timeline=None` (the default on every affected dataclass) is also a
    # silent no-op - no attribute access on `None` is ever attempted.
    safe_ready(None, _repository(), 1)
    safe_start(None, _repository(), 1, phase="dev", actor="claude")
    safe_end(None, _repository(), 1, phase="dev", actor="claude", result="blocked")


def test_manual_timeline_cli_remains_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-024-11: Task 018's `timeline start`/`end`/`status` behavior is
    unaffected by Task 024's automatic-recording additions - the daemon-only
    `ready`/`safe_start`/`safe_end` helpers are never invoked by this manual
    CLI path, so no `ready` event ever appears from a manual sequence."""
    backend = _FakeGitHubBackend(issue_number=70, labels=["devbot:working"])
    read_client, write_client, _read_session, write_session = backend.build_clients()
    monkeypatch.setattr("devbot.main.GitHubClient", lambda token: read_client)
    monkeypatch.setattr("devbot.main.GitHubWriteClient", lambda token: write_client)

    env_path, repositories_path = _write_env(tmp_path, dry_run="true")

    start_exit = main(
        ["timeline", "start", "--issue", "70", "--phase", "dev", "--actor", "claude"],
        env_path=env_path,
        repositories_path=repositories_path,
    )
    end_exit = main(
        [
            "timeline",
            "end",
            "--issue",
            "70",
            "--phase",
            "dev",
            "--actor",
            "claude",
            "--result",
            "pushed",
            "--pr",
            "35",
        ],
        env_path=env_path,
        repositories_path=repositories_path,
    )
    status_exit = main(
        ["timeline", "status", "--issue", "70"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert (start_exit, end_exit, status_exit) == (0, 0, 0)
    assert write_session.post.call_count == 1
    assert write_session.patch.call_count == 1
    (body,) = backend.comments.values()
    events = parse_events(body)
    assert len(events) == 2
    assert events[0].phase == "dev" and events[0].event == "start"
    assert events[1].phase == "dev" and events[1].event == "end" and events[1].result == "pushed"
    assert all(
        e.event != "ready" for e in events
    ), "수동 timeline 명령은 ready 이벤트를 만들지 않는다"
