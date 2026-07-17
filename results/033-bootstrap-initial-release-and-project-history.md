# Task 033 Result: Bootstrap Initial Release and Project History

## 완료 내용

- `tasks/033-bootstrap-initial-release-and-project-history.md` 계약서를 추가했다.
- `docs/history.md`를 추가해 Task 032까지의 canonical project history와 future
  Release Notes format을 기록했다.
- `src/devbot/release.py`에 첫 안정 릴리스 노트 helper와 표준 섹션 상수를
  추가했다.
- Task 033 검증 테스트를 `tests/test_release.py`에 추가했다.
- 로컬에서 Task 032 portable Python artifact와 `SHA256SUMS`를 생성하고,
  패키지된 `devbot --version` smoke를 수행했다.

## 주요 설계 결정

- 첫 안정 릴리스 기준 commit은 현재 검증된 `main` head인
  `6526cfea27e4a5fd0003df6eb698cd202dedcf57`로 고정했다.
- 권위 버전은 `pyproject.toml`의 `0.1.0`을 사용했다.
- 과거 prerelease 태그 `v0.1.0-alpha.1`은 stable Release 기준으로 사용하지
  않는다.
- future Release Notes section order는 코드 상수와 문서 양쪽에 고정했다.
- 현재 실행 환경 지시가 원격 discovery와 GitHub publication을 금지하므로, 실제
  GitHub Release publish는 수행하지 않고 operator-controlled remaining step으로
  기록했다.

## 수정 파일

- `src/devbot/release.py`
- `tests/test_release.py`
- `tasks/033-bootstrap-initial-release-and-project-history.md`
- `docs/history.md`
- `results/033-bootstrap-initial-release-and-project-history.md`

## Release Evidence

- Source commit: `6526cfea27e4a5fd0003df6eb698cd202dedcf57`
- Version: `0.1.0`
- Tag: `v0.1.0`
- Artifact: `devbot-0.1.0-portable-python.tar.gz`
- SHA-256:
  `8a2a30bb78f1f06da0b80519efc13aa7003a055d93a4896aa2aa38deb6d7401a`
- Intended Release URL: `https://github.com/hjlee83/devbot/releases/tag/v0.1.0`
- Local prior version tag evidence: `v0.1.0-alpha.1` only; no local stable tag was
  present.

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-033-1 initial stable version | `test_first_stable_release_uses_authoritative_initial_version_and_artifact_contract` |
| CP-033-2 stable Release safety | `test_initial_release_rejects_prior_stable_release_or_moved_tag` |
| CP-033-3 artifact/checksum evidence | `test_first_stable_release_uses_authoritative_initial_version_and_artifact_contract`, existing artifact/checksum/package CLI tests |
| CP-033-4 Release Notes standard | `test_initial_release_notes_use_standard_future_sections` |
| CP-033-5 canonical history | `docs/history.md`, `test_initial_release_notes_use_standard_future_sections` |

## Validation 결과

- `git rev-parse HEAD`: PASS,
  `6526cfea27e4a5fd0003df6eb698cd202dedcf57`
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py version --project-root .`: PASS, `0.1.0`
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py build-artifact --version 0.1.0 --os-name portable --architecture python --output-dir /tmp/devbot-task033-release --project-root .`: PASS
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py checksum-manifest --version 0.1.0 --artifact-dir /tmp/devbot-task033-release --output /tmp/devbot-task033-release/SHA256SUMS`: PASS
- `/tmp/devbot-task033-smoke/devbot-release/bin/devbot --version`: PASS,
  `devbot 0.1.0`
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv sync`: PASS
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run pytest tests/test_release.py tests/test_task_contract_docs.py -q`: PASS, 39 passed
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run pytest -q`: PASS, 523 passed
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache WORKSPACE_ROOT=/tmp/devbot-task033-doctor-runtime DEVBOT_REPOSITORIES_PATH=/tmp/devbot-task033-doctor-runtime/repositories.yaml DEVBOT_LOCK_FILE=/tmp/devbot-task033-doctor-runtime/devbot.lock GITHUB_TOKEN=dummy uv run devbot doctor --ci`: PASS exit 0, `safe_to_start: yes`; dummy token caused expected `github_connectivity` bad-credentials diagnostic.
- `UV_CACHE_DIR=/tmp/devbot-task033-uv-cache WORKSPACE_ROOT=/tmp/devbot-task033-runtime DEVBOT_REPOSITORIES_PATH=/tmp/devbot-task033-runtime/repositories.yaml DEVBOT_LOCK_FILE=/tmp/devbot-task033-runtime/devbot.lock GITHUB_TOKEN=dummy uv run devbot --once --dry-run`: PASS, `no_managed_repositories`

## 수동 검증 결과

- Artifact file size: `114935` bytes
- `SHA256SUMS` content:

```text
8a2a30bb78f1f06da0b80519efc13aa7003a055d93a4896aa2aa38deb6d7401a  devbot-0.1.0-portable-python.tar.gz
```

- Initial Release Notes are recorded in `docs/history.md`.
- Future Release Notes sections:
  - What's New
  - Improvements
  - Fixes
  - Operational Changes
  - Upgrade Notes
  - Known Limitations

## 남은 TODO와 제한

- 실제 GitHub Release 생성과 asset upload는 현재 prompt가 `gh`, `curl`, remote
  discovery, publication 명령을 금지하므로 수행하지 않았다.
- 운영자는 merge 후 Task 032 pipeline 또는 controlled manual dispatch로
  `v0.1.0` stable Release를 publish해야 한다.

## 위험 요소

- 실제 publication 시점에 GitHub 원격에 stable `v0.1.0` Release나 tag가 이미
  생겨 있으면 Task 032 pipeline은 fail-closed해야 한다.
- 현재 artifact/checksum은 로컬 재현 evidence다. 실제 GitHub Release asset은
  publish workflow에서 같은 source commit으로 다시 생성/검증되어야 한다.

## Improvement Suggestions

- Runtime Release discovery와 automatic update는 다음 Task에서 `docs/history.md`
  Release metadata를 참조하지 말고 GitHub Releases API와 artifact metadata를
  authoritative source로 사용해야 한다.
