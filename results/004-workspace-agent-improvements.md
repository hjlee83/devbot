# Task 004 Improvement Suggestions

## 애매했던 요구사항

- "execute an AgentRunner in dry-run mode"라는 Goal 문구와, CP-004-6("Runner
  executes in target working directory")이 요구하는 비-dry-run 실행 경로
  검증이 표면적으로 충돌하는 것처럼 보였다. Task 001의 `codex.py`가
  "Real Codex execution is out of scope for Task 001"이라고 남겨둔 것과,
  CP-004-6/CP-004-7이 dry-run과 비-dry-run을 명확히 분리해서 요구하는 것을
  근거로, "실제 프로세스는 여전히 실행하지 않지만(mock으로 검증), 그
  경로의 구현 자체는 이번 Task에 포함된다"로 해석해 구현했다.
- 비-dry-run일 때 정확히 어떤 명령을 실행해야 하는지(`codex` CLI의 인자
  형식) Task에 명시되어 있지 않았다. `["codex", "exec", prompt]`로
  임의로 정했다 — CP-004-6은 `cwd`만 검증하고 정확한 명령 형식은
  검증하지 않으므로 테스트는 통과하지만, 실제 `codex` CLI 인터페이스와
  다를 수 있다.
- "Prompt includes Issue and unprocessed comments"에서 "unprocessed"를
  누가 판단하는지 명시되어 있지 않았다. `build_agent_prompt`는 이미
  필터링된 댓글 목록을 받아 그대로 렌더링만 한다는 쪽으로 범위를
  좁혔다 — "무엇이 미처리인가"를 판단하는 로직은 이후 Task(아마도 008:
  PR 피드백 루프)로 미뤘다.
- `GitHubIssue`에 `body` 필드가 없어서 CP-004-5를 구현할 수 없었다. Task
  002 산출물을 최소한으로 확장(필드 1개 추가)해 해결했다 — Task 002의
  기존 테스트(`tests/test_github_client.py`)는 수정하지 않고도 전부
  그대로 통과함을 확인했다(`raw.get("body") or ""`로 기존 fixture와
  호환).

## 추가하면 좋을 품질 게이트

- Task 004도 "Verification gates"에 2개만 명시(Task 002/003과 동일한
  패턴). 반복해서 보고 중이니 Task 문서 템플릿 자체에서 정리가
  필요해 보인다(이전 Task들의 개선 제안과 동일).
- `_has_uncommitted_changes`가 `git status --porcelain`만 확인하고
  스테이지된(staged) 변경과 스테이지 안 된 변경을 구분하지 않는다 —
  둘 다 "dirty"로 간주한다. 이게 의도와 맞는지, 아니면 "커밋되지 않은
  스테이지 변경은 허용"처럼 더 세밀한 정책이 필요한지 확인이 필요하다.

## 누락된 경계 조건

- `ensure_git_workspace_ready`가 `.git`이 파일인 경우(git worktree의
  `.git`은 디렉터리가 아니라 파일일 수 있음)를 별도로 테스트하지
  않았다. 현재 구현은 `.exists()`만 확인하므로 worktree도 통과하지만,
  명시적으로 검증된 동작은 아니다.
- `generate_branch_name`에 제목이 완전히 특수문자로만 이뤄진 경우
  (슬러그가 빈 문자열이 되는 경우) `"issue"`로 대체하는 fallback을
  넣었지만, 이 경계를 직접 검증하는 테스트는 추가하지 않았다.

## 다음 Task에 반영할 제안

- Task 005(메인 폴링 루프)에서 이번에 만든 함수들
  (`ensure_git_workspace_ready` → `generate_branch_name` →
  `build_agent_prompt` → `CodexRunner.run`)을 실제로 순서대로 연결하는
  지점과, 그 앞단에서 `select_global_ready_task`(Task 003)가 고른
  `IssueTask`를 어떻게 `GitHubIssue`/댓글 목록과 다시 연결할지(둘 다
  결국 같은 Issue를 가리켜야 함) Task 문서에 명시해주면 좋겠다.
- 비-dry-run 시 실행할 정확한 `codex` CLI 명령 형식을 Task 문서나
  `docs/04-agent-system.md`에 명시해주면, 지금처럼 임의로 정하지 않아도
  된다.
