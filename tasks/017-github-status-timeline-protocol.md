# Task 017: GitHub Status Timeline Protocol

## 목표

DevBot의 현재 상태를 VPS나 로컬 로그가 아니라 GitHub만 보고 일관되게 요약할 수 있도록
상태 타임라인 규격을 정의한다.

이 Task는 즉시 자동 기록 기능을 구현하는 것이 아니라, 모든 Agent와 사람이 같은 방식으로
Issue/PR 상태를 읽고 답할 수 있도록 GitHub 라벨, comment marker, 시간 구간, 상태 카드
형식을 문서화하는 작업이다.

## 배경

Task 013~016 운영 중 DevBot은 실제로 작업을 진행했지만, 사용자는 다음을 구분하기 어려웠다.

- Implementer가 Issue를 가져갔는지
- 개발이 시작됐는지
- push/PR update가 끝났는지
- Reviewer가 리뷰를 시작했는지
- 리뷰가 끝났는지
- 단계 사이에 사람이 기다린 시간인지, Agent가 작업한 시간인지

향후 VPS에서 DevBot을 돌리고 GPT/Claude/Gemini 앱에서 GitHub를 읽어 상태를 묻는 구조가 된다.
따라서 GitHub가 source of truth가 되어야 하며, 상태 질문은 DevBot 로그가 아니라 GitHub
Issue/PR 라벨, 댓글, commit, check, review 결과를 기준으로 답해야 한다.

## Dependencies

- Task 013: Observability and debug logging
- Task 014: Workflow state machine hardening
- Task 015: Review gate standardization
- Task 016: Rework no-op and metadata action handling
- `AGENTS.md`
- `docs/00-roadmap.md`
- `docs/03-state-machine.md`
- `docs/08-beta-runbook.md`

## 구현 범위

1. GitHub Status Timeline Protocol 문서를 추가한다.
   - 문서 경로는 `docs/10-github-status-timeline.md`로 한다.
   - GitHub Issue/PR/label/comment/check/commit을 source of truth로 정의한다.
   - VPS/로컬 DevBot 로그는 장애 분석용 보조 자료로만 정의한다.
2. 상태 라벨 의미를 정리한다.
   - `devbot:ready`: 작업 대기
   - `devbot:working`: active work 진행 중
   - `devbot:review`: 리뷰 대기
   - `devbot:manual-action`: 사람 조치 필요
   - `devbot:blocked`: 자동화 실패
   - `devbot:done`: 완료
3. timeline marker 규격을 정의한다.
   - marker는 GitHub comment 안의 hidden HTML comment로 작성한다.
   - marker는 기계용이고, 사람이 읽는 상태 카드는 별도로 짧고 명확해야 한다.
   - marker는 version, issue, pr, cycle, phase, event, result, at 필드를 지원해야 한다.
4. 필수 이벤트를 정의한다.
   - `ready`: Queue 시작
   - `phase=dev event=start`: Implementer가 Issue를 claim하고 개발/수정을 시작
   - `phase=dev event=end`: Implementer가 push/PR update 또는 manual-action/blocked로 종료
   - `phase=review event=start`: Reviewer가 리뷰를 시작
   - `phase=review event=end`: Reviewer가 리뷰 결과를 남김
5. 시간 구간 계산 규칙을 정의한다.
   - 모든 완료된 구간은 시작 시간, 종료 시간, 소요 시간을 모두 표시해야 한다.
   - pending/running 구간은 시작 시간, `now`, 현재까지 경과 시간을 표시해야 한다.
   - phase 사이 gap은 별도 waiting segment로 표시해야 한다.
   - 예: `Wait reviewer`, `Wait implementer`, `Queue`.
6. 상태 카드 표준 형식을 정의한다.
   - `#31 / PR #30`
   - `State`
   - `Waiting`
   - `Queue`
   - Cycle별 `Dev`, `Wait reviewer`, `Review`, `Wait implementer`, `Result`
   - `Total active`
   - `Total waiting`
   - `Total elapsed`
7. 상태 질문 응답 규칙을 `AGENTS.md`에 추가한다.
   - "현재 상태" 질문은 GitHub를 기준으로 답한다.
   - 로컬/VPS 로그는 사용자가 명시적으로 요청할 때만 참고한다.
   - 상태 요약은 가능한 한 상태 카드 형식을 따른다.
8. 기존 자동화 구현은 변경하지 않는다.
   - 이 Task는 프로토콜과 문서 규격 확정이다.
   - DevBot이 marker를 실제로 자동 작성하는 기능은 후속 Task로 남긴다.

## 제외 범위

- DevBot의 marker 자동 작성 구현
- `devbot status` CLI 구현
- GitHub status card 자동 업데이트
- Dashboard/UI
- VPS 로그 수집
- Worktree Manager
- Agent registry/failover
- 상태 라벨 추가 또는 삭제

## 품질 게이트

### CP-017-1: Protocol document exists

`docs/10-github-status-timeline.md`가 존재하고 GitHub Status Timeline Protocol의 목적,
source of truth, marker, 상태 카드 형식을 설명해야 한다.

### CP-017-2: State labels are documented

문서는 `devbot:ready`, `devbot:working`, `devbot:review`, `devbot:manual-action`,
`devbot:blocked`, `devbot:done`의 의미를 모두 정의해야 한다.

### CP-017-3: Required timeline events are documented

문서는 `ready`, `dev:start`, `dev:end`, `review:start`, `review:end` 이벤트를 모두
정의해야 한다.

### CP-017-4: Marker format is machine-readable

문서는 hidden HTML comment marker 형식을 정의해야 하며, 최소한 다음 필드를 포함해야 한다.

- `devbot-timeline:v1`
- `issue`
- `cycle`
- `phase`
- `event`
- `result`
- `at`

### CP-017-5: Human status card includes start/end/duration

문서의 상태 카드 예시는 모든 완료된 구간에 시작 시간, 종료 시간, 소요 시간을 포함해야 한다.

### CP-017-6: Waiting gaps are explicit

문서는 `Queue`, `Wait reviewer`, `Wait implementer` 같은 phase 사이 대기 시간을 별도
구간으로 표시하는 규칙을 포함해야 한다.

### CP-017-7: Pending/running intervals are documented

문서는 아직 종료되지 않은 구간을 `start -> now`와 현재 경과 시간으로 표시하는 규칙을
포함해야 한다.

### CP-017-8: AGENTS status-answer rule is added

`AGENTS.md`는 "현재 상태" 질문에 GitHub Issue/PR/label/comment/check/commit 기준으로
답하고, VPS/로컬 로그는 명시 요청 시에만 참고한다는 규칙을 포함해야 한다.

### CP-017-9: Roadmap is updated

`docs/00-roadmap.md`는 Task 017을 Operations phase에 추가해야 한다.

### CP-017-10: Result documents protocol decisions

`results/017-github-status-timeline-protocol.md`는 확정된 marker 형식, 상태 카드 예시,
남은 후속 구현 범위를 기록해야 한다.

## 필수 테스트 이름

이 Task는 문서/프로토콜 Task이므로 런타임 테스트는 필수 범위가 아니다. 대신 문서 검증을 위한
정적 테스트를 추가한다.

- `test_task_017_protocol_doc_exists`
- `test_task_017_protocol_documents_required_markers`
- `test_task_017_status_card_requires_start_end_duration`
- `test_task_017_agents_status_rule_is_documented`

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
```

문서 전용 변경이므로 `uv run devbot --once --dry-run`은 선택 검증이다.

## Files Expected to Change

- `AGENTS.md`
- `docs/00-roadmap.md`
- `docs/10-github-status-timeline.md`
- `tasks/017-github-status-timeline-protocol.md`
- `results/017-github-status-timeline-protocol.md`
- `tests/test_task_contract_docs.py` 또는 기존 문서 검증 테스트 파일

## Risk

- marker 규격이 너무 복잡하면 Agent들이 일관되게 작성하지 못할 수 있다.
- 상태 카드가 너무 장황하면 모바일에서 가독성이 떨어질 수 있다.
- DevBot 자동 marker 작성 구현 전까지는 과거 Task의 정확한 시간표는 GitHub 이벤트 기반
  추정에 머물 수 있다.
- `devbot:working`은 dev/review phase를 모두 표현하므로, phase 구분은 반드시 marker에
  의존해야 한다.

## Rollback Strategy

- 문서 규격이 맞지 않으면 `docs/10-github-status-timeline.md`와 AGENTS 상태 응답 규칙만
  되돌린다.
- 기존 DevBot 런타임 상태 전이나 라벨 동작은 이 Task에서 변경하지 않으므로 런타임 rollback은
  필요하지 않다.

## Reviewer Focus

- GitHub만 source of truth로 삼는 원칙이 명확한가.
- 시작 시간, 종료 시간, 소요 시간이 모두 필요한 구간에 명시되어 있는가.
- phase 사이 waiting gap이 누락되지 않는가.
- 사람이 읽는 상태 카드와 기계용 marker가 분리되어 있는가.
- 다른 Agent가 같은 규격을 보고 동일한 상태 요약을 작성할 수 있는가.
- 후속 구현 범위와 이번 문서 범위가 섞이지 않았는가.

## Definition of Done

- CP-017-1~10을 모두 만족한다.
- `docs/10-github-status-timeline.md`가 작성된다.
- `AGENTS.md`에 상태 질문 응답 규칙이 추가된다.
- `docs/00-roadmap.md`에 Task 017이 추가된다.
- 문서 검증 테스트가 추가되고 통과한다.
- `results/017-github-status-timeline-protocol.md`가 작성된다.

## Result 문서 경로

`results/017-github-status-timeline-protocol.md`
