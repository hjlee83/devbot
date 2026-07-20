# Task 138 Result: Runtime Scheduler Architecture

## 완료 내용

- `RuntimeScheduler`를 추가해 polling의 selected `Job` 실행을 전담하도록 했다.
- repository별 FIFO queue 구성을 명시하고, repository lock으로 같은 repository 작업을 한 번에 하나만 실행하도록 했다.
- global worker pool 크기는 기존 `MAX_CONCURRENT_JOBS`를 사용한다.
- AI 실행 동시성은 새 `AI_CONCURRENCY` 설정과 semaphore로 worker concurrency와 분리했다.
- `PollingService._execute_jobs()`가 단일/복수 job 모두 runtime scheduler를 통과하도록 연결했다.
- `devbot status` 명령이 scheduler worker count, AI concurrency, worker state snapshot을 읽기 전용으로 출력한다.
- startup observability 로그에 `ai_concurrency`를 포함했다.

## 주요 설계 결정

- Task 012의 pure selector는 그대로 유지했다. 후보 선택 정책은 `devbot.scheduler`가 담당하고, Task 138은 선택된 job의 runtime execution 계층만 추가한다.
- 같은 repository serialization은 per-repository lock으로 강제했다. 서로 다른 repository job은 worker capacity와 AI semaphore가 허용하는 범위에서 병렬 실행된다.
- `AI_CONCURRENCY` 기본값은 `1`로 두어 기존 보수적 실행 동작을 유지했다.
- `devbot status`는 현재 daemon IPC를 추가하지 않고, 현 구성으로 만들 수 있는 scheduler snapshot을 출력하는 읽기 전용 진입점으로 제한했다.

## 수정 파일

- `.env.example`
- `src/devbot/config.py`
- `src/devbot/main.py`
- `src/devbot/models.py`
- `src/devbot/observability.py`
- `src/devbot/polling.py`
- `src/devbot/runtime_scheduler.py`
- `tests/conftest.py`
- `tests/test_config.py`
- `tests/test_main.py`
- `tests/test_observability.py`
- `tests/test_runtime_scheduler.py`
- `tasks/138-runtime-scheduler.md`
- `results/138-runtime-scheduler.md`
- `docs/00-roadmap.md`

## Checkpoint별 Evidence

- Different repositories concurrent: `test_runtime_scheduler_runs_different_repositories_concurrently`
- Same repository sequential: `test_runtime_scheduler_serializes_same_repository_jobs`
- Independent AI concurrency: `test_runtime_scheduler_ai_semaphore_limits_ai_concurrency`
- One queue per repository: `test_runtime_scheduler_builds_one_fifo_queue_per_repository`
- Runtime state snapshot: `test_runtime_scheduler_snapshot_exposes_worker_state`
- Configuration: `test_ai_concurrency_defaults_to_one`, `test_invalid_ai_concurrency_is_rejected`
- Status command: `test_status_command_reports_runtime_scheduler_state`
- Startup evidence: `test_log_startup_never_includes_github_token`

## Validation 결과

```bash
$ uv run ruff check .
All checks passed!

$ uv run pytest
1388 passed in 199.83s (0:03:19)
```

## 남은 TODO와 제한

- Running daemon의 live status IPC는 없다. 이번 Task는 status에 노출 가능한 runtime snapshot 모델과 CLI 출력만 제공한다.
- GoalExecutionPlan, DAG scheduling, retry priority, multi-agent orchestration은 범위 밖이다.
- Per-repository polling cadence와 repository별 별도 로그 파일은 후속 작업 범위다.
