# Task 017 Result: GitHub Status Timeline Protocol

## 완료 내용

- `docs/10-github-status-timeline.md`를 신규 작성해 GitHub Status Timeline Protocol을
  확정했다.
  - GitHub Issue/PR/Label/Comment/Check/Commit을 유일한 source of truth로,
    VPS/로컬 DevBot 로그는 장애 분석용 보조 자료로만 정의했다(2절).
  - 상태 라벨 `devbot:ready`/`devbot:working`/`devbot:review`/`devbot:manual-action`/
    `devbot:blocked`/`devbot:done`의 의미를 정리했다(3절). `devbot:working`은 dev/review
    phase를 모두 표현하므로 phase 구분은 반드시 timeline marker에 의존해야 함을 명시했다.
  - hidden HTML comment marker 형식 `devbot-timeline:v1`을 확정했다. 필드는
    `issue`, `pr`(optional), `cycle`, `phase`, `event`, `result`, `at`이며, 파서는
    알 수 없는 추가 필드를 무시하고 순서에 의존하지 않아야 한다(4절).
  - 필수 이벤트 `ready`(phase=queue), `dev:start`, `dev:end`, `review:start`,
    `review:end`를 정의했다(5절).
  - 시간 구간 계산 규칙을 정의했다: 완료된 구간은 시작/종료/소요 시간, 진행 중인
    구간은 시작/`now`/경과 시간, phase 사이 gap(`Queue`, `Wait reviewer`,
    `Wait implementer`)은 별도 waiting 구간으로 표시한다(6절).
  - 사람이 읽는 Status Card 표준 형식을 정의하고, 진행 중/rework 중/완료 3가지
    상태의 예시를 포함했다(7절): `#<issue> / PR #<pr>`, `State`, `Waiting`, `Queue`,
    cycle별 `Dev`/`Wait reviewer`/`Review`/`Wait implementer`/`Result`,
    `Total active`, `Total waiting`, `Total elapsed`.
  - marker가 아직 자동 기록되지 않는 구간은 GitHub 타임스탬프 기반 최선 추정임을
    답변에 명시하도록 했다(8절).
  - 후속 구현 범위(marker 자동 기록, `devbot status` CLI, status card 자동 업데이트,
    Dashboard/UI, VPS 로그 수집, Worktree Manager, Agent registry/failover, 상태
    라벨 추가/삭제)를 이 Task 범위 밖으로 명시했다(9절).
- `AGENTS.md`에 "15. 상태 질문 응답 규칙" 절을 추가했다. "현재 상태" 질문은
  GitHub 기준으로 답하고, VPS/로컬 로그는 명시적 요청 시에만 참고하며, 답변은
  `docs/10-github-status-timeline.md`의 Status Card 형식을 따르도록 했다. 문서
  Version을 1.1.0 → 1.2.0으로, Last Updated를 2026-07-15로 갱신했다.
- `docs/00-roadmap.md`의 Task 017 항목을 `[ ]` → `[x]`로 갱신하고 확정된 산출물
  경로와 후속 Task 범위를 덧붙였다.
- 문서 검증을 위한 정적 테스트 `tests/test_task_contract_docs.py`를 신규 작성했다.
  Task 계약의 필수 테스트 이름 4개와, CP-017-9/CP-017-10을 커버하는 보조 테스트
  2개를 포함한다.
- 기존 자동화 구현(`src/devbot/*`)은 전혀 변경하지 않았다 — 이 Task는 프로토콜/문서
  범위로 한정했다.

## 수정 파일

- `AGENTS.md`
- `docs/00-roadmap.md`
- `docs/10-github-status-timeline.md` (신규)
- `results/017-github-status-timeline-protocol.md` (신규, 이 문서)
- `tests/test_task_contract_docs.py` (신규)

## Checkpoint별 테스트

- CP-017-1: `test_task_017_protocol_doc_exists`
- CP-017-2: `test_task_017_protocol_documents_required_markers`
- CP-017-3: `test_task_017_protocol_documents_required_markers`
- CP-017-4: `test_task_017_protocol_documents_required_markers`
- CP-017-5: `test_task_017_status_card_requires_start_end_duration`
- CP-017-6: `test_task_017_protocol_documents_required_markers`
- CP-017-7: `test_task_017_protocol_documents_required_markers`
- CP-017-8: `test_task_017_agents_status_rule_is_documented`
- CP-017-9: `test_task_017_roadmap_updated`
- CP-017-10: `test_task_017_result_doc_exists` (이 Result 문서 자체가 확정된 marker
  형식/상태 카드 예시/후속 구현 범위를 기록)

## 검증 결과

- `uv sync`: 통과 (Resolved 15 packages, Checked 14 packages)
- `uv run ruff check .`: 통과 (`All checks passed!`)
- `uv run pytest`: 통과, 255 passed (Task 017 신규 테스트 6개 포함, 기존 249개 회귀 없음)
- `uv run devbot --once --dry-run`: 문서 전용 변경(Task 계약상 선택 검증)이므로 실행하지
  않았다. `src/devbot/**` 런타임 코드는 변경하지 않았다.

## TODO

- 없음 (이 Task 범위 내에서는 없음). marker 자동 기록 등 후속 구현은 새 Task로
  분리한다(아래 Improvement Suggestions 참고).

## 위험 요소

- marker 규격은 아직 어떤 자동화 코드에도 연결되어 있지 않다. 후속 Task가 실제로
  `devbot-timeline:v1` marker를 GitHub comment에 기록하기 전까지, Status Card는
  GitHub 타임스탬프 기반 수동/추정 재구성에 의존한다(문서 8절에 명시).
  Task 계약의 Risk 섹션과 동일하게, 과거 Task의 정확한 시간표는 이 Task만으로는
  자동 계산되지 않는다.
- `devbot:working`이 dev/review 두 phase를 모두 표현하는 기존 라벨 설계(Task 014)는
  이 Task에서 바꾸지 않았다. phase 구분이 marker에만 의존하므로, marker 자동
  기록이 구현되기 전까지는 `devbot:working` 상태의 phase를 GitHub만으로 확정
  구분하기 어려울 수 있다 — 문서에 이 제약을 명시했다.
- Status Card 예시는 3가지 시나리오(진행 중/rework 중/완료)만 다룬다.
  `devbot:blocked`/`devbot:manual-action` 상태의 Status Card 예시는 포함하지
  않았다. 라벨 의미(3절)는 정의했지만, 이 두 상태의 전용 카드 예시는 후속
  문서 보강 대상이다.

## Improvement Suggestions

- DevBot이 5개 필수 이벤트 발생 시 `devbot-timeline:v1` marker를 자동으로 comment에
  남기는 구현을 다음 Task로 추가한다.
- `devbot status` CLI 또는 GitHub Action으로 marker를 읽어 7절 Status Card를
  자동 생성하는 기능을 검토한다.
- `devbot:blocked`/`devbot:manual-action` 상태에 대한 Status Card 예시를
  `docs/10-github-status-timeline.md`에 보강한다.
