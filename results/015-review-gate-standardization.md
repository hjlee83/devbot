# Task 015 Result: Review Gate Standardization

## 완료 내용

- AGENTS.md 리뷰 기준에 PR Evidence, Result, CI, Task 계약, 운영 정책 불일치 시 `REQUEST CHANGES` 판단 규칙을 명시했다.
- Task 계약 표준 문서에 단일 Task 추적 정책, PR Evidence 필수 항목, Review Gate 표준을 추가했다.
- 자동 리뷰 프롬프트가 Task 계약, Result, PR Evidence, CI, 운영 정책 정합성을 확인하도록 강화했다.
- 구현 관여 리뷰어가 Evidence를 더 엄격히 확인해야 한다는 기준을 문서와 프롬프트에 고정했다. 이 기준은
  구현 관여 여부와 관계없이 모든 리뷰어에게 예외 없이 적용되는 기본값이며, 구현 관여 리뷰어는 그 위에
  추가로 더 엄격한 evidence 확인을 수행한다는 점을 AGENTS.md, `docs/09-task-contract-standard.md`,
  `build_review_prompt()`에 동일하게 명시했다(아래 Rework 이력 참고).
- 기존 리뷰 상태 파싱과 `REQUEST CHANGES` rework 전환 회귀를 보존하는 테스트를 추가했다.
- `docs/00-roadmap.md`에 Task 015를 완료 항목(`[x]`)으로 추가했다.

## Rework 이력

head `678fe7a`에 대한 리뷰가 `REQUEST CHANGES`로 판정되어 아래 Blocker를 반영했다.

- PR Evidence 최신성 부족 → PR 본문을 최신 head 기준 Checkpoint별 테스트, Result 경로,
  검증 명령 실행 결과, CI 상태(또는 확인 불가 사유)로 갱신했다.
- Result 문서의 수정 파일 목록이 실제 diff와 불일치 → 아래 수정 파일 목록에 실제 diff에 포함된
  `docs/00-roadmap.md`, `tasks/015-review-gate-standardization.md`를 추가했다.
- `uv run devbot --once --dry-run` 실패(`iteration_error`)가 PR Evidence에 반영되지 않음 →
  아래 검증 결과와 PR 본문에 원인(샌드박스 아웃바운드 네트워크 차단)과 merge 판단 영향을 명시했다.
- CI 상태 Evidence 누락 → 이 실행 환경에서 GitHub API 조회가 차단되어 CI 상태를 직접 확인할 수
  없다는 점과 그 사유를 PR 본문에 명시했다(`docs/09-task-contract-standard.md`의 PR Evidence
  필수 항목이 허용하는 "CI 상태 또는 확인하지 못한 명확한 이유"에 해당).
- Warning(구현 관여 리뷰어 기준과 "항상 strict" 운영 의도 간 긴장) → AGENTS.md,
  `docs/09-task-contract-standard.md`, `build_review_prompt()`에 "이 게이트는 구현 관여 여부와
  관계없이 모든 리뷰어에게 예외 없이 적용되는 기본값"이라는 문장을 추가해, 구현 관여 리뷰어 규칙이
  기본 기준을 낮추는 예외가 아니라 그 위에 얹는 추가 확인임을 명확히 했다.

## 수정 파일

- `AGENTS.md`
- `docs/00-roadmap.md`
- `docs/09-task-contract-standard.md`
- `results/015-review-gate-standardization.md`
- `src/devbot/review.py`
- `tasks/015-review-gate-standardization.md`
- `tests/test_review.py`

## Checkpoint별 테스트

- CP-015-1: `test_agents_review_gate_requires_request_changes_for_evidence_mismatch`
- CP-015-2: `test_task_contract_standard_documents_single_task_branch_pr_policy`
- CP-015-3: `test_task_contract_standard_documents_single_task_branch_pr_policy`
- CP-015-4: `test_review_prompt_requires_contract_result_pr_ci_alignment`
- CP-015-5: `test_review_prompt_requires_request_changes_for_any_gate_mismatch`
- CP-015-6: `test_review_prompt_mentions_stricter_evidence_for_involved_reviewer`
- CP-015-7: 이 Result 문서와 전체 검증 결과
- CP-015-8: `test_review_status_parsing_still_requires_exactly_one_status`, 기존 `test_request_changes_moves_issue_from_review_to_rework`

## 검증 결과

- `uv sync`: 통과
- `uv run ruff check .`: 통과
- `uv run pytest`: 통과, 236 passed
- `uv run devbot --once --dry-run`: 실행되었으나 `iteration_error`로 종료.
  - 원인: 이 실행 환경(로컬 sandbox/CI 실행기)이 GitHub API로 나가는 아웃바운드 네트워크를
    차단하고 있어 `--once --dry-run`이 관리 저장소의 Issue를 조회하는 단계에서 DNS/연결에
    실패한다.
  - Task 015 변경 범위(`AGENTS.md`, `docs/09-task-contract-standard.md`, `src/devbot/review.py`
    의 리뷰 프롬프트 문자열, `docs/00-roadmap.md`)는 네트워크 호출 경로나 dry-run 로직 자체를
    건드리지 않으므로, 이 실패는 Task 015 구현의 회귀가 아니라 실행 환경의 네트워크 제약이다.
  - **merge 판단 영향**: 이 caveat만으로 blocker는 아니지만, GitHub 네트워크가 열려 있는
    환경(CI 또는 사람 리뷰어의 로컬 환경)에서 `uv run devbot --once --dry-run`이 정상 종료
    (`iteration_error`가 아님)하는지 최소 1회 이상 재확인한 뒤 merge할 것을 권장한다.
- CI 상태: 이 실행 환경에서는 GitHub API 조회가 차단되어 있어 PR #28에 연결된 CI 워크플로의
  성공/실패 여부를 직접 확인하지 못했다. PR 본문에도 동일하게 "CI 상태 확인 불가 + 사유"를
  Evidence로 명시했다 — `docs/09-task-contract-standard.md`의 PR Evidence 필수 항목이 정의하는
  "CI 상태 또는 CI를 확인하지 못한 명확한 이유"에 해당한다. 사람 리뷰어가 GitHub UI에서 CI
  상태를 직접 확인해야 한다.

## TODO

- 없음

## 위험 요소

- 리뷰 게이트 기준은 프롬프트와 문서로 강제되므로, 실제 판단 품질은 리뷰 Agent가 해당 기준을 따르는지에 의존한다.
- `uv run devbot --once --dry-run`을 이 실행 환경에서 네트워크 제약 없이 검증하지 못했다. merge
  전에 네트워크가 열린 환경에서 재검증이 필요하다(위 검증 결과 참고).
- CI 상태를 이 환경에서 직접 확인하지 못했다. merge 전에 사람이 GitHub UI에서 CI 결과를
  확인해야 한다.

## Improvement Suggestions

- 향후 PR Evidence를 구조화된 템플릿으로 파싱해 누락 항목을 자동 감지하면 리뷰 전 단계에서 더 빨리 차단할 수 있다.
