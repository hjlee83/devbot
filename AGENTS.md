# DevBot AGENTS

Version: 1.0.0
Last Updated: 2026-07-13

> 이 문서는 DevBot 프로젝트의 최상위 운영 규칙이다.
> 모든 AI(Claude Code, Codex 등)는 작업을 시작하기 전에 반드시 이 문서를 먼저 읽는다.

---

# 1. 언어 정책

사람이 읽는 모든 출력은 반드시 한국어로 작성한다.

## 한국어 대상

- Task 결과
- Result 문서
- PR 제목 및 설명
- PR 리뷰
- Issue 댓글
- Commit 설명
- 최종 보고
- 개선 제안
- 에러 원인 설명

## 영어 사용 허용

- 소스코드
- 클래스명
- 함수명
- 변수명
- 테스트 함수명
- API 이름
- GitHub Label
- Branch 이름
- Conventional Commit Type (feat, fix, refactor...)

---

# 2. 개발 환경

필수 환경

- Python 3.13
- uv
- pytest
- ruff

모든 공개 API는 타입 힌트를 작성한다.

프로젝트는 다음 환경을 모두 지원해야 한다.

- macOS
- Linux VPS

---

# 3. 안전 규칙

- Secret, Token, API Key를 Commit하지 않는다.
- 기본 실행 모드는 DRY_RUN=true 이다.
- main/master 브랜치에는 직접 Commit하거나 Push하지 않는다.
- 항상 작업 브랜치를 사용한다.
- 검증이 실패한 상태에서는 Task를 완료로 판단하지 않는다.

---

# 4. Context Loading 순서

새로운 작업을 시작하면 반드시 아래 순서로 Context를 읽는다.

1. AGENTS.md
2. docs/
3. 이전 Task의 Result
4. 현재 Task
5. Source Code

이 순서를 변경하지 않는다.

---

# 5. 구현 AI 표준 절차 (SOP)

새로운 Task를 시작하면 반드시 아래 절차를 수행한다.

1. AGENTS.md 읽기
2. docs 읽기
3. 이전 Result 읽기
4. 현재 Task 읽기
5. Task 범위만 구현
6. Task의 모든 품질 게이트를 만족하는 단위 테스트 작성
7. Task에 정의된 필수 테스트 이름은 변경하지 않는다.
8. 아래 검증 명령을 모두 수행한다.

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot
```

9. 실패한 검증이 있으면 수정 후 다시 검증한다.
10. results 문서를 작성한다.
11. 작업 브랜치에 Commit한다.
12. Push한다.
13. Pull Request를 생성한다.
14. 개선 사항은 Result 문서의 Improvement Suggestions 섹션에 기록한다.

---

# 6. 품질 게이트

- 모든 Checkpoint를 만족해야 한다.
- 각 Checkpoint에는 최소 하나 이상의 테스트가 존재해야 한다.
- 테스트는 실제 동작을 검증해야 한다.
- 반드시 아래를 포함한다.

- Happy Path
- Failure Path
- Boundary Condition

필요하면 Regression Test를 추가한다.

---

# 7. Result 작성 규칙

모든 Task는 반드시 results 폴더에 Result 문서를 작성한다.

반드시 포함한다.

- 완료 내용
- 수정 파일
- Checkpoint별 테스트
- 검증 결과
- TODO
- 위험 요소
- Improvement Suggestions

---

# 8. 리뷰 AI 표준 절차 (SOP)

Pull Request를 리뷰할 때 반드시 아래 순서를 따른다.

1. AGENTS.md
2. 현재 Task
3. Result
4. Pull Request

그리고 아래 항목을 확인한다.

- 계약 범위
- 품질 게이트
- 필수 테스트
- 테스트 품질
- CI
- 보안
- 문서

---

# 9. 리뷰 기준

다음을 모두 만족해야 한다.

- Task 범위를 벗어나지 않았다.
- 모든 Checkpoint에 대응하는 테스트가 존재한다.
- 테스트가 실제 동작을 검증한다.
- Happy Path를 검증한다.
- Failure Path를 검증한다.
- Boundary Condition을 검증한다.
- Result 문서가 최신이다.
- PR Evidence가 최신이다.
- CI가 성공했다.

---

# 10. 리뷰 결과

리뷰 AI는 Pull Request를 Merge하지 않는다.

반드시 아래 형식으로 종료한다.

# Review Summary

## 상태

- MERGE READY

또는

- REQUEST CHANGES

## 필수 포함 항목

- 계약 검토
- 품질 게이트
- 테스트
- CI
- 보안
- 문서
- Blocker
- Warning
- 비고

최종 Merge는 사람이 수행한다.

---

# 11. Prompt Contract

## 구현 AI

사용자가

```
Task-XXX 수행해.
```

라고 요청하면 추가 설명을 요구하지 말고 구현 SOP를 수행한다.

Task가 모호한 경우에만 질문한다.

---

## 리뷰 AI

사용자가

```
PR-XXX 리뷰해.
```

라고 요청하면 리뷰 SOP를 수행한다.

Merge하지 않고 Review Summary를 작성한다.

---

# 12. 문서 규칙

- 구현과 문서는 항상 함께 수정한다.
- 설계 변경은 docs에 기록한다.
- 중요한 설계 결정은 docs/07-decisions.md에 기록한다.
- Task를 수정하면 Result도 함께 수정한다.

---

# 13. DevBot 철학

- Task는 계약이다.
- Checkpoint는 품질 게이트이다.
- Test는 계약을 증명한다.
- Result는 다음 작업자를 위한 인수인계 문서이다.
- Review는 계약 준수 여부를 검증한다.
- 모든 AI는 동일한 규칙을 따른다.
- 사람은 최종 Merge만 수행한다.
