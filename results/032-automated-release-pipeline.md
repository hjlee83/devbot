# Task 032 Result: Automated Release Pipeline

## 완료 내용

- `src/devbot/release.py`에 릴리스 정책의 순수 함수 모델을 추가했다.
- `devbot --version`이 새 authoritative version helper를 통하도록 연결했다.
- `.github/workflows/release.yml`을 추가해 `main` push와 `workflow_dispatch`에서 validation → plan → artifact build → draft Release → stable publication 순서로 실행되도록 했다.
- `scripts/release_pipeline.py`를 추가해 workflow가 버전 조회, release plan 산출, artifact 생성, checksum manifest 생성을 동일한 로컬 코드로 수행하게 했다.
- `tests/test_release.py`에 Task 032 필수 테스트와 workflow fixture 검증을 추가했다.

## 주요 설계 결정

- 권위 버전 소스는 `pyproject.toml`의 `[project].version`이다. 설치된 CLI는 package metadata를 읽고, source-tree release tooling은 같은 값을 `pyproject.toml`에서 읽는다.
- 릴리스 정책은 GitHub API 호출과 분리된 pure helper로 작성했다. tag 중복, release 중복, semantic version 계산, artifact naming, checksum manifest, release notes를 로컬 테스트로 검증할 수 있다.
- `plan-release`는 merge commit에 연결된 PR metadata와 기존 stable Release 목록을 입력으로 사용한다. `release:none`은 publish=false로 종료하고, 누락/충돌 release label은 fail-closed로 처리한다.
- Artifact naming contract는 `devbot-<version>-portable-python.tar.gz`로 고정했다.
- Artifact는 현재 Task 033의 launcher/updater가 소비할 metadata와 `bin/devbot --version` fixture를 포함하는 deterministic tarball로 구성했다. gzip/tar metadata 시간을 고정해 동일 입력의 artifact byte가 재현된다.

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
| CP-032-6 artifacts | `test_release_artifact_names_are_deterministic`, `test_release_artifact_generation_is_reproducible`; workflow matrix covers portable Python |
| CP-032-7 embedded metadata | `test_packaged_cli_reports_release_version` |
| CP-032-8 checksum manifest | `test_checksum_manifest_covers_every_release_artifact`, `test_checksum_manifest_is_deterministic` |
| CP-032-9 atomic publication | workflow verifies artifacts/checksums before `gh release create --draft`, then publishes with `gh release edit --draft=false` |
| CP-032-10 release notes | `test_release_note_generation_is_deterministic`; workflow passes generated notes through `--notes-file`; sample: `- minor: #67 Task 032: Automated Release Pipeline` |
| CP-032-11 permissions | workflow top-level `contents: read`; release write permission only on `publish-release`; no PAT required |
| CP-032-12 concurrency/idempotency | workflow `concurrency.group: release-${{ github.repository }}`; `test_release_rerun_is_idempotent_for_same_commit` |
| CP-032-13 manual dispatch | workflow `workflow_dispatch` inputs `increment` and `commit_sha`; manual job rejects non-main commits and uses the same planner/artifact/checksum/publication path |
| CP-032-14 auditability | `test_safe_summary_fixture_contains_audit_fields_without_credentials`; workflow writes source commit/version/tag/artifacts/checksums/URL |
| CP-032-15 compatibility | full `uv run pytest` passed; release workflow has no `pull_request` trigger |

## Claude Reviewer Authentication Regression Fix

- `AgentExecutionContext.safe_environment()` now preserves the minimal user-level operating-system and provider authentication environment required by child Agents: `HOME`, `PATH`, temp directory variables, user identity variables, XDG config/data variables, and provider config-location variables when present.
- The normalized `DEVBOT_*` execution variables are still injected into the same child process environment.
- `AgentLauncher` exposes the effective child environment for non-task readiness checks without logging environment values.
- `devbot doctor` now checks Claude authentication readiness with `claude auth status` using the same launcher environment shape as real Agent execution, without running a review task.
- No credentials are logged and no credential files are copied into worktrees.

Regression evidence:

- `tests/test_agent_execution.py::test_agent_environment_preserves_user_auth_context`
- `tests/test_agent_execution.py::test_claude_execution_inherits_home_and_prepared_workspace`
- `tests/test_agent_execution.py::test_agent_execution_diagnostics_are_complete_and_redacted`
- `tests/test_doctor.py::test_doctor_checks_claude_auth_with_launcher_environment`

## Release Artifact Contract Rework

재리뷰 Blocker 대응으로 Release asset 계약을 platform-specific native binary에서 portable Python artifact로 수정했다.

- `SUPPORTED_PLATFORMS`는 `portable/python` 단일 artifact 계약으로 정리했다.
- artifact 이름은 `devbot-<version>-portable-python.tar.gz`이다.
- artifact 내부에는 실제 `src/devbot` package code, `pyproject.toml`, `metadata.json`, `bin/devbot` launcher가 포함된다.
- launcher는 `DEVBOT_RELEASE_VERSION`과 `PYTHONPATH`를 설정한 뒤 `devbot.main.main()`을 실행하므로 `--version`뿐 아니라 실제 CLI parser/code path를 사용한다.
- `authoritative_version()`은 release artifact 안에서는 `DEVBOT_RELEASE_VERSION`을 우선해 Release tag, artifact metadata, runtime `devbot --version`을 일치시킨다.
- workflow는 가짜 portable Python matrix를 제거하고 portable artifact 1개를 build한다.
- workflow build 단계는 artifact를 추출한 뒤 `bin/devbot --version`과 `bin/devbot --help` smoke를 실행한다.

Evidence:

- `test_release_artifact_names_are_deterministic`
- `test_release_artifact_contains_real_package_code`
- `test_release_version_override_unifies_runtime_version`
- `test_packaged_cli_reports_release_version`
- `test_release_workflow_matrix_and_manual_dispatch_are_declared`

## Validation 결과

- `UV_CACHE_DIR=/tmp/devbot-uv-cache uv sync`: PASS
- `UV_CACHE_DIR=/tmp/devbot-uv-cache uv run ruff check .`: PASS
- `uv run pytest`: PASS, 488 passed in the local writable workspace for the launcher/auth regression subset plus existing suite; CI on PR #67 remains the branch-level validation source for Task 032 release files.
- `UV_CACHE_DIR=/tmp/devbot-uv-cache WORKSPACE_ROOT=/tmp/devbot-task032-workspace DEVBOT_REPOSITORIES_PATH=/tmp/devbot-task032-workspace/repositories.yaml DEVBOT_LOCK_FILE=/tmp/devbot-task032-workspace/devbot.lock GITHUB_TOKEN=dummy uv run devbot --once --dry-run`: PASS, `no_managed_repositories`
- `uv run devbot doctor`: PASS. Claude reviewer readiness reports `auth_ready=True` using launcher-equivalent environment.

## 수동 검증 결과

- `uv run python scripts/release_pipeline.py version --project-root .`: `0.1.0`
- `scripts/release_pipeline.py plan` fixture: PASS
  - previous version `0.2.0`, `release:patch` PR metadata → new version `0.2.1`, tag `v0.2.1`
- 대표 artifact 생성: PASS
  - `devbot-0.2.0-portable-python.tar.gz`
  - 추출 후 `bin/devbot --version` 및 `bin/devbot --help` smoke 검증
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
