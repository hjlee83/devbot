# Task 003 Improvement Suggestions

## 애매했던 요구사항

- "Disabled repositories are ignored"(CP-003-5)가 정확히 어느 단계에서
  적용되는지 명시되어 있지 않았다. 두 가지 해석이 가능하다.
  1. (이번 구현이 채택한 해석) 비활성화된 저장소의 Task는 blocking 판정
     포함 전체 파이프라인에서 완전히 제외된다 — 마치 애초에 존재하지
     않았던 것처럼.
  2. blocking 판정(working/review)에는 여전히 포함되고, `ready` 후보
     선택 단계에서만 제외된다.
  "저장소가 비활성화됐다면 DevBot이 애초에 그 저장소에서 작업을 시작하지
  않았을 것"이라는 시스템 관점에서 1번이 더 일관적이라고 판단해 채택했고,
  `test_disabled_repository_active_task_does_not_block_selection` 테스트로
  이 해석을 명시적으로 검증해뒀다. 의도와 다르면 `select_global_ready_task`
  의 필터링 순서만 바꾸면 되는 국소적인 수정이다.
- Task 003은 `IssueTask`(`TaskState`/`Priority`가 이미 부여된 상태)를
  입력으로 받는다고 전제했다. GitHub 원문 라벨(`devbot:*`/`priority:*`)을
  `IssueTask`로 변환하는 매핑 로직은 Task 002 개선 제안에서 이미 다음
  Task로 미뤄뒀던 것과 동일한 이유로 이번에도 범위에 포함하지 않았다.

## 추가하면 좋을 품질 게이트

- Task 003도 Task 002와 마찬가지로 "Verification gates"에
  `uv run ruff check .` / `uv run pytest` 두 개만 명시돼 있어 `AGENTS.md`
  §5의 4개 표준 명령과 매번 불일치한다. Task 문서 템플릿
  (`docs/05-task-format.md`)에 검증 명령 섹션을 아예 생략하고 "AGENTS.md
  §5의 표준 절차를 따른다"는 문구로 통일하면, Task마다 반복해서 이 격차를
  보고할 필요가 없어질 것 같다.

## 누락된 경계 조건

- `select_global_ready_task`에 빈 `repositories` 리스트를 넘기는 경우(관리
  대상 저장소가 하나도 없는 상태)는 별도로 테스트하지 않았다 — 현재 구현상
  `enabled_repository_names`가 빈 집합이 되어 모든 Task가 걸러지고 항상
  `None`을 반환하므로 `test_no_ready_issue_returns_none`과 동일한 경로를
  타긴 하지만, 명시적인 회귀 테스트는 아니다.

## 다음 Task에 반영할 제안

- Task 005(메인 폴링 루프)에서 `select_global_ready_task`를 실제로
  호출하는 지점과, 그 전에 `GitHubIssue` → `IssueTask` 변환이 이뤄지는
  모듈(예: `queue.py`에 변환 함수를 추가할지, 별도 모듈을 만들지)을 Task
  문서에 명시해주면 좋겠다.
- "비활성화된 저장소는 완전히 무시" 해석이 맞는지 다음 Task 리뷰에서
  확인해주면 좋겠다 — 틀렸다면 지금 고치는 게 이후 Task에서 이 동작에
  의존하는 코드가 늘어나기 전이라 더 저렴하다.
