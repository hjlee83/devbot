# Result: Task 002

## Status
- COMPLETE

## 요약
`tasks/002-github-read-client.md` 범위대로 GitHub REST API에 대한
인증된 읽기 전용 클라이언트를 구현했다. `requests`를 사용해 실제 네트워크
호출을 수행하되(운영 시), 단위 테스트는 `requests.Session`을 목(mock)으로
주입해 실제 네트워크 호출 없이 검증한다.

- `get_authenticated_user()` — `GET /user`를 호출해 `GitHubUser(login, id)`로 변환
- `list_issues()` — `GET /repos/{owner}/{repo}/issues`를 `state`/`labels` 필터와 함께 호출하고, 응답 페이지 크기가 `per_page` 미만이 될 때까지 페이지네이션을 따라가 전체 Issue를 수집. GitHub Issues API가 함께 반환하는 Pull Request 항목(`pull_request` 키 존재)은 결과에서 제외
- 에러는 `GitHubClientError` 계층(`GitHubAuthenticationError` 401, `GitHubNotFoundError` 404, `GitHubAPIError` 그 외)으로 변환되어, 호출자는 원본 `requests` 예외를 다룰 필요가 없음
- `GitHubClient`는 읽기 전용 메서드(`get_authenticated_user`, `list_issues`)만 공개하며, 쓰기 계열 메서드는 전혀 존재하지 않음(라벨 변경/댓글/PR 생성은 Out of scope)

## 수정 파일
- `pyproject.toml` — `requests` 의존성 추가
- `src/devbot/github_client.py` — 전면 재작성: `GitHubClient`, `GitHubUser`, `GitHubIssue`, `GitHubClientError`/`GitHubAuthenticationError`/`GitHubNotFoundError`/`GitHubAPIError` (Task 001의 미구현 skeleton을 대체)
- `tests/test_github_client.py` — 신규
- `README.md` — `github_client.py` 설명을 실제 구현에 맞게 갱신
- `results/002-github-read-client.md` — 본 문서
- `results/002-github-read-client-improvements.md` — 개선 제안

## Checkpoint별 테스트 근거

| Checkpoint | 테스트 이름 | 결과 |
|---|---|---|
| CP-002-1 | `test_get_authenticated_user` | PASS |
| CP-002-2 | `test_list_issues_follows_pagination` | PASS |
| CP-002-3 | `test_list_issues_applies_state_and_label_filters` | PASS |
| CP-002-4 | `test_github_error_is_translated` (추가 경계 테스트: `test_github_authentication_error_is_translated`, `test_github_generic_error_is_translated`, `test_github_client_error_hierarchy`) | PASS |
| CP-002-5 | `test_client_exposes_read_operations_only` | PASS |

`tests/test_github_client.py` 8개 전부 PASS. 전체 스위트(기존 15개 + 신규 8개) 23개 전부 PASS.

## 검증 결과

Task 002 문서에는 `uv run ruff check .`, `uv run pytest` 두 개만 명시되어
있지만, `AGENTS.md` §5(구현 AI SOP)와 `templates/result-template.md`의
기본 검증 표는 4개 명령을 요구하므로 회귀 확인을 위해 4개 모두 실행했다.

| Command | Result |
|---|---|
| `uv sync` | PASS (`requests` 및 하위 의존성 설치 확인) |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (23 passed) |
| `uv run devbot` | PASS (exit 0) — Task 002는 `main.py`/CLI를 변경하지 않았으므로 회귀 없음을 재확인하는 목적 |

## 남은 TODO
- `GitHubClient`는 아직 `config.py`/`main.py`에 연결되지 않았다(`GITHUB_TOKEN` 등 설정 항목 없음). Task 003(전역 큐 선택)에서 `GitHubIssue`(GitHub 원본 상태/라벨)를 `IssueTask`(`TaskState`/`Priority`)로 변환하는 매핑 로직과 함께 실제로 연결될 것으로 예상.
- 라벨 변경, 댓글, PR 생성 등 쓰기 작업은 이후 Task(006~008)에서 다룬다.

## 위험 요소
- GitHub REST API 응답의 `created_at`은 항상 `...Z` 형식의 UTC ISO-8601이라고 가정하고 `Z` → `+00:00`으로 치환해 파싱한다. GitHub가 이 형식을 바꾸지 않는 한 안전하지만, 다른 포맷이 오면 `ValueError`가 그대로 전파된다(별도 도메인 에러로 감싸지 않음).
- 페이지네이션 종료 조건으로 `Link` 헤더 대신 "응답 페이지 크기 < `per_page`"를 사용한다. GitHub가 정확히 `per_page` 배수만큼의 Issue를 반환하는 마지막 페이지에서는 빈 페이지를 한 번 더 요청하게 되어 API 호출이 1회 추가될 수 있다(정확성에는 문제없음, 효율성 측면의 사소한 트레이드오프).
- 개선 제안은 `results/002-github-read-client-improvements.md`에 별도 기록했으며, 이번 구현 범위를 임의로 바꾸지 않았다.
