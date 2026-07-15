# GitHub Status Timeline Protocol

Version: 1.1.0
Last Updated: 2026-07-16

이 문서는 Task 017의 산출물이다. DevBot의 현재 상태를 사람과 모든 Agent(GPT/Claude/
Gemini 등, VPS에서 실행되는 DevBot 자신 포함)가 동일한 방식으로 읽고 답할 수 있도록
GitHub 기반 상태 타임라인 규격을 정의한다.

이 문서는 프로토콜/규격 문서다. Task 018이 수동 `devbot timeline start/end/status`
CLI를, Task 024가 daemon lifecycle(IMPLEMENT/REVIEW/REWORK Job)의 자동 marker
기록(`devbot.timeline.safe_ready`/`safe_start`/`safe_end`)을 각각 구현했다 -
자세한 내용은 `## 후속 구현 범위` 참고.

---

# 1. 목적

Task 013~016 운영 중 다음을 GitHub만 보고 답하기 어려웠다.

- Implementer가 Issue를 가져갔는지
- 개발이 시작됐는지
- push/PR update가 끝났는지
- Reviewer가 리뷰를 시작했는지
- 리뷰가 끝났는지
- 단계 사이에 사람이 기다린 시간인지, Agent가 작업한 시간인지

VPS에서 DevBot을 돌리고 GPT/Claude/Gemini 앱에서 GitHub를 읽어 상태를 묻는 구조가 된 뒤에도
동일한 질문에 항상 동일한 방식으로 답하려면, "상태"의 정의와 그 근거 자료가 하나로
고정되어 있어야 한다. 이 문서가 그 고정점이다.

---

# 2. Source of Truth 원칙

- GitHub Issue, Pull Request, Label, Comment, Check(CI), Commit이 DevBot 상태의
  유일한 source of truth다.
- "현재 상태가 뭐야?"라는 질문은 항상 이 GitHub 데이터를 기준으로 답한다.
- VPS나 로컬 DevBot 로그(`src/devbot/observability.py`가 남기는 구조적 로그 등)는
  장애 분석용 보조 자료다. 로그는 사용자가 명시적으로 "로그 보여줘"/"로그 기준으로"처럼
  요청했을 때만 참고하고, 상태 질문에 대한 기본 근거로 사용하지 않는다.
- 이유: 로그는 VPS/로컬 프로세스가 살아있고 접근 가능할 때만 존재한다. GitHub는 Agent나
  실행 위치와 무관하게 항상 동일한 답을 준다. 로그와 GitHub 상태가 어긋나면 GitHub가
  항상 이긴다.

---

# 3. 상태 라벨

DevBot은 Issue에 아래 `devbot:*` 라벨 중 정확히 하나를 부착한다
(라벨의 상세 전이 규칙은 `docs/03-state-machine.md` 참고, 이 문서는 라벨의 "의미"만 정의).

| 라벨 | 의미 |
|---|---|
| `devbot:ready` | 작업 대기. 아직 아무도 Issue를 claim하지 않았다. |
| `devbot:working` | active work 진행 중. dev phase(구현/수정 실행)와 review phase(자동 리뷰 실행)를 모두 표현하므로, 지금 진행 중인 것이 dev인지 review인지는 이 라벨만으로는 구분할 수 없고 반드시 comment의 timeline marker(`phase` 필드, 4절)로 구분해야 한다. |
| `devbot:review` | 리뷰 대기 또는 `MERGE READY` 결과를 받아 사람의 Merge를 기다리는 중. |
| `devbot:rework` | `devbot:working`과는 별개의 안정 상태. 리뷰가 `REQUEST CHANGES`를 남긴 뒤, 구현자가 rework를 claim해 다시 `devbot:working`으로 전이하기 전까지의 대기 상태다("PR feedback waiting for implementer changes", `docs/07-decisions.md` 2026-07-14 결정). 이 대기 구간은 6.3절의 `Wait implementer` 구간과 대응한다. |
| `devbot:manual-action` | 사람 조치 필요. GitHub metadata 작업, 외부 검증, 또는 Agent의 대화형 승인 대기 때문에 자동 rework 대상에서 제외됨. |
| `devbot:blocked` | 자동화 실패. 사람이 원인을 확인하고 `devbot:ready`/`devbot:review`/`devbot:rework` 중 하나로 되돌려야 재개된다. |
| `devbot:done` | 완료. PR이 Merge되어 Issue가 닫혔다. |

이 문서는 라벨 자체를 추가/삭제하지 않는다(Task 017 제외 범위). 위 7개 라벨의 의미와
전이는 `docs/07-decisions.md`(2026-07-14 "Rework is a separate stable Issue state"
결정), `docs/03-state-machine.md`, `src/devbot/issue_state.py`의 기존 정의와
일치하며, 이 문서는 그 정의를 상태 질문 응답 관점에서 재설명할 뿐 바꾸지 않는다.

---

# 4. Timeline Marker (기계용)

## 4.1 형식

Marker는 GitHub comment 본문 안에 hidden HTML comment로 작성한다. 사람이 읽는
상태 카드(5절)와 반드시 분리한다 — 같은 comment 안에 있어도 되지만, 사람이 읽는
텍스트에는 marker의 raw 필드를 노출하지 않는다.

```
<!-- devbot-timeline:v1 issue=32 pr=33 cycle=1 phase=dev event=start result=- at=2026-07-14T21:30:00Z -->
```

## 4.2 필드

| 필드 | 필수 | 의미 |
|---|---|---|
| `devbot-timeline:v1` | 예 | Marker 식별자 + 버전. 항상 첫 토큰. 버전이 바뀌면 `v2`, `v3`... 로 올리고 이전 버전 marker도 파서가 인식할 수 있어야 한다. |
| `issue` | 예 | 이 Job이 속한 GitHub Issue 번호. |
| `pr` | 아니오 | 연결된 Pull Request 번호. PR이 아직 없는 이벤트(`ready`, `dev:start`)에서는 생략하거나 `-`. |
| `cycle` | 예 | 몇 번째 dev/review 왕복인지. 최초 구현은 `cycle=1`, review가 `REQUEST CHANGES`를 내고 rework가 시작될 때마다 1씩 증가한다. |
| `phase` | 예 | `queue` \| `dev` \| `review` 중 하나. `ready` 이벤트는 `phase=queue`를 쓴다. |
| `event` | 예 | `ready` \| `start` \| `end` 중 하나 (5절). |
| `result` | 예 | `event=end`일 때 결과 코드(예: `pushed`, `manual-action`, `blocked`, `merge-ready`, `request-changes`, `done`). `event=ready`/`event=start`처럼 아직 결과가 없으면 `-`. |
| `at` | 예 | 이벤트 발생 시각. UTC, ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`). |

파서는 알 수 없는 추가 필드를 무시해야 한다(향후 필드 확장을 깨뜨리지 않기 위해).
필드 순서는 위 표 순서를 기본으로 하되, 파서는 순서에 의존하지 않고 `key=value` 토큰
단위로 읽어야 한다.

---

# 5. 필수 이벤트

| 이벤트 | marker 조합 | 의미 |
|---|---|---|
| `ready` | `phase=queue event=ready` | Queue 시작. Issue가 `devbot:ready`가 된 시각. |
| `dev:start` | `phase=dev event=start` | Implementer가 Issue를 claim하고 개발/수정을 시작. |
| `dev:end` | `phase=dev event=end` | Implementer가 push/PR update 또는 `manual-action`/`blocked`로 종료. |
| `review:start` | `phase=review event=start` | Reviewer가 리뷰를 시작. |
| `review:end` | `phase=review event=end` | Reviewer가 리뷰 결과(`MERGE READY`/`REQUEST CHANGES`)를 남김. |

이 5개 이벤트만으로 6절의 모든 구간(Queue, Dev, Review)의 시작/종료를 계산할 수 있다.
Waiting 구간(Wait reviewer, Wait implementer)은 별도 이벤트가 아니라, 인접한 두
이벤트 사이의 gap으로 "계산"되는 파생 구간이다(6절).

같은 Issue에 여러 cycle이 존재하면(rework 반복), 각 cycle은 독립된 `dev:start` ~
`review:end` marker 집합을 가지며 `cycle` 필드로 구분한다.

---

# 6. 시간 구간 계산 규칙

## 6.1 완료된 구간

모든 완료된 구간(시작 marker와 종료 marker가 모두 존재)은 다음 세 값을 함께 표시한다.

- 시작 시간
- 종료 시간
- 소요 시간 (종료 - 시작)

예: `Dev: 2026-07-14 21:30 → 2026-07-14 22:10 (40m)`

## 6.2 진행 중(pending/running) 구간

아직 종료 marker가 없는 구간은 다음을 표시한다.

- 시작 시간
- `now` (조회 시점)
- 현재까지 경과 시간

예: `Review: 2026-07-14 22:15 → now (0h 12m 경과)`

## 6.3 Waiting gap

phase 사이의 대기 시간은 작업 시간과 분리된 별도 구간으로 표시한다. 대표적인 waiting
구간은 다음과 같다.

- `Queue`: `ready` 이벤트 ~ 그 cycle의 `dev:start` 사이 (사람/스케줄러가 아직 claim
  하지 않고 대기한 시간).
- `Wait reviewer`: `dev:end` ~ `review:start` 사이 (Reviewer 실행을 기다린 시간).
- `Wait implementer`: `review:end`(result=`request-changes`) ~ 다음 cycle의
  `dev:start` 사이 (rework 시작을 기다린 시간).

Waiting 구간도 6.1/6.2와 동일한 규칙을 따른다 — 종료 marker가 있으면 시작/종료/소요
시간을, 아직 진행 중이면 시작/`now`/경과 시간을 표시한다. Waiting 구간을 Dev/Review
같은 active 구간과 합산하거나 생략해서는 안 된다(7절 `Total active`/`Total waiting`
분리 참고).

---

# 7. 사람이 읽는 상태 카드 (Status Card)

Status Card는 marker의 raw 필드를 노출하지 않고, 6절 규칙에 따라 계산된 값만 짧고
명확하게 보여준다. 표준 형식은 다음 항목을 이 순서로 포함한다.

1. `#<issue> / PR #<pr>` — 대상 Issue/PR 식별자
2. `State` — 현재 `devbot:*` 라벨
3. `Waiting` — 지금 이 순간 사람/Agent가 무엇을 기다리는 중인지 한 줄 요약
4. `Queue` — Queue 구간
5. Cycle별로: `Dev`, `Wait reviewer`, `Review`, `Wait implementer`, `Result`
6. `Total active` — 모든 Dev + Review 구간의 합
7. `Total waiting` — 모든 Queue + Wait reviewer + Wait implementer 구간의 합
8. `Total elapsed` — Queue 시작부터 지금(또는 완료 시각)까지 전체 경과 시간

## 7.1 예시 — 진행 중인 Issue

```
#31 / PR #30

State: devbot:review
Waiting: Wait reviewer 진행 중 (0h 12m 경과)

Queue: 2026-07-14 21:23 → 2026-07-14 21:30 (7m)

Cycle 1
  Dev: 2026-07-14 21:30 → 2026-07-14 22:10 (40m)
  Wait reviewer: 2026-07-14 22:10 → now (0h 12m 경과)
  Review: -
  Wait implementer: -
  Result: -

Total active: 40m
Total waiting: 19m (Queue 7m + Wait reviewer 12m, 진행 중)
Total elapsed: 59m
```

## 7.2 예시 — REQUEST CHANGES 이후 rework 진행 중(Cycle 2)

```
#31 / PR #30

State: devbot:working (phase=dev)
Waiting: 없음 (Cycle 2 Dev 진행 중)

Queue: 2026-07-14 21:23 → 2026-07-14 21:30 (7m)

Cycle 1
  Dev: 2026-07-14 21:30 → 2026-07-14 22:10 (40m)
  Wait reviewer: 2026-07-14 22:10 → 2026-07-14 22:25 (15m)
  Review: 2026-07-14 22:25 → 2026-07-14 22:40 (15m)
  Wait implementer: 2026-07-14 22:40 → 2026-07-14 22:42 (2m)
  Result: REQUEST CHANGES

Cycle 2
  Dev: 2026-07-14 22:42 → now (0h 8m 경과)
  Wait reviewer: -
  Review: -
  Wait implementer: -
  Result: -

Total active: 88m (Cycle 1 Dev 40m + Review 15m + Cycle 2 Dev 8m 진행 중)
Total waiting: 24m (Queue 7m + Wait reviewer 15m + Wait implementer 2m)
Total elapsed: 119m
```

## 7.3 예시 — 완료(Merge)된 Issue

```
#31 / PR #30

State: devbot:done
Waiting: 없음 (완료)

Queue: 2026-07-14 21:23 → 2026-07-14 21:30 (7m)

Cycle 1
  Dev: 2026-07-14 21:30 → 2026-07-14 22:10 (40m)
  Wait reviewer: 2026-07-14 22:10 → 2026-07-14 22:25 (15m)
  Review: 2026-07-14 22:25 → 2026-07-14 22:40 (15m)
  Wait implementer: -
  Result: MERGE READY (사람이 2026-07-14 22:50 Merge)

Total active: 55m
Total waiting: 22m (Queue 7m + Wait reviewer 15m)
Total elapsed: 87m
```

---

# 8. 다른 Agent와의 일관성

- 모든 Agent(구현/리뷰 역할, 그리고 상태를 "질문받는" GPT/Claude/Gemini 앱 등)는
  이 문서의 라벨 의미(3절), marker 필드(4절), 이벤트(5절), 구간 계산 규칙(6절)만
  가지고 동일한 Status Card(7절)를 재구성할 수 있어야 한다.
- Task 024 이전에 완료된 과거 Task 구간(9절 - 역사적 backfill은 범위 밖)은 이벤트
  marker가 없으므로, GitHub의 Issue/PR 타임스탬프(라벨 변경 시각, comment 시각, PR
  생성/커밋/리뷰 시각)로 최선 추정(best-effort)한다는 점을 답변에 명시한다.
- 상태 질문에는 항상 Status Card 형식(7절)을 우선 사용하고, 필요할 때만 자연어
  설명을 덧붙인다.

---

# 9. 후속 구현 범위 (이 Task의 범위 밖)

다음은 이 프로토콜을 정의만 하고, 실제 구현은 이후 Task로 남긴다.

- ~~DevBot이 5절 이벤트가 발생할 때마다 4절 marker를 GitHub comment에 자동으로
  남기는 구현.~~ Task 024에서 구현 완료 (`src/devbot/timeline.py`의
  `safe_ready`/`safe_start`/`safe_end`, `devbot.polling`/`devbot.review`/
  `devbot.rework`에서 호출). Timeline write 실패는 진단으로만 노출되고 Job의
  실제 결과를 대체하지 않는다(`results/024-timeline-auto-recording.md`).
- ~~`devbot timeline status` 같은 CLI로 7절 Status Card를 자동 생성하는 구현.~~
  Task 018에서 구현 완료 (`devbot timeline start/end/status`).
- 자동 Merge, historical backfill(Task 024 이전 완료 Task 구간에 marker 소급 기록).
- GitHub 상의 status card 자동 업데이트(예: PR 설명 갱신).
- Dashboard/UI.
- VPS 로그 수집 파이프라인.
- Worktree Manager, Agent registry/failover.
- 상태 라벨(`devbot:*`) 자체의 추가/삭제.
