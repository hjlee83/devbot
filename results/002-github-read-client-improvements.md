# Task 002 Improvement Suggestions

## 애매했던 요구사항

- "conversion into typed models"이 정확히 어떤 모델을 가리키는지 Task에
  명시되어 있지 않았다. `models.py`에는 이미 `IssueTask`(`TaskState`,
  `Priority` 포함)가 있지만, 이는 devbot의 내부 워크플로 상태(라벨
  `devbot:*`/`priority:*`를 해석한 결과)이지 GitHub API 응답 자체의 상태가
  아니다. `docs/00-roadmap.md`에 "Task 003: Global queue selection"이
  별도로 명시돼 있어, Task 002는 GitHub 원본 리소스(Issue의 `state`,
  `labels` 원문)를 타입 있는 모델(`GitHubIssue`)로만 변환하고, 그것을
  `IssueTask`/`TaskState`/`Priority`로 매핑하는 로직은 Task 003 몫으로
  남겨두는 것으로 해석해 구현했다. Task 003에서 이 경계가 맞는지 확인이
  필요하다.
- `list_issues`가 받는 `repository` 인자 타입이 명시되어 있지 않았다.
  기존 코드베이스 전반에서 이미 쓰이는 `models.RepositoryConfig`를
  재사용했다(owner/repo 문자열을 따로 받지 않음).
- GitHub Issues API가 Pull Request도 함께 반환한다는 GitHub API 자체의
  특성을 Task 문서가 언급하지 않았다. "Issue retrieval"이라는 문구를
  근거로 PR 항목(`pull_request` 키가 있는 응답)은 걸러내도록 구현했다.

## 추가하면 좋을 품질 게이트

- Task 002의 "Verification gates"에는 `uv run ruff check .` /
  `uv run pytest` 두 개만 적혀 있어 `AGENTS.md` §5의 4개 표준 명령과
  불일치했다. 이번엔 4개를 모두 실행하고 보고했지만, Task 문서 작성 시
  `AGENTS.md`의 표준 4개 명령과 항상 맞추거나, 의도적으로 줄인 경우 그
  이유를 Task에 남겨주면 Task별 문서가 상위 SOP와 매번 어긋나는 걸
  방지할 수 있다.
- 에러 변환(CP-002-4)에 대해 "clear domain error"라는 표현만 있고 구체적인
  예외 타입/계층 구조 요구는 없었다. 이번엔 `GitHubAuthenticationError`
  (401), `GitHubNotFoundError`(404), `GitHubAPIError`(그 외)로 나눴는데,
  이후 Task에서 이 예외들을 실제로 구분해서 처리할 일이 생기면(예: 인증
  실패 시 재시도 금지, rate limit 시 백오프) 이 분류가 그대로 재사용
  가능한지 Task 003+에서 확인이 필요하다.

## 누락된 경계 조건

- 페이지네이션 종료 조건을 GitHub의 `Link` 헤더(`rel="next"`) 대신
  "응답 페이지 크기 < `per_page`"로 구현했다. 이 방식은 GitHub Issues
  API가 실제로 `per_page` 단위로 잘라서 응답한다는 전제에 의존하며, 마지막
  페이지가 정확히 `per_page` 개수와 같을 경우 빈 페이지를 한 번 더
  요청하는 비효율이 있다(결과 정확성에는 영향 없음).
- `created_at` 파싱은 GitHub REST API의 표준 UTC(`...Z`) 포맷만
  가정한다. 다른 포맷의 타임스탬프가 오면 `ValueError`가 그대로
  전파되고 `GitHubClientError` 계열로 감싸지지 않는다.

## 다음 Task에 반영할 제안

- Task 003(전역 큐 선택)에서 `GitHubIssue.labels`(원문 라벨 문자열)를
  `devbot:*`/`priority:*` 라벨 규칙(`docs/02-workflow.md`)에 따라
  `TaskState`/`Priority`로 매핑하는 함수의 소유 모듈(예: `queue.py` vs
  새 변환 모듈)을 Task 문서에 명시해주면 좋겠다.
- `GITHUB_TOKEN`을 `config.py`의 필수/선택 값으로 언제 편입시킬지(Task
  003에서 `GitHubClient`를 실제로 생성해 쓰기 시작하는 시점일 가능성이
  높음) Task 003에 명시해주면 좋겠다.
