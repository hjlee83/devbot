# Task 035 Result: Release Smoke Uses Managed Python Path

## 완료 내용

- Release workflow의 `Smoke packaged DevBot` 단계에서 `uv python find 3.13`으로 GitHub Actions managed Python 3.13 실행 파일을 찾도록 수정했다.
- 패키지 launcher 실행 시 해당 Python 실행 파일의 directory를 `PATH` 앞에 붙여 `python3.13` 검증을 통과하도록 했다.
- Linux와 macOS matrix job이 동일한 smoke command를 사용하도록 유지했다.
- 워크플로우 회귀 테스트에 managed Python PATH 설정 검증을 추가했다.

## 주요 설계 결정

- Artifact launcher 계약은 변경하지 않았다. 설치된 artifact는 여전히 runtime에서 `PATH`의 Python 3.13을 요구한다.
- `uv`는 GitHub Actions smoke 환경에서만 Python 위치 확인에 사용하고, artifact runtime dependency로 추가하지 않았다.
- Release publication, tag 생성, artifact content, checksum publication 흐름은 변경하지 않았다.

## 수정 파일

- `.github/workflows/release.yml`
- `tests/test_release.py`
- `tasks/035-release-smoke-managed-python-path.md`
- `results/035-release-smoke-managed-python-path.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-035-1 managed Python smoke PATH | `test_release_workflow_uses_platform_artifact_matrix_and_manual_dispatch` |
| CP-035-2 launcher contract preserved | Launcher/source artifact builder 변경 없음 |
| CP-035-3 no release publication | `v0.1.0` tag/Release 수동 생성 없음 |
| CP-035-4 validation gate | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `UV_CACHE_DIR=/private/tmp/devbot-task035-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task035-uv-cache uv run pytest`: PASS, 562 passed in 166.12s

## 수동 검증 결과

- PR 생성 후 GitHub 링크 확인: Pending
- `git ls-remote --tags origin refs/tags/v0.1.0 refs/tags/v0.1.0^{}`: no output, tag absent
- `gh release view v0.1.0 --json tagName,url,isDraft,isPrerelease,targetCommitish`: `release not found`

## 남은 TODO와 제한

- PR merge 후 새 main commit으로 기존 Release workflow를 다시 dispatch해야 한다.
- 이 Task는 workflow smoke 환경만 수정하므로, release publication은 수행하지 않는다.

## 위험 요소

- GitHub Actions runner의 `setup-uv` 동작이 바뀌면 `uv python find 3.13` 결과 확인 방식도 재검토해야 한다.
