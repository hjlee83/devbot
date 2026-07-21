# Task 148 Result: Ignore DevBot Metadata During Workspace Cleanliness Validation

## 완료 내용

- `git status --porcelain` 결과를 공통 parser로 통과시켜 `.devbot/` 및 하위 경로를
  workspace dirty 판정에서 제외했다.
- `inspect_workspace()`와 `ensure_git_workspace_ready()`가 같은 필터를 사용하도록
  정리했다.
- ignored DevBot metadata path를 `devbot` logger debug level로 남긴다.
- `.gitignore`는 계속 Git의 porcelain status 결과에 위임한다.

## 주요 설계 결정

- `.devbot/`만 특별 취급하고 다른 untracked/tracked path는 기존처럼 dirty로 유지한다.
- 별도 파일 시스템 스캔을 추가하지 않고 `git status --porcelain` 출력만 필터링해
  `.gitignore` 동작을 보존한다.

## 수정 파일

- `src/devbot/workspace.py`
- `tests/test_workspace.py`
- `tests/test_startup.py`
- `tasks/148-ignore-devbot-metadata-cleanliness.md`
- `results/148-ignore-devbot-metadata-cleanliness.md`

## Checkpoint Evidence

- Metadata ignored:
  `test_devbot_metadata_changes_do_not_dirty_workspace`,
  `test_workspace_cleanliness_ignores_devbot_metadata`
- Real changes preserved:
  `test_devbot_metadata_filter_preserves_real_source_changes`,
  `test_workspace_cleanliness_still_reports_source_changes_with_devbot_metadata`
- `.gitignore` respected:
  `test_workspace_cleanliness_respects_gitignore`

## Validation 결과

- `uv run ruff check src/devbot/workspace.py tests/test_workspace.py tests/test_startup.py`
  - PASS
- `uv run pytest tests/test_workspace.py tests/test_startup.py::test_workspace_cleanliness_detects_dirty_workspace tests/test_startup.py::test_workspace_cleanliness_ignores_devbot_metadata tests/test_startup.py::test_workspace_cleanliness_still_reports_source_changes_with_devbot_metadata`
  - PASS, 14 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1443 passed in 137.44s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: dirty operator checkout은 dry-run에서 skip했고, `.devbot/`만 있던
    `hjlee83/vr-infinite-buy-tracker`는 `workspace_cleanliness`에서 `ok=True clean`으로
    보고됨. 현재 Task 변경 파일이 있는 `hjlee83/devbot`만 dirty로 보고됨.

## 수동 검증

- `inspect_workspace()`와 `ensure_git_workspace_ready()`가 동일한
  `_porcelain_changed_files()` 필터를 사용해 `.devbot/`만 있는 경우 clean으로 판단한다.
- Git porcelain status를 먼저 사용하므로 `.gitignore`에 의해 숨겨진 파일은 parser에
  들어오지 않는다.
- `.devbot/`과 source file이 함께 있을 때 `.devbot/`만 제외되고 source file은
  `changed_files`에 남는다.

## 남은 TODO와 제한

- 현재 세션의 `.git` 쓰기 권한 제한으로 branch 생성, commit, push, PR 생성은 아직 수행하지 못했다.

## 위험 요소

- `.devbot/` 아래 실제 사용자가 직접 관리하고 싶은 파일이 있어도 workspace cleanliness에서는
  DevBot runtime metadata로 간주되어 무시된다.

## Improvement Suggestions

- 향후 metadata ignore 목록이 늘어나면 상수화하고 doctor output에 ignored count를 노출할 수 있다.
