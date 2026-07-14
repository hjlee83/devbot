# Task 015 Result: Review Gate Standardization

## 완료 내용

- AGENTS.md 리뷰 기준에 PR Evidence, Result, CI, Task 계약, 운영 정책 불일치 시 `REQUEST CHANGES` 판단 규칙을 명시했다.
- Task 계약 표준 문서에 단일 Task 추적 정책, PR Evidence 필수 항목, Review Gate 표준을 추가했다.
- 자동 리뷰 프롬프트가 Task 계약, Result, PR Evidence, CI, 운영 정책 정합성을 확인하도록 강화했다.
- 구현 관여 리뷰어가 Evidence를 더 엄격히 확인해야 한다는 기준을 문서와 프롬프트에 고정했다.
- 기존 리뷰 상태 파싱과 `REQUEST CHANGES` rework 전환 회귀를 보존하는 테스트를 추가했다.

## 수정 파일

- `AGENTS.md`
- `docs/09-task-contract-standard.md`
- `src/devbot/review.py`
- `tests/test_review.py`
- `results/015-review-gate-standardization.md`

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

- `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv sync`: 통과
- `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv run ruff check .`: 통과
- `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv run pytest`: 통과, 230 passed
- `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache DEVBOT_LOCK_FILE=/Users/luna/workspace/devbot/.devbot-dry-run.lock uv run devbot --once --dry-run`: 실행됨, GitHub 네트워크 조회 제한으로 `iteration_error`

## TODO

- 없음

## 위험 요소

- 리뷰 게이트 기준은 프롬프트와 문서로 강제되므로, 실제 판단 품질은 리뷰 Agent가 해당 기준을 따르는지에 의존한다.

## Improvement Suggestions

- 향후 PR Evidence를 구조화된 템플릿으로 파싱해 누락 항목을 자동 감지하면 리뷰 전 단계에서 더 빨리 차단할 수 있다.
