# Task 032 Result: Automated Release Pipeline

## 완료 내용

- `src/devbot/release.py`에 릴리스 정책의 순수 함수 모델을 추가했다.
- `devbot --version`이 새 authoritative version helper를 통하도록 연결했다.
- `.github/workflows/release.yml`을 추가해 `main` push와 `workflow_dispatch`에서 validation → plan → artifact build → draft Release → stable publication 순서로 실행되도록 했다.
- `scripts/release_pipeline.py`를 추가해 workflow가 버전 조회, release plan 산출, platform-specific artifact 생성, checksum manifest 생성을 동일한 로컬 코드로 수행하게 했다.
- `tests/test_release.py`에 Task 032 필수 테스트와 workflow fixture 검증을 추가했다.

## 주요 설계 결정

- 권위 버전 소스는 `pyproject.toml`의 `[project].version`이다. 설치된 CLI는 package metadata를 읽고, source-tree release tooling은 같은 값을 `pyproject.toml`에서 읽는다.
- 릴리스 정책은 GitHub API 호출과 분리된 pure helper로 작성했다. tag 중복, release 중복, semantic version 계산, artifact naming, checksum manifest, release notes를 로컬 테스트로 검증할 수 있다.
- `plan-release`는 대상 commit SHA를 명시 입력으로 받아 merge commit에 연결된 PR metadata와 기존 Release 목록을 검증한다. 같은 target commit에 stable/draft Release가 이미 있으면 새 버전을 증가시키지 않고 기존 version/tag를 재사용한다. `release:none`은 publish=false로 종료하고, 누락/충돌 release label은 fail-closed로 처리한다.
- Artifact naming contract는 updater와 일치하는 platform-specific artifact `devbot-<version>-linux-x86_64.tar.gz` and `devbot-<version>-macos-arm64.tar.gz`로 고정했다.
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
| CP-032-2 release intent | `test_release_increment_is_selected_from_merged_pr_label`, `test_missing_or_conflicting_release_labels_fail_closed`, `test_release_none_skips_publication`, `test_release_pipeline_closed_unmerged_pr_is_not_treated_as_merged`, `test_release_pipeline_plan_command_writes_github_outputs` |
| CP-032-3 next version | `test_next_semantic_version_is_calculated_from_latest_stable_tag`, `test_prerelease_draft_and_malformed_tags_are_ignored`, `test_release_plan_uses_pr_label_and_latest_stable_release`, `test_release_plan_reuses_existing_stable_release_for_target_commit`, `test_release_plan_bumps_next_commit_after_existing_release` |
| CP-032-4 validation gate | `test_release_workflow_structure_enforces_validation_before_publication`; workflow `publish-release` needs `validate-main` |
| CP-032-5 safe tag/release | `test_release_tag_targets_validated_main_commit`, `test_duplicate_tag_or_release_is_rejected_without_mutation` |
| CP-032-6 artifacts | `test_release_artifact_names_are_deterministic`, `test_release_artifact_generation_is_reproducible`; workflow builds linux/x86_64 and macos/arm64 artifact without an OS/architecture matrix |
| CP-032-7 embedded metadata | `test_packaged_cli_reports_release_version` |
| CP-032-8 checksum manifest | `test_checksum_manifest_covers_every_release_artifact`, `test_checksum_manifest_is_deterministic` |
| CP-032-9 atomic publication | workflow verifies artifacts/checksums before `gh release create --draft`, then publishes with `gh release edit --draft=false` |
| CP-032-10 release notes | `test_release_note_generation_is_deterministic`; workflow passes generated notes through `--notes-file`; sample: `- minor: #67 Task 032: Automated Release Pipeline` |
| CP-032-11 permissions | workflow top-level `contents: read`; release write permission only on `publish-release`; no PAT required |
| CP-032-12 concurrency/idempotency | workflow `concurrency.group: release-${{ github.repository }}`; `test_release_plan_reuses_existing_stable_release_for_target_commit`; `test_release_plan_recovers_existing_draft_release_for_target_commit`; `test_release_rerun_is_idempotent_for_same_commit`; `test_release_workflow_resumes_partial_publication_states` |
| CP-032-13 manual dispatch | workflow `workflow_dispatch` inputs `increment` and `commit_sha`; manual job rejects non-main commits with `git merge-base --is-ancestor` after full-history checkout and uses the same planner/artifact/checksum/publication path; `test_release_workflow_uses_full_history_target_commit_plan_and_ancestry_check` |
| CP-032-14 auditability | `test_safe_summary_fixture_contains_audit_fields_without_credentials`; workflow writes source commit/version/tag/artifacts/checksums/URL |
| CP-032-15 compatibility | full `uv run pytest` passed; release workflow has no `pull_request` trigger |

## 재리뷰 Blocker 대응: Portable Release Artifact와 Version Consistency

- release workflow를 실제 platform-specific artifact matrix로 정렬했다.
- `build_artifact()`는 실제 `src/devbot` 패키지 코드와 `pyproject.toml`을 tarball에 포함한다.
- Artifact 생성 시 계산된 release version을 artifact 내부 `pyproject.toml`에 주입한다.
- Artifact launcher는 `DEVBOT_PROJECT_ROOT`와 `PYTHONPATH`를 artifact 내부 source tree로 설정해 실제 `devbot.main`을 실행한다.
- Release workflow는 artifact extract 후 `bin/devbot --version` smoke를 수행한다.
- 검증 테스트는 artifact 안에 `src/devbot/main.py`, `src/devbot/release.py`, release version이 주입된 `pyproject.toml`이 들어 있고 실제 package code 기반 launcher가 계산된 version을 출력함을 확인한다.

## 재리뷰 Blocker 대응: CI Doctor Profile

- `devbot doctor --ci`를 추가했다.
- CI profile은 repository/GitHub/config/worktree/release validation에 필요한 checks는 유지하고, GitHub-hosted runner에 없는 Agent executable 및 사용자 로그인 readiness checks만 생략한다.
- 일반 `devbot doctor`는 기존 Codex/Claude executable/auth readiness checks를 유지한다.
- Release workflow의 `validate-main`은 `uv run devbot doctor --ci`를 사용한다.
- `doctor --ci`는 지정 commit checkout 검증용이므로 startup self-update를 수행하지 않는다.

## 재리뷰 Blocker 대응: Release Publication Idempotency

- `release_rerun_result()`가 동일 commit의 기존 tag/release 상태를 `already-published`, `resume-with-tag`, `resume-draft`, `create`로 분류한다.
- `publish-release` workflow는 기존 tag가 같은 commit을 가리키면 재사용하고, 다른 commit이면 fail-closed한다.
- tag만 있고 Release가 없으면 기존 tag로 draft Release 생성을 계속한다.
- draft Release가 있으면 `gh release upload --clobber`로 누락/교체 asset과 checksum을 보완한 뒤 stable publish를 재개한다.
- stable Release가 이미 있고 expected artifact와 `SHA256SUMS`가 모두 있으면 `already-published`로 성공 처리한다.
- stable Release가 불완전하거나 tag/release target이 충돌하면 fail-closed한다.

## 재리뷰 Blocker 대응: Target Commit Plan Idempotency

- `scripts/release_pipeline.py plan`은 이제 `--target-commit`을 필수 입력으로 받는다.
- PR 기반 release planning은 PR `merge_commit_sha`가 target commit과 정확히 일치하지 않으면 fail-closed한다.
- `pull_request.state == "closed"`만으로 merged로 판단하지 않고, `merged == true` 또는 `merged_at`만 병합 evidence로 사용한다.
- 같은 target commit에 stable Release가 이미 있으면 publish=false로 기존 version/tag를 반환해 동일 commit 재실행이 새 patch version을 만들지 않는다.
- 같은 target commit에 draft Release가 있으면 기존 version/tag로 publish=true를 반환해 partial publication을 이어간다.
- 다음 commit은 기존 stable Release를 base로 새 version을 계산한다.
- `workflow_dispatch`의 main 포함 검증은 shallow `git branch -r --contains` 대신 full-history checkout 후 `git merge-base --is-ancestor`를 사용한다.

## Validation 결과

- `UV_CACHE_DIR=/tmp/devbot-pr67-uv-cache uv sync`: PASS
- `UV_CACHE_DIR=/tmp/devbot-pr67-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/tmp/devbot-pr67-uv-cache uv run pytest -q`: PASS, 520 passed
- `UV_CACHE_DIR=/tmp/devbot-pr67-uv-cache WORKSPACE_ROOT=/tmp/devbot-pr67-runtime DEVBOT_REPOSITORIES_PATH=/tmp/devbot-pr67-runtime/repositories.yaml DEVBOT_LOCK_FILE=/tmp/devbot-pr67-runtime/devbot.lock GITHUB_TOKEN=dummy uv run devbot --once --dry-run`: PASS, `no_managed_repositories`
- `UV_CACHE_DIR=/tmp/devbot-pr67-uv-cache WORKSPACE_ROOT=/tmp/devbot-pr67-doctor-runtime DEVBOT_REPOSITORIES_PATH=/tmp/devbot-pr67-doctor-runtime/repositories.yaml DEVBOT_LOCK_FILE=/tmp/devbot-pr67-doctor-runtime/devbot.lock GITHUB_TOKEN=$(gh auth token) uv run devbot doctor --ci`: PASS, `safe_to_start: yes`. Agent executable/auth checks are skipped in CI profile while the report remains concise and secret-safe.
- `uv run devbot doctor`: NOT RUN TO PASS in PR checkout. 일반 doctor는 startup self-update 정책상 operator checkout이 clean `main`이어야 하므로 Task branch 검증에서는 `doctor --ci`를 사용했다; dirty PR checkout에서 실행 시 `operator checkout dirty`로 fail-closed함을 확인했다.

## 수동 검증 결과

- `uv run python scripts/release_pipeline.py version --project-root .`: `0.1.0`
- `scripts/release_pipeline.py plan` fixture: PASS
  - target commit `new`, previous version `0.2.0`, `release:patch` PR metadata → new version `0.2.1`, tag `v0.2.1`
  - same target commit existing stable Release → existing version/tag reused, publish=false
  - same target commit existing draft Release → existing version/tag reused, publish=true
- 대표 artifact 생성: PASS
  - `devbot-0.2.0-linux-x86_64.tar.gz` and `devbot-0.2.0-macos-arm64.tar.gz`
- checksum manifest 생성: PASS
  - platform-specific artifact의 SHA-256 항목 생성 확인
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

## 위험 요소

- workflow의 tag/release 생성 단계는 GitHub 원격 상태에 의존하므로 post-merge 첫 실행에서 permissions와 branch containment를 반드시 확인해야 한다.
- artifact는 Task 033 소비 계약을 위한 deterministic portable Python package이며, user-machine installer/updater itself는 Task 033 범위다.

## Improvement Suggestions

- `devbot doctor`에 startup self-update skip 플래그나 환경 변수를 추가하면 CI/로컬 검증이 네트워크 정책과 덜 충돌한다.
- release planning script가 GitHub event payload와 release list JSON을 직접 받아 `next_version()`까지 출력하도록 확장하면 workflow shell logic을 더 줄일 수 있다.
