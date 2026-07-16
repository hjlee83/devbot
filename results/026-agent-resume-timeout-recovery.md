# Task 026: Agent Resume & Timeout Recovery

## 완료 내용

- Agent timeout/interruption을 `AgentOutcome.RESUMABLE_INTERRUPTION`으로 구조화했다.
- Claude CLI timeout은 generic failure가 아니라 resumable outcome hint를 반환한다.
- IMPLEMENT Job에서 resumable interruption이 발생하면 prepared worktree를 삭제하지 않고 Issue를 이전 안정 상태로 복구하며, `devbot-resume:v1` 진단 marker를 남긴다.
- 다음 cycle에서 같은 Issue/PR/branch/contract metadata의 dirty reused worktree만 continuation prompt를 받는다.
- Resume attempt는 Issue comment marker에서 읽어 기본 3회로 제한하고, cap 초과 또는 contract metadata 누락은 worktree를 보존한 채 `devbot:manual-action`으로 전환한다.
- Reliability, worktree, Timeline 문서와 roadmap을 갱신했다.

## 수정 파일

- `src/devbot/models.py`
- `src/devbot/agent_outcome.py`
- `src/devbot/agents/claude.py`
- `src/devbot/worktree.py`
- `src/devbot/issue_state.py`
- `src/devbot/polling.py`
- `src/devbot/delivery.py`
- `src/devbot/github_write_client.py`
- `tests/test_agent_outcome.py`
- `tests/test_agents_claude.py`
- `tests/test_delivery.py`
- `tests/test_github_write_client.py`
- `tests/test_polling.py`
- `docs/00-roadmap.md`
- `docs/10-github-status-timeline.md`
- `docs/11-daemon-reliability.md`
- `docs/13-host-managed-workspace-preparation.md`
- `results/026-agent-resume-timeout-recovery.md`

## Checkpoint별 테스트

- CP-026-1: `test_agent_timeout_is_classified_resumable`
- CP-026-2: `test_timeout_preserves_unfinished_worktree`
- CP-026-3: `test_matching_dirty_worktree_is_detected_as_resumable`
- CP-026-4: `test_resume_prompt_includes_existing_work_instructions`
- CP-026-5: `test_resume_reuses_existing_branch_and_pr`
- CP-026-6: `test_resume_attempt_limit_requires_manual_action`
- CP-026-7: `test_timeout_and_resume_are_recorded_idempotently`
- CP-026-8: `test_resumed_execution_completes_existing_pr_delivery`
- CP-026-9: `test_unsafe_resume_is_rejected_without_deleting_work`
- CP-026-10: `test_existing_workflows_compatible_with_resume_recovery`
- CP-026-11: 문서 및 Result 갱신
- Delivery regression: `test_delivery_comments_existing_pr_evidence_without_replacing_body`

## 검증 결과

- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/python -m pytest tests/test_agent_outcome.py tests/test_agents_claude.py tests/test_polling.py tests/test_worktree.py -q` 통과: 127 passed.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/python -m ruff check ...` 통과.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/python -m pytest -q` 통과: 400 passed.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/python -m ruff check .` 통과.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/devbot doctor` 실행: 명령은 동작했으나 현재 환경의 `/tmp/devbot.lock` 점유, GitHub DNS 제한, 원본 checkout의 `.env.backup.20260716-075550` 미커밋 파일로 `safe_to_start: no`.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/devbot --once --dry-run` 실행: 현재 환경의 `/tmp/devbot.lock` 점유로 시작 전 실패.
- Delivery failure 조사 중 기존 PR 재사용 경로의 Evidence 미갱신 회귀를 재현하고 수정했다. 리뷰 반영 후 기존 PR delivery는 Planner가 작성한 PR body를 덮어쓰지 않고, commit/push 후 최신 checkpoint evidence를 Issue/PR comment로 남긴다.

## TODO

- 전체 Validation Gate(`uv sync`, `uv run ruff check .`, `uv run pytest`, `uv run devbot doctor`, `uv run devbot --once --dry-run`)는 네트워크 제한으로 새 uv 환경이 의존성을 다운로드하지 못해 아직 완료하지 못했다.
- PR #51 Evidence는 원격 쓰기 권한/네트워크가 가능한 환경에서 최신 검증 결과와 함께 갱신해야 한다.

## 위험 요소

- Resume attempt marker는 Issue comment 기반이다. 댓글 조회가 실패하면 attempt를 0으로 간주하므로, GitHub read 장애가 반복되면 cap 판단이 지연될 수 있다.
- CodexRunner에는 별도 timeout 설정이 없으므로, 현재 구조화된 timeout hint는 ClaudeRunner 및 테스트/미래 runner가 반환하는 `outcome_hint` 경로로 검증했다.

## Improvement Suggestions

- `RESUME_ATTEMPT_LIMIT`를 환경 설정으로 노출하면 운영 환경별 timeout/retry 정책을 더 쉽게 조정할 수 있다.
- CodexRunner에도 명시적 timeout_seconds를 도입하면 runner 간 timeout classification이 더 균일해진다.
