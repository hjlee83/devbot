# Result: Task 004

## Status
- COMPLETE

## 요약
`tasks/004-workspace-agent.md` 범위대로, 안전한 로컬 Git 워크스페이스를
검증하고 dry-run 모드로 `AgentRunner`를 실행하는 로직을 구현했다.

- `workspace.py`에 `ensure_git_workspace_ready(repository)` 추가 — 경로 존재 → `.git` 존재 → `git status --porcelain`으로 uncommitted 변경 여부 순으로 검사하고, 위반 시 각각 다른 예외를 던진다.
- `workspace.py`에 `generate_branch_name(repository, issue_number, title)` 추가 — Issue 제목을 슬러그화해 결정적이고 Git ref로 안전한 브랜치 이름(`devbot/{repo}-{issue_number}-{slug}`)을 만든다.
- `workspace.py`에 `build_agent_prompt(repository, issue, unprocessed_comments, template_path=...)` 추가 — `prompts/issue-task.md`를 렌더링해 Issue 본문과 미처리 댓글을 포함한 프롬프트 문자열을 만든다.
- `agents/codex.py`의 `CodexRunner.run()`에 실제(비-dry-run) 실행 경로를 구현 — `subprocess.run(["codex", "exec", prompt], cwd=repository.local_path, ...)`을 호출한다. 단위 테스트는 `subprocess.run`을 mock해서 실제 프로세스를 띄우지 않는다.

## 수정 파일
- `src/devbot/models.py` — `IssueComment(author, body)` 추가
- `src/devbot/github_client.py` — `GitHubIssue`에 `body: str` 필드 추가(Task 002 산출물에 대한 최소 확장, 기존 테스트 fixture는 `body` 키가 없어도 빈 문자열로 안전하게 파싱됨)
- `src/devbot/workspace.py` — `NotAGitRepositoryError`/`DirtyWorkspaceError` 추가, `ensure_git_workspace_ready`/`generate_branch_name`/`build_agent_prompt` 추가(기존 `validate_repository_paths`/`WorkspaceValidationError`는 미변경)
- `src/devbot/agents/base.py` — `AgentRunResult`에 `returncode: int | None = None` 추가(기본값 있어 기존 생성 코드 호환)
- `src/devbot/agents/codex.py` — 비-dry-run 실행 경로 구현
- `prompts/issue-task.md` — `{{unprocessed_comments}}` 섹션 추가
- `tests/test_workspace.py` — 신규
- `tests/test_agents_codex.py` — 신규 테스트 2개 추가(기존 `test_codex_runner_dry_run_does_not_execute`는 미변경)
- `results/004-workspace-agent.md`, `results/004-workspace-agent-improvements.md`

## Checkpoint별 테스트 근거

| Checkpoint | 테스트 이름 | 결과 |
|---|---|---|
| CP-004-1 | `test_missing_repository_path_raises` | PASS |
| CP-004-2 | `test_non_git_directory_is_rejected` | PASS |
| CP-004-3 | `test_dirty_workspace_is_detected` (+ `test_clean_workspace_is_accepted`) | PASS |
| CP-004-4 | `test_branch_name_is_safe_and_deterministic` (+ `test_branch_name_differs_for_different_issues`) | PASS |
| CP-004-5 | `test_prompt_contains_issue_and_unprocessed_comments` (+ `test_prompt_without_comments_shows_placeholder`) | PASS |
| CP-004-6 | `test_runner_uses_target_repository_directory` | PASS |
| CP-004-7 | `test_agent_dry_run_does_not_execute_process` | PASS |

`tests/test_workspace.py` 8개, `tests/test_agents_codex.py` 신규 2개(기존 1개 포함 총 3개) 전부 PASS. 전체 스위트 40개 전부 PASS.

## 검증 결과

Task 004 문서에도 `uv run ruff check .` / `uv run pytest` 두 개만
명시되어 있지만, 이전 Task들과 동일하게 `AGENTS.md` §5 표준 절차를 따라
4개 모두 실행했다.

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (40 passed) |
| `uv run devbot` | PASS (exit 0) |

`tests/test_workspace.py`의 워크스페이스 검사 테스트는 실제 `git` CLI를
호출한다(`git init`/`config`/`add`/`commit`/`status`) — 이는 GitHub
네트워크 호출이 아니라 로컬 파일시스템 안에서만 동작하는 오프라인 Git
작업이라 "실제 네트워크 호출 없음" 원칙과 충돌하지 않는다.

## 남은 TODO
- `ensure_git_workspace_ready`/`generate_branch_name`/`build_agent_prompt`/`CodexRunner`는 아직 서로 연결되어 실제 워크플로를 이루지 않는다. Task 005(메인 폴링 루프)에서 "Issue 선택 → 워크스페이스 준비 → 브랜치 생성 → 프롬프트 생성 → 에이전트 실행" 파이프라인으로 연결될 것으로 예상.
- "미처리 댓글"을 실제로 GitHub에서 가져오고 무엇이 "미처리"인지 판단하는 로직은 여전히 미구현이다(Task 002는 댓글 조회 자체를 Out of scope로 명시했음). `build_agent_prompt`는 이미 필터링된 댓글 목록을 받는다는 전제로 구현했다.
- `generate_branch_name`이 실제로 브랜치를 생성/체크아웃하지는 않는다(이름만 계산). 실제 `git checkout -b` 호출은 이후 Task(007: push/PR)에서 다뤄질 것으로 예상.

## 위험 요소
- 비-dry-run `CodexRunner.run()`이 실행하는 정확한 명령(`codex exec <prompt>`)은 Task 문서에 명시되지 않아 임의로 정했다. 실제 `codex` CLI의 정확한 인터페이스와 다를 수 있으며, CP-004-6은 "cwd가 올바른지"만 검증하고 정확한 argv는 검증하지 않는다.
- `generate_branch_name`의 슬러그 길이 제한(40자)과 구분자 규칙은 Task에 명시되지 않아 임의로 정했다. 다른 값이 필요하면 이 함수만 수정하면 되는 국소적인 변경이다.
- 개선 제안은 `results/004-workspace-agent-improvements.md`에 별도 기록했으며, 구현 범위는 임의로 바꾸지 않았다.
