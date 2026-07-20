# Task 130 Result: Execution Lifecycle Logging

## 완료 내용

- `observability.log_lifecycle_stage_started()`와 `observability.log_lifecycle_stage_finished()`를 추가했다.
- 기존 `observability.log_stage()`의 DEBUG 완료 로그는 유지하면서, 같은 호출에서 INFO lifecycle end 로그도 함께 남기도록 했다.
- `PollingService`의 주요 stage timer 시작 시점에 lifecycle start 로그를 추가했다.
- raw stage 이름을 operator-facing lifecycle stage로 정규화했다.
  - `workspace_validate`, `workspace_preparation` -> `bootstrap`
  - `agent_execution`, `rework_process` -> `implement`
  - `verification` -> `verify`
  - `delivery`, `automerge_gate` -> `delivery`
  - `review_process` -> `review`
- Delivery path는 `verify`와 `delivery` lifecycle start/end를 모두 남긴다.

## 주요 설계 결정

- 기존 `stage_finished` DEBUG event는 제거하지 않았다. Issue #130은 observability 개선이지 기존 진단 필드 제거가 아니므로, 기존 로그/테스트 소비자를 보존했다.
- lifecycle 로그는 INFO로 남긴다. tmux/터미널에서 기본 로그 수준으로도 현재 stage와 elapsed time을 볼 수 있어야 하기 때문이다.
- stage failure의 원인 상세는 기존 error log, job failure summary, diagnostic report가 계속 담당한다. lifecycle end record는 stage/status/elapsed 중심의 짧은 operator signal로 유지했다.

## 수정 파일

- `src/devbot/observability.py`
- `src/devbot/polling.py`
- `tests/test_observability.py`
- `tests/test_polling.py`
- `tasks/130-lifecycle-logging.md`
- `results/130-lifecycle-logging.md`
- `docs/00-roadmap.md`

## 테스트 Evidence

- `test_lifecycle_stage_logs_start_and_end_with_elapsed_ms`
- `test_implement_job_emits_lifecycle_stage_logs`

## Validation 결과

```bash
$ uv run ruff check .
All checks passed!

$ uv run pytest tests/test_observability.py tests/test_polling.py tests/test_task_contract_docs.py -q
145 passed in 1.42s

$ uv run pytest
1377 passed in 185.00s
```

## 남은 TODO와 제한

- Metrics backend/dashboard는 없다.
- Stage duration은 로그에만 남고 별도 저장소에 누적하지 않는다.
- GitHub Timeline comment 형식은 기존 dev/review phase marker를 유지한다.
