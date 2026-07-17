# Task 032 Result: Automated Release Pipeline

## 완료 내용

- `src/devbot/release.py`에 릴리스 정책의 순수 함수 모델을 추가했다.
- `devbot --version`이 새 authoritative version helper를 통하도록 연결했다.
- `.github/workflows/release.yml`을 추가해 `main` push와 `workflow_dispatch`에서 validation → plan → artifact build → draft Release → stable publication 순서로 실행되도록 했다.
- `scripts/release_pipeline.py`를 추가해 workflow가 버전 조회, release plan 산출, portable Python artifact 생성, checksum manifest 생성을 동일한 로컬 코드로 수행하게 했다.
- `tests/test_release.py`에 Task 032 필수 테스트와 workflow fixture 검증을 추가했다.

## 주요 설계 결정

- 권위 버전 소스는 `pyproject.toml`의 `[project].version`이다. 설치된 CLI는 package metadata를 읽고, source-tree release tooling은 같은 값을 `pyproject.toml`에서 읽는다.
- 릴리스 정책은 GitHub API 호출과 분리된 pure helper로 작성했다. tag 중복, release 중복, semantic version 계산, artifact naming, checksum manifest, release notes를 로컬 테스트로 검증할 수 있다.
- `plan-release`는 merge commit에 연결된 PR metadata와 기존 stable Release 목록을 입력으로 사용한다. `release:none`은 publish=false로 종료하고, 누락/충돌 release label은 fail-closed로 처리한다.
- Artifact naming contract는 updater와 일치하는 portable Python artifact `devbot-<version>-portable-python.tar.gz`로 고정했다.
- Artifact는 실제 `src/` 패키지 코드, version이 주입된 `pyproject.toml`, `uv.lock`, metadata, `bin/devbot` launcher를 포함하는 deterministic tarball로 구성했다. gzip/tar metadata 시간을 고정해 동일 입력의 artifact byte가 재현된다.

## 수정 파일

- `.github/workflows/release.yml`
- `scripts/release_pipeline.py`
- `src/devbot/main.py`
- `src/devbot/release.py`
- `tests/test_main.py`
- `tests/test_release.py`
- `results/032-automated-release-pipeline.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-032-1 authoritative version | `test_runtime_and_package_version_use_authoritative_version_source`, `test_release_tag_and_embedded_version_must_match` |
| CP-032-2 release intent | `test_release_increment_is_selected_from_merged_pr_label`, `test_missing_or_conflicting_release_labels_fail_closed`, `test_release_none_skips_publication`, `test_release_pipeline_plan_command_writes_github_outputs` |
| CP-032-3 next version | `test_next_semantic_version_is_calculated_from_latest_stable_tag`, `test_prerelease_draft_and_malformed_tags_are_ignored`, `test_release_plan_uses_pr_label_and_latest_stable_release` |
| CP-032-4 validation gate | `test_release_workflow_structure_enforces_validation_before_publication`; workflow `publish-release` needs `validate-main` |
| CP-032-5 safe tag/release | `test_release_tag_targets_validated_main_commit`, `test_duplicate_tag_or_release_is_rejected_without_mutation` |
| CP-032-6 artifacts | `test_release_artifact_names_are_deterministic`, `test_release_artifact_generation_is_reproducible`; workflow matrix covers macOS/Linux arm64/x86_64 |
| CP-032-7 embedded metadata | `test_packaged_cli_reports_release_version` |
| CP-032-8 checksum manifest | `test_checksum_manifest_covers_every_release_artifact`, `test_checksum_manifest_is_deterministic` |
| CP-032-9 atomic publication | workflow verifies artifacts/checksums before `gh release create --draft`, then publishes with `gh release edit --draft=false` |
| CP-032-10 release notes | `test_release_note_generation_is_deterministic`; workflow passes generated notes through `--notes-file`; sample: `- minor: #67 Task 032: Automated Release Pipeline` |
| CP-032-11 permissions | workflow top-level `contents: read`; release write permission only on `publish-release`; no PAT required |
| CP-032-12 concurrency/idempotency | workflow `concurrency.group: release-${{ github.repository }}`; `test_release_rerun_is_idempotent_for_same_commit` |
| CP-032-13 manual dispatch | workflow `workflow_dispatch` inputs `increment` and `commit_sha`; manual job rejects non-main commits and uses the same planner/artifact/checksum/publication path |
| CP-032-14 auditability | `test_safe_summary_fixture_contains_audit_fields_without_credentials`; workflow writes source commit/version/tag/artifacts/checksums/URL |
| CP-032-15 compatibility | full `uv run pytest` passed; release workflow has no `pull_request` trigger |

## 재리뷰 Blocker 대응: Portable Release Artifact와 Version Consistency

- OS/architecture별 가짜 shell-script artifact matrix를 제거하고 portable Python artifact 하나로 계약을 변경했다.
- `build_artifact()`는 실제 `src/devbot` 패키지 코드와 `pyproject.toml`을 tarball에 포함한다.
- Artifact 생성 시 계산된 release version을 artifact 내부 `pyproject.toml`에 주입한다.
- Artifact launcher는 `DEVBOT_PROJECT_ROOT`와 `PYTHONPATH`를 artifact 내부 source tree로 설정해 실제 `devbot.main`을 실행한다.
- Release workflow는 artifact extract 후 `bin/devbot --version` smoke를 수행한다.
- 검증 테스트는 artifact 안에 `src/devbot/main.py`, `src/devbot/release.py`, release version이 주입된 `pyproject.toml`이 들어 있고 실제 package code 기반 launcher가 계산된 version을 출력함을 확인한다.

## Validation 결과

- `UV_CACHE_DIR=/tmp/devbot-uv-cache uv sync`: PASS
- `UV_CACHE_DIR=/tmp/devbot-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/tmp/devbot-task032-uv-cache UV_PROJECT_ENVIRONMENT=/tmp/devbot-task032-fix-98351-venv uv run pytest`: PASS, 507 passed
- `UV_CACHE_DIR=/tmp/devbot-uv-cache WORKSPACE_ROOT=/tmp/devbot-task032-workspace DEVBOT_REPOSITORIES_PATH=/tmp/devbot-task032-workspace/repositories.yaml DEVBOT_LOCK_FILE=/tmp/devbot-task032-workspace/devbot.lock GITHUB_TOKEN=dummy uv run devbot --once --dry-run`: PASS, `no_managed_repositories`
- `uv run devbot doctor`: NOT RUN. 이번 Task prompt가 `git fetch`, `gh`, `curl` 등 원격 discovery/network 명령 실행을 금지했고, 현재 `doctor`는 startup self-update 경로에서 원격 확인을 수행할 수 있어 로컬 실행하지 않았다.

## 수동 검증 결과

- `uv run python scripts/release_pipeline.py version --project-root .`: `0.1.0`
- `scripts/release_pipeline.py plan` fixture: PASS
  - previous version `0.2.0`, `release:patch` PR metadata → new version `0.2.1`, tag `v0.2.1`
- 대표 artifact 생성: PASS
  - `devbot-0.2.0-portable-python.tar.gz`
- checksum manifest 생성: PASS
  - 모든 4개 artifact의 SHA-256 항목 생성 확인
- release-note generation: PASS
  - `## devbot 0.2.0`
  - `- minor: #67 Task 032: Automated Release Pipeline`
- duplicate-release rejection: PASS
  - `test_duplicate_tag_or_release_is_rejected_without_mutation`
  - workflow publication 전에 existing tag와 existing Release를 조회해 overwrite/duplicate를 거부
- draft-to-publish workflow structure: PASS
  - artifact/checksum verification 후 draft Release 생성, 이후 stable publication
- 실제 GitHub tag/Release publication은 수행하지 않았다. 첫 stable Release는 merge 후 `main` commit에서 운영 검증해야 한다.

## 남은 TODO와 제한

- 실제 GitHub Release 생성은 PR merge 후 `main`에서만 검증 가능하다.
- `devbot doctor`가 startup self-update를 항상 실행하는 현재 동작은 로컬/offline 검증에 취약하다. release workflow에서는 GitHub Actions token과 checkout context에서 실행된다.

## 위험 요소

- workflow의 tag/release 생성 단계는 GitHub 원격 상태에 의존하므로 post-merge 첫 실행에서 permissions와 branch containment를 반드시 확인해야 한다.
- artifact는 Task 033 소비 계약을 위한 deterministic package fixture이며, 아직 user-machine installer나 updater는 포함하지 않는다.

## Improvement Suggestions

- `devbot doctor`에 startup self-update skip 플래그나 환경 변수를 추가하면 CI/로컬 검증이 네트워크 정책과 덜 충돌한다.
- release planning script가 GitHub event payload와 release list JSON을 직접 받아 `next_version()`까지 출력하도록 확장하면 workflow shell logic을 더 줄일 수 있다.
