# Task 030: GitHub API Transient Retry Result

## 완료 내용

- GitHub read/write 클라이언트가 공유하는 `devbot.github_retry` 정책을 추가했다.
- HTTP 429, 500, 502, 503, 504와 `requests.Timeout`, `requests.ConnectionError`를 transient로 분류하고 bounded exponential backoff + jitter로 재시도한다.
- GitHub `Retry-After`가 있으면 계산된 backoff보다 우선 적용하되 `max_delay_seconds`를 넘지 않게 제한한다.
- 401/403은 인증/권한 오류, 404는 not found로 유지했다.
- transient GitHub 실패가 polling claim/read 단계에서 `blocked`/`manual-action` 라벨 전이로 이어지지 않도록 `GitHubTransientError`를 별도 처리했다.
- retry 진단 로그는 status, attempt, delay, endpoint category, outcome, error type만 기록하고 Authorization/token은 기록하지 않는다.
- Codex 실행 명령을 CLI help 기반으로 구성해 PreparedWorkspace cwd, `workspace-write`, approval `never`, network enabled, 최소 writable root를 강제한다.
- PreparedWorkspace에서 `git rev-parse --git-dir`, `--git-common-dir`, `--show-toplevel`을 해석해 worktree Git metadata 접근을 안전하게 허용한다.
- IMPLEMENT/REWORK 준비 단계에서 canonical Task branch와 `origin/main`을 fetch하고, clean worktree와 PR head를 확인한 뒤 rebase + `--force-with-lease`만 사용해 main에 동기화한다.
- REVIEW 준비 단계는 PR head를 변경하지 않는 `synchronize_with_main=False` 경로를 사용하고, interactive approval 출력은 review summary 파싱 전에 `agent_configuration_invalid`로 manual-action 처리한다.
- 리뷰 지적을 반영해 future worktree의 기본 root를 repository-local `.worktrees/issue-<number>`로 변경하고 `.git/info/exclude`에 `.worktrees/`를 등록해 operator checkout을 dirty로 만들지 않게 했다.
- REVIEW 전에 `git merge-tree --write-tree origin/<main> HEAD`로 latest-main mergeability를 계산해 PR branch를 변경하지 않는 integration validation을 수행한다.
- Codex production command에 `sandbox_workspace_write.network_access=true` config override를 추가해 network policy가 단순 진단이 아니라 실행 명령에 반영되게 했다.

## 주요 설계 결정

- read/write 클라이언트의 HTTP 메서드 래퍼에서 같은 `execute_with_github_retry()`를 호출하도록 해 정책 drift를 막았다.
- retry 설정은 `GitHubRetryConfig`로 주입 가능하게 해 테스트에서 sleep과 random을 deterministic하게 대체했다.
- transient exhaustion은 `GitHubTransientError`로 표면화해 polling/reliability가 GitHub API 계열 실패로 다루되 상태 mutation은 만들지 않게 했다.
- Codex CLI 옵션은 설치 버전별 차이가 있어 `codex --help`와 `codex exec --help` capability를 확인한 뒤 지원되는 root 옵션을 exec 앞에 배치한다. 현재 확인 버전은 `codex-cli 0.144.1`이다.
- main 동기화 충돌은 `git rebase --abort` 후 `task_branch_conflict`로 분류해 원래 branch/worktree를 보존한다.
- REVIEW integration validation은 merge commit이나 rebase를 만들지 않고 Git object 계산만 수행하는 `merge-tree --write-tree`를 사용한다.

## 수정 파일

- `src/devbot/github_retry.py`
- `src/devbot/github_client.py`
- `src/devbot/github_write_client.py`
- `src/devbot/polling.py`
- `src/devbot/worktree.py`
- `src/devbot/main.py`
- `src/devbot/agents/base.py`
- `src/devbot/agents/codex.py`
- `src/devbot/review.py`
- `tests/test_github_client.py`
- `tests/test_polling.py`
- `tests/test_timeline.py`
- `tests/test_agents_codex.py`
- `tests/test_worktree.py`
- `tests/test_review.py`
- `tests/test_doctor.py`
- `tasks/030-github-api-transient-retry.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-030-1 transient classification | `test_github_failure_classification_distinguishes_transient_and_permanent_errors` |
| CP-030-2 bounded retry | `test_github_transient_failure_retries_with_bounded_backoff` |
| CP-030-3 Retry-After | `test_github_retry_after_header_is_honored` |
| CP-030-4 read/write parity | `test_github_read_and_write_clients_share_retry_policy` |
| CP-030-5 state preservation | `test_transient_github_failure_preserves_task_state` |
| CP-030-6 idempotent recovery | `test_github_retry_recovery_does_not_duplicate_side_effects` |
| CP-030-7 safe diagnostics | `test_github_retry_diagnostics_are_structured_and_redacted` |
| CP-030-8 workflow compatibility | `test_existing_workflows_remain_compatible_with_github_retry`, full `uv run pytest` |
| CP-030-9 non-interactive Codex execution | `test_codex_runner_builds_unattended_workspace_scoped_command` |
| CP-030-10 interactive prompt detection | `test_interactive_approval_output_is_configuration_invalid_before_summary_parse` |
| CP-030-11 interactive failure recovery | `test_interactive_approval_output_is_configuration_invalid_before_summary_parse` |
| CP-030-12 effective policy diagnostics | `test_codex_runner_policy_reports_safe_effective_settings` |
| CP-030-13 PreparedWorkspace Git metadata access | `test_prepared_workspace_resolves_git_metadata_paths`, `test_codex_runner_builds_unattended_workspace_scoped_command` |
| CP-030-14 IMPLEMENT/REWORK main synchronization | `test_implement_prepare_rebases_latest_main_and_force_pushes_with_lease`, `test_dirty_worktree_is_not_rebased_or_overwritten`, `test_stale_pr_head_metadata_stops_execution` |
| CP-030-15 conflict-safe recovery | `test_rebase_conflict_preserves_original_branch` |
| CP-030-16 non-mutating latest-main review validation | `test_review_prepare_does_not_change_pr_head`, `test_review_integration_validation_uses_non_mutating_merge_tree`, `test_review_validates_latest_main_integration_before_agent`, `test_review_stops_when_latest_main_integration_conflicts` |
| Review comment 1 repository-local future worktrees | `test_worktree_default_root_is_repository_local_dot_worktrees`, `test_job_uses_isolated_worktree`, `test_doctor_reports_worktree_health` |
| Review comment 2 isolated latest-main validation | `test_review_integration_validation_uses_non_mutating_merge_tree`, `test_review_validates_latest_main_integration_before_agent`, `test_review_stops_when_latest_main_integration_conflicts` |
| Review comment 3 enforced Codex network policy | `test_codex_runner_builds_unattended_workspace_scoped_command` |

## Validation 결과

- `uv sync` PASS
- `uv run ruff check .` PASS
- `uv run pytest` PASS: 461 passed
- `uv run devbot doctor` PASS
- `uv run devbot --once --dry-run` PASS: `NO_RUNNABLE_TASK` / `skipped_active_task` (Issue #62는 `devbot:rework` 상태였지만 처리 가능한 unprocessed rework 후보가 없어 dry-run으로 안전 종료)
- Post-label `uv run devbot --once --dry-run` LIMITATION: Issue #62를 `devbot:review`로 전환한 뒤 현재 Codex 세션의 filesystem sandbox가 로컬 Git metadata 쓰기를 막아 로컬 HEAD를 원격 최종 commit으로 이동하지 못했고, 그 때문에 canonical worktree가 local dirty로 감지되어 review workspace preparation이 `workspace_dirty`로 중단되었다. 원격 PR #63 head는 최종 commit으로 갱신되었고 Issue label은 `devbot:review`다.

## 수동 검증 결과

- 429, 500, 502, 503, 504는 deterministic unit test로 transient retry를 검증했다.
- timeout/connection failure 분류는 `classify_github_failure()`에서 `requests.Timeout`, `requests.ConnectionError`를 transient로 다루도록 구현했다.
- 401/403/404는 retry 대상이 아니며 각각 인증/권한, not found로 구분된다.
- 실제 sleep이나 외부 네트워크에 의존하지 않도록 retry sleep/random은 테스트에서 주입했다.
- Codex production command before: `codex exec <prompt>`.
- Codex production command after: `codex -a never -s workspace-write -C <PreparedWorkspace> -c sandbox_workspace_write.network_access=true --add-dir <worktree-git-dir> --add-dir <git-common-dir> exec <prompt>` (설치 CLI capability에 따라 지원되는 옵션만 사용).
- 현재 canonical workspace Git metadata: `git_dir=/Users/luna/workspace/devbot/.git/worktrees/issue-62`, `git_common_dir=/Users/luna/workspace/devbot/.git`, `top_level=/Users/luna/workspace/devbot/.worktrees/issue-62`.
- main synchronization policy: canonical Task branch와 `origin/main` fetch, clean worktree/PR head 확인, DevBot 단독 소유 branch는 rebase 우선, rewrite push는 `--force-with-lease`만 허용.
- review integration-validation method: REVIEW 준비는 `synchronize_with_main=False`로 PR head를 변경하지 않으며, `git merge-tree --write-tree origin/<main> HEAD`로 latest-main mergeability를 비변경 방식으로 검증한다.

## 남은 TODO와 제한

- PR #63 Evidence는 GitHub PR 본문에 반영했다.

## 위험 요소

- transient failure가 claim 이후 downstream write에서 발생하면 현재 cycle은 보류되고 다음 poll cycle의 GitHub 최신 상태를 기준으로 복구한다. 이 동작은 중복 side effect 방지에는 안전하지만, 기존에 이미 성공한 원격 write가 있었다면 다음 cycle이 최신 label state를 다시 읽는 것이 중요하다.

## Improvement Suggestions

- retry diagnostics를 observability의 JSON/structured event 스키마로 승격하면 운영 로그 필터링이 쉬워진다.
- `GitHubRetryConfig`를 config/env로 노출하면 운영 환경별 attempt/delay tuning이 가능하다.
