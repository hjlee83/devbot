# Task 129 Result: Bulk Worktree Cleanup

## 완료 내용

- `WorktreeManager.cleanup_stale()`를 추가했다.
- `devbot worktree cleanup --stale` CLI를 추가했다.
- 기존 `devbot worktree cleanup --issue <N>` 경로는 유지하고, `--issue`와 `--stale`를 mutually exclusive target으로 만들었다.
- `devbot doctor`의 `worktree_health[...]` detail에 stale worktree가 있으면 `devbot worktree cleanup --stale` 힌트를 출력한다.

## 주요 설계 결정

- 자동 정리는 도입하지 않았다. cleanup은 계속 명시적 operator command다.
- bulk cleanup은 Git이 `prunable`로 표시한 worktree만 대상으로 한다. 이 기준은 기존 `WorktreeManager.health()`가 stale로 보고하던 기준과 동일하다.
- GitHub Issue closed/done 상태 조회 기반 정리는 이번 범위에서 제외했다. 네트워크 상태와 GitHub 라벨 상태를 cleanup 판단에 섞지 않고, 로컬 Git이 이미 prunable로 판단한 항목만 prune한다.

## 수정 파일

- `src/devbot/worktree.py`
- `src/devbot/main.py`
- `src/devbot/doctor.py`
- `tests/test_worktree.py`
- `tests/test_main.py`
- `tests/test_doctor.py`
- `docs/13-host-managed-workspace-preparation.md`
- `docs/00-roadmap.md`
- `tasks/129-bulk-worktree-cleanup.md`
- `results/129-bulk-worktree-cleanup.md`

## 테스트 Evidence

- `test_cleanup_stale_prunes_only_git_prunable_worktrees`
- `test_cleanup_stale_returns_empty_when_no_prunable_worktrees`
- `test_worktree_cleanup_stale_command_is_wired`
- `test_worktree_health_reports_stale_cleanup_hint`

## Validation 결과

```bash
$ uv run ruff check .
All checks passed!

$ uv run pytest tests/test_worktree.py tests/test_main.py::test_worktree_cleanup_stale_command_is_wired tests/test_doctor.py::test_worktree_health_reports_stale_cleanup_hint tests/test_task_contract_docs.py
43 passed in 17.74s

$ uv run pytest
1375 passed in 223.29s
```

## 남은 TODO와 제한

- GitHub closed/done Issue 조회 기반 cleanup은 아직 없다.
- daemon polling 중 자동 cleanup은 없다.
- 활성 worktree, dirty worktree, 실패 Job 보존 정책은 유지된다.
