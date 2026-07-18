# Task 037 Result: Release Operator UX

## 완료 내용

- `devbot release preview|publish|status` CLI를 추가했다 (`src/devbot/main.py`의
  `_build_release_parser`/`_run_release_command`, 기존 `timeline`/`worktree`/`doctor`와
  동일하게 daemon `ProcessLock` 없이 실행되고 `main()`에서 `ProcessLock` 진입 전에
  분기된다).
- `src/devbot/release_ops.py`를 새로 만들어 GitHub 호출(읽기/쓰기)과 순수 계산을
  분리했다: `gather_release_context`(네트워크)와 `build_release_preview`(순수 함수,
  네트워크 호출 없이 단위 테스트 가능)로 나눴다.
- 다음 stable 버전은 마지막 stable Release 이후 병합된 **모든** main Pull Request를
  집계해 계산한다(`GitHubClient.compare_commits`로 커밋 구간을 구하고, 각 커밋의
  merged PR을 `get_commit_pull_request_metadata`로 조회, 기존
  `release_increment_for_pr`의 "정확히 하나의 `release:*` 라벨" 정책을 그대로
  재사용). 이전 stable Release가 전혀 없으면 `list_commits`로 전체 히스토리로
  fallback한다.
- `devbot.release.aggregate_release_notes()`를 추가해 한국어 섹션 다음 `---` 다음
  영어 섹션 순서로 병기된 Release Notes를 생성한다. 두 언어 섹션 모두 병합된 PR
  번호/제목/`release:*` 라벨에서만 생성되며, 동일한 PR 집합을 설명한다(자유
  텍스트 번역이나 지어낸 기능 설명 없음).
- `devbot release publish`는: 최신 검증된 `main` 커밋 탐지 → 다음 버전/증가폭 결정
  → Release Notes 생성 → 기존 `.github/workflows/release.yml`을
  `workflow_dispatch`로 실행(`GitHubWriteClient.dispatch_workflow`가 유일한 쓰기
  경로) → 실행 완료까지 폴링(`wait_for_dispatched_run`) → tag/Release/자산/
  `SHA256SUMS` 검증(`validate_published_release`) → 최종 보고 순서로 동작한다.
  태그나 Release를 직접 만드는 코드 경로는 존재하지 않는다.
- `release.yml`에 선택적 `notes` `workflow_dispatch` 입력을 추가했다.
  `scripts/release_pipeline.py plan --notes-file`이 비어있지 않을 때만 사용되고,
  없으면 기존 한 줄짜리 수동 dispatch 기본값(`manual_release_plan`)이 그대로
  유지된다 - GitHub Actions UI를 통한 기존 복구 경로는 영향받지 않는다.
- 안전 게이트(모두 GitHub에 아무것도 쓰기 전에 계산됨, `build_release_preview`):
  로컬 checkout dirty, 최신 `main` 커밋 미검증(check-runs 조회 실패도 포함),
  대상 커밋에 이미 stable Release 존재, 병합된 PR에 `release:*` 라벨 누락/중복,
  구간에 병합 PR을 찾을 수 없는 커밋 존재, 대상 변경 없음(모두 `release:none`),
  생성된 Release Notes가 비어 있음.
- `devbot release status`는 최신 stable 버전, 최신 Release 워크플로 실행,
  마지막 게시 커밋, 게시 상태(`never-run`/`in-progress`/`up-to-date`/`failed`)를
  읽기 전용으로 보여준다.

## 주요 설계 결정

- **`release.py`는 계속 순수하게 유지하고, GitHub 호출은 새 `release_ops.py`에
  분리했다.** `release.py`의 기존 모듈 docstring("Deterministic release pipeline
  policy helpers")과 일관되게, 네트워크 I/O가 필요한 로직만 새 모듈로 뺐다.
  `build_release_preview`는 이미 가져온 `ReleaseContext` 값만 받는 순수 함수라
  모킹 없이 테스트할 수 있다.
- **`workflow_dispatch`만 사용하고 tag/Release를 직접 만들지 않는다.**
  `GitHubWriteClient`에 추가한 메서드는 `dispatch_workflow` 하나뿐이며,
  `test_publish_release_never_calls_any_write_method_other_than_dispatch`가 이를
  고정한다. "기존 Release 워크플로를 절대 우회하지 않는다"는 요구사항을 코드
  구조 자체로 강제한다.
- **Release Notes를 `workflow_dispatch`의 새 `notes` 입력으로 전달한다** (게시 후
  `gh release edit`로 사후 수정하는 대신). Release가 처음 생성되는 순간부터 올바른
  병기 본문을 가지도록 하기 위함이며, 사후 편집이라는 별도의 안전 게이트가 필요한
  두 번째 쓰기 경로를 만들지 않기 위함이다. `manual_release_plan`은 override가
  없으면(즉 CLI를 거치지 않은 수동 dispatch) 기존 동작을 그대로 유지한다.
- **집계 다음 버전 계산은 기존 per-PR push 트리거 정책을 재사용한다.** 새로운 라벨
  의미를 만들지 않고, 구간 내 각 커밋마다 기존 `release_increment_for_pr`(정확히
  하나의 라벨 아니면 실패)을 그대로 호출한 뒤 최댓값(`major` > `minor` > `patch`)을
  취한다.
- **"최신 검증된 main 커밋"은 HEAD의 check-run이 모두 success일 때만 인정하고,
  더 오래된 초록색 커밋으로 fallback하지 않는다.** 실제로 검증되지 않은 최신
  커밋 위에 쌓인 변경을 조용히 건너뛰는 것을 막기 위한 명시적 선택이다.
- **check-runs 조회 자체가 실패하면(권한 등) "검증 실패"가 아니라 "검증할 수
  없음"으로 별도 표시한다.** 실제 라이브 저장소에서 검증하다 발견한 문제로,
  아래 "남은 TODO와 제한"에 상세 기록.

## 수정 파일

- `src/devbot/release.py`
- `src/devbot/release_ops.py` (신규)
- `src/devbot/github_client.py`
- `src/devbot/github_write_client.py`
- `src/devbot/main.py`
- `.github/workflows/release.yml`
- `scripts/release_pipeline.py`
- `tests/test_release.py`
- `tests/test_release_ops.py` (신규)
- `tests/test_main.py`
- `tests/test_github_client.py`
- `tests/test_github_write_client.py`
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `docs/07-decisions.md`
- `tasks/037-release-operator-ux.md`
- `results/037-release-operator-ux.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-037-1 preview는 읽기 전용, 전체 계획 계산 | `test_build_release_preview_ready_when_all_checks_pass`, `test_release_preview_command_is_wired`, `test_release_preview_reports_not_ready_as_failure` |
| CP-037-2 다음 버전은 마지막 stable Release 이후 병합 PR 집계 | `test_gather_release_context_computes_commit_range_from_compare_api`, `test_gather_release_context_falls_back_to_full_history_without_prior_release`, `test_build_release_preview_handles_first_release_bootstrap` |
| CP-037-3 한국어+영어 병기 결정적 Release Notes | `test_aggregate_release_notes_is_deterministic`, `test_aggregate_release_notes_has_korean_then_english_sections_in_the_same_order`, `test_aggregate_release_notes_korean_and_english_describe_the_same_prs`, `test_aggregate_release_notes_sections_are_deterministic_by_increment`, `test_aggregate_release_notes_requires_at_least_one_entry`, `test_build_release_preview_notes_describe_same_prs_in_both_languages` |
| CP-037-4 publish는 기존 Release 워크플로만 dispatch | `test_dispatch_release_sends_expected_inputs`, `test_publish_release_never_calls_any_write_method_other_than_dispatch`, `test_release_workflow_supports_optional_manual_notes_override`, `test_manual_release_plan_notes_override_is_used_verbatim`, `test_manual_release_plan_without_override_keeps_trivial_notes`, `test_release_pipeline_plan_command_uses_notes_file_override`, `test_release_pipeline_plan_command_ignores_empty_notes_file` |
| CP-037-5 모든 안전 조건에서 fail closed | `test_build_release_preview_blocks_on_dirty_checkout`, `test_build_release_preview_blocks_on_unvalidated_ci`, `test_build_release_preview_blocks_when_release_already_exists_for_target`, `test_build_release_preview_blocks_on_missing_release_label`, `test_build_release_preview_blocks_when_commit_has_no_associated_pr`, `test_build_release_preview_not_ready_when_no_eligible_changes`, `test_dispatch_release_refuses_when_not_ready`, `test_manual_release_plan_rejects_blank_notes_override`, `test_release_publish_refuses_when_not_ready`, `test_local_checkout_is_dirty_detects_clean_and_dirty_repo`, `test_local_checkout_is_dirty_returns_none_for_non_git_path` |
| CP-037-6 워크플로 대기 및 게시 결과 검증 | `test_wait_for_dispatched_run_polls_until_success`, `test_wait_for_dispatched_run_raises_on_failure_conclusion`, `test_wait_for_dispatched_run_times_out_waiting_for_run_to_appear`, `test_validate_published_release_success`, `test_validate_published_release_rejects_draft`, `test_validate_published_release_rejects_target_commit_mismatch`, `test_validate_published_release_rejects_asset_mismatch`, `test_validate_published_release_rejects_malformed_checksum_manifest`, `test_release_publish_dispatches_when_ready`, `test_release_publish_dry_run_never_dispatches` |
| CP-037-7 status는 읽기 전용 | `test_build_release_status_up_to_date`, `test_build_release_status_never_run`, `test_build_release_status_failed`, `test_build_release_status_in_progress`, `test_release_status_is_read_only` |
| CP-037-8 CLI 연결이 기존 서브커맨드 관례를 따름 | `test_release_command_does_not_acquire_daemon_lock` |
| CP-037-9 기존 자동/수동 릴리스 경로 호환 유지 | 기존 `tests/test_release.py` 전체 스위트 무변경 통과 |
| CP-037-10 문서와 근거 | 본 Result, `docs/00-roadmap.md`, `docs/08-beta-runbook.md`, `docs/07-decisions.md` |
| CP-037-11 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS, 609 passed
  (기존 563개 + 이번 Task에서 추가한 46개: `tests/test_release_ops.py` 28개,
  `tests/test_release.py` 11개, `tests/test_main.py` 7개)

## 수동 검증 결과

`hjlee83/devbot` 실제 저장소를 대상으로 읽기 전용/`--dry-run` 명령을 직접 실행했다
(`devbot release publish`를 real dry-run이 아닌 형태로는 실행하지 않았다 - 실제
Release는 게시하지 않았다).

- `uv run devbot release status` (실제 GitHub 상태, 아무것도 쓰지 않음):
  ```
  latest_stable_version: 0.1.0
  latest_release_url: https://github.com/hjlee83/devbot/releases/tag/v0.1.0
  latest_release_published_at: 2026-07-18T06:26:19+00:00
  last_published_commit: 4363221aaf7920999cd1fd27b99aaf99cd1a701e
  latest_workflow_run: https://github.com/hjlee83/devbot/actions/runs/29633940913 status=completed conclusion=success
  publication_state: up-to-date
  ```
  실제 v0.1.0 Release(태그/URL/게시 시각/target 커밋/워크플로 실행)와 정확히
  일치한다.
- `uv run devbot release preview` / `uv run devbot release publish --dry-run` (실제
  GitHub 상태, 아무것도 쓰지 않음): `previous_version: 0.1.0`, `next_version: 0.1.0`,
  `ready: no`, blocker 3개(로컬 checkout dirty - 이 Task를 작업 중인 checkout
  자체이므로 예상된 결과, check-runs 조회 실패, 대상 커밋에 이미 v0.1.0 존재)를
  정확히 보고하고 종료 코드 1로 끝났다. `devbot release publish`(dry-run)는
  `dispatch_workflow`를 호출하지 않았다(준비되지 않음 판정에서 이미 중단).

## 남은 TODO와 제한

- **발견한 배포 환경 이슈 (Task 037 범위 밖, 코드 버그 아님):** 이 저장소에 배포된
  `.env`의 `GITHUB_TOKEN`은 `GET /repos/{owner}/{repo}/commits/{ref}/check-runs`에
  대해 `403 Resource not accessible by personal access token`을 반환한다
  (`get_commit_sha`/`list_releases`/`list_commits`/`list_workflow_runs`는 모두
  정상 동작함 - check-runs 엔드포인트만 실패). 이 메서드(`list_check_runs_for_ref`)는
  Task 037 이전부터 존재했고 B2 자동 머지 안전 게이트도 동일 메서드에 의존하므로,
  자동 머지 게이트도 라이브 환경에서 같은 권한 문제를 겪을 가능성이 있다. 코드
  수정으로는 고칠 수 없는 토큰/배포 설정 문제이며, 운영자가 확인해야 한다.
  이번 Task는 이 실패를 처리 불가 크래시가 아니라 명확한 "검증할 수 없음"
  블로커로 바꾸는 방어 코드를 추가했다(`gather_release_context`의
  `target_commit_validation_error`).
- **`docs/history.md`의 기존 서술이 실제 상태와 다르다 (Task 037 범위 밖):**
  `docs/history.md`는 여전히 "No official stable GitHub Release has been published
  yet" / "pending operator-controlled publication"이라고 서술하지만, `v0.1.0`은
  실제로 2026-07-18에 게시되었다(위 `release status` 결과 참고). 이 문서를
  고치려면 `tests/test_release.py::test_history_marks_initial_release_pending_and_
  preserves_required_milestones`(Task 033/036이 고정한 테스트)를 함께 바꿔야 하고,
  이는 이 Task의 계약 범위(Task 032/033의 tag/artifact/publication 로직 변경 금지)
  밖이라고 판단해 손대지 않았다. 후속 Task에서 다뤄야 한다.
- `docs/00-roadmap.md`의 Task 030-036 항목이 이번 Task에서 함께 채워졌다(이전에는
  029 다음 036으로 건너뛰어 있었다) - Task 037 자체의 요구사항은 아니지만, 같은
  파일을 수정하는 김에 낮은 위험으로 정합성을 맞췄다.
- `devbot release publish`의 실제(비-dry-run) 실행은 검증하지 않았다 - 계약에서
  명시적으로 금지했다. 실제 워크플로 dispatch/대기/검증 경로는 mock을 사용한
  단위 테스트(`test_release_ops.py`)로만 검증했다.

## 위험 요소

- `list_check_runs_for_ref` 권한 문제가 실제로는 "체크가 없다"가 아니라 "확인할
  수 없다"인데, 두 경우 모두 현재는 게시를 막는다(안전한 방향의 fail-closed).
  다만 배포 토큰이 실제로 Checks 권한을 영구적으로 갖지 못한다면 `devbot release
  publish`가 라이브에서 항상 이 이유로만 막힐 수 있다 - 토큰 권한이 수정되기
  전까지는 운영자가 GitHub Actions UI에서 기존 수동 `workflow_dispatch` 경로를
  그대로 사용할 수 있다(이번 Task가 그 경로를 바꾸지 않았기 때문). 근본 원인
  진단(토큰 종류/권한 재발급)은 이 Task의 범위 밖이며 후속 조치가 필요하다.
- 대량의 병합 PR(수십~수백 개)이 쌓인 뒤 첫 `release preview`/`publish`를 실행하면
  커밋마다 `get_commit_pull_request_metadata` 호출이 발생해 API 호출 수가
  늘어난다 - 이 저장소 규모에서는 문제가 되지 않았지만, 원인/완화책으로만
  기록해 둔다.
