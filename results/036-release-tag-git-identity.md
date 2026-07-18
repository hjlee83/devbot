# Task 036 Result: Release Tag Git Identity

## 완료 내용

- Release workflow의 `Create immutable tag` 단계에서 annotated tag 생성 전에 Git committer identity를 설정하도록 수정했다.
- 설정한 identity는 GitHub Actions bot 표준 값이다:
  - `github-actions[bot]`
  - `41898282+github-actions[bot]@users.noreply.github.com`
- `git tag --annotate`보다 identity 설정이 먼저 실행되는지 검증하는 workflow regression test를 추가했다.
- v0.1.0 tag나 GitHub Release는 생성하지 않았다.

## 주요 설계 결정

- Git identity는 tag 생성이 필요한 `Create immutable tag` step 안에서만 local repository config로 설정한다.
- immutable tag, exact target commit, draft-first publication, idempotent retry, manual workaround 금지 정책은 변경하지 않았다.
- Release workflow 재실행은 이 PR merge 이후 새 main commit으로 수행해야 한다.

## 수정 파일

- `.github/workflows/release.yml`
- `tests/test_release.py`
- `tasks/036-release-tag-git-identity.md`
- `results/036-release-tag-git-identity.md`
- `docs/00-roadmap.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-036-1 Git identity before annotated tag | `test_release_workflow_configures_git_identity_before_annotated_tag` |
| CP-036-2 release safety preserved | Existing release workflow tests in `tests/test_release.py` |
| CP-036-3 documentation and evidence | This Result, Task contract, roadmap entry |
| CP-036-4 validation gate | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `UV_CACHE_DIR=/private/tmp/devbot-task036-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task036-uv-cache uv run pytest`: PASS, 563 passed in 172.37s

## 수동 검증 결과

- PR URL: https://github.com/hjlee83/devbot/pull/78
- `v0.1.0` tag/Release 수동 생성 없음

## 남은 TODO와 제한

- PR merge 후 새 main commit SHA로 기존 Release workflow를 다시 dispatch해야 한다.
- 이 Task는 failed release workflow의 tag identity 문제만 수정한다.

## 위험 요소

- GitHub Actions runner policy가 Git identity 또는 annotated tag 생성 동작을 변경하면 release workflow를 다시 검증해야 한다.
