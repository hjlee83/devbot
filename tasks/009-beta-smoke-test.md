# Task 009: Beta Smoke Test

Version: 1.0.0
Last Updated: 2026-07-13

## 목표

Task 001~008에서 구현한 기능이 실제로 하나의 흐름으로 동작하는지 검증한다.

이번 Task에서는 새로운 기능을 추가하지 않는다.

기존 기능을 연결하고 운영 가능한 상태인지 확인하는 것이 목표다.

---

## 구현 범위

### 포함

- Dry-run 통합 테스트
- 전체 실행 흐름 검증
- Beta Runbook 작성
- 운영 체크리스트 작성
- 발견된 버그 수정
- Result 작성
- Pull Request 생성

### 제외

- Dashboard
- Multi Repository
- SQLite
- 새로운 Agent
- 새로운 기능

---

## 검증 대상

다음 흐름이 정상 동작해야 한다.

ready

↓

working

↓

Agent 실행

↓

검증

↓

Commit

↓

Push

↓

PR

↓

review

---

## 품질 게이트

| ID | 체크포인트 | 테스트 |
|---|---|---|
| CP-009-1 | Dry-run은 외부 변경이 없다 | test_dry_run_has_no_side_effect |
| CP-009-2 | Working이 있으면 새 작업을 시작하지 않는다 | test_skip_when_working_exists |
| CP-009-3 | Ready를 정상 선택한다 | test_select_ready_issue |
| CP-009-4 | AgentRunner를 호출한다 | test_agent_runner_called |
| CP-009-5 | 검증 성공 후 Commit/Push/PR 순서를 지킨다 | test_delivery_after_verification |
| CP-009-6 | 실패 시 Commit하지 않는다 | test_stop_delivery_when_failed |
| CP-009-7 | Review 상태로 변경된다 | test_move_to_review |
| CP-009-8 | 기존 PR을 재사용한다 | test_reuse_existing_pr |

---

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

---

## Result

작성 파일

results/009-beta-smoke-test.md

반드시 포함

- 구현 요약
- 테스트 결과
- 발견된 문제
- 수정 내용
- 운영 체크리스트
- 다음 Task 제안

---

## Reviewer Focus

- 실제 기능 추가가 없는가
- 기존 기능만 연결했는가
- Dry-run이 안전한가
- 운영 문서만으로 재현 가능한가

---

## 완료 조건

- 모든 테스트 PASS
- Ruff PASS
- Dry-run PASS
- Result 작성 완료
- PR 생성
- CI 성공
