# Task 015 Result: Review Gate Standardization

## 완료 내용

- AGENTS.md 리뷰 기준에 PR Evidence, Result, CI, Task 계약, 운영 정책 불일치 시 `REQUEST CHANGES` 판단 규칙을 명시했다.
- Task 계약 표준 문서에 단일 Task 추적 정책, PR Evidence 필수 항목, Review Gate 표준을 추가했다.
- 자동 리뷰 프롬프트가 Task 계약, Result, PR Evidence, CI, 운영 정책 정합성을 확인하도록 강화했다.
- 구현 관여 여부나 특정 Agent 종류와 무관하게 모든 Reviewer가 같은 strict gate를 적용한다는 기준을
  AGENTS.md, `docs/09-task-contract-standard.md`, `build_review_prompt()`에 동일하게 명시했다
  (아래 Rework 이력 참고).
- 기존 리뷰 상태 파싱과 `REQUEST CHANGES` rework 전환 회귀를 보존하는 테스트를 추가했다.
- `docs/00-roadmap.md`에 Task 015를 완료 항목(`[x]`)으로 추가했다.

## Rework 이력

head `678fe7a`에 대한 리뷰가 `REQUEST CHANGES`로 판정되어 아래 Blocker를 반영했다.

- PR Evidence 최신성 부족 → PR 본문을 최신 head 기준 Checkpoint별 테스트, Result 경로,
  검증 명령 실행 결과, CI 상태, dry-run caveat로 갱신했다.
- Result 문서의 수정 파일 목록이 실제 diff와 불일치 → 아래 수정 파일 목록에 실제 diff에 포함된
  `docs/00-roadmap.md`, `tasks/015-review-gate-standardization.md`를 추가했다.
- `uv run devbot --once --dry-run` 실패(`iteration_error`)가 PR Evidence에 반영되지 않음 →
  아래 검증 결과와 PR 본문에 원인(샌드박스 아웃바운드 네트워크 차단)과 merge 판단 영향을 명시했다.
- CI 상태 Evidence 누락 → GitHub PR #28의 `verify` check가 SUCCESS임을 PR 본문과 검증 결과에
  명시했다.
- Warning(구현 관여 리뷰어 기준과 "항상 strict" 운영 의도 간 긴장) → AGENTS.md,
  `docs/09-task-contract-standard.md`, `build_review_prompt()`에 구현 관여 여부나 특정 Agent
  종류에 따라 기준을 완화하거나 강화하지 않고, 모든 Reviewer가 같은 strict gate를 적용한다고
  명확히 했다.

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
- CP-015-6: `test_review_prompt_applies_same_strict_gate_to_all_reviewers`
- CP-015-7: 이 Result 문서와 전체 검증 결과
- CP-015-8: `test_review_status_parsing_still_requires_exactly_one_status`, 기존 `test_request_changes_moves_issue_from_review_to_rework`

## 검증 결과

- `uv sync`: 통과
- `uv run ruff check .`: 통과
- `uv run pytest`: 통과, 230 passed
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
- CI 상태: GitHub PR #28의 `verify` check가 SUCCESS임을 확인했다.

## TODO

- 없음

## 위험 요소

- 리뷰 게이트 기준은 프롬프트와 문서로 강제되므로, 실제 판단 품질은 리뷰 Agent가 해당 기준을 따르는지에 의존한다.
- `uv run devbot --once --dry-run`을 이 실행 환경에서 네트워크 제약 없이 검증하지 못했다. merge
  전에 네트워크가 열린 환경에서 재검증이 필요하다(위 검증 결과 참고).
- PR Evidence는 GitHub PR 본문 metadata이므로 repository commit만으로 갱신되지 않는다. merge 전에
  PR 본문이 이 Result와 최신 head 기준으로 일치하는지 확인해야 한다.

## Improvement Suggestions

- 향후 PR Evidence를 구조화된 템플릿으로 파싱해 누락 항목을 자동 감지하면 리뷰 전 단계에서 더 빨리 차단할 수 있다.
