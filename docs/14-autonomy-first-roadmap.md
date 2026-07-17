# Autonomy-First Roadmap

> 이 문서는 devbot 재도전의 **흔들리지 않는 기준선**이다. 우선순위가 흔들릴 때 여기로 돌아온다.
> `docs/00-roadmap.md`(엔지니어링 태스크 이력)를 대체하지 않고, 그 위에서 **작업 순서를 재배치**한다.

## 배경: 왜 이 문서인가

지난 시도의 후회는 **코드 품질이 아니라 순서**였다. 자동화(무인 루프)를 나중으로 미루고, 반쯤 자동인 도구로 자기 자신을 만드는 도그푸딩을 너무 일찍 켜서 개입 하나하나가 이중 세금이 됐다.

그래서 이번 기준은 다음과 같이 확정한다.

- **제자리 리팩터** — 기존 repo를 살린다. 그린필드 재시작하지 않는다. (검증된 ~11K LOC + ~14K LOC 테스트, 거버넌스, 릴리즈 파이프라인은 자산이다.)
- **자동화 최단거리** — 인간개입 제거를 최우선으로 재배치한다.

## 설계 원칙

1. **각 단계 = 인간개입 1개 제거.** 스트레스가 마지막이 아니라 매 단계마다 즉시 줄어든다.
2. **도그푸딩 디커플링.** 무인 루프가 신뢰될 때까지 도그푸딩은 OFF. 자동화 코어는 사람이 직접(일반 개발로) 만든다.
3. **두 수술은 별도 단계가 아니다.** 드리프트가 몰린 두 곳(에이전트 실행 인터페이스 이중화, `polling.py` god-module)은 **큰 리팩터로 멈춰 서서** 하지 않고, 아래 작업이 그 코드를 건드릴 때 **그 자리에서 조금씩** 정리한다. 이것이 최단거리를 지키며 드리프트를 청산하는 법이다.
4. **자기수정·자기업데이트는 보류.** "자동 업데이트 → 즉시 반영 + 자기수정 + 도그푸딩"을 동시에 켜는 것은 안전장치 없이는 위험하다(나쁜 자기수정 하나가 돌고 있는 데몬을 망가뜨린다). 안전장치(last-known-good, 테스트 게이트) 이후로 미룬다.

## 목표 상태 (Definition of Done)

> **이슈 하나를 던지면 사람 손 없이 머지(또는 배포)까지 가는 무인 루프.**

현재는 가운데 3분의 1(`IMPLEMENT → REVIEW → REWORK → devbot:ready-to-merge` 라벨)만 자동이다. 시작단(생성)과 끝단(머지·배포), 자가복구가 빈 곳이다.

---

## Phase A — 준비 (측정 먼저, 반나절)

자르기 전에 잰다.

- [x] **도그푸딩 OFF.** 이 로드맵 기간 동안 devbot 코어는 손으로 개발한다. (실행 중인 데몬/launchd/cron 없음 확인)
- [x] **DRY_RUN으로 장난감 이슈 end-to-end 1회.** `uv run devbot --once --dry-run --verbose`로 실제로 **어디서 멈추고 사람을 부르는지** 실측한다. 서베이가 아니라 관측으로 개입 지점을 확정한다.

### Phase A 실측 결과 (2026-07-17)

더미 이슈(`devbot:ready`, PR 미연결) 1건으로 end-to-end 스모크 테스트 실행. **체크아웃이 깨끗하면 이 사이클은 사람 개입 없이 완주한다** — issue 발견 → claim → workspace 준비 → 구현(dry-run stub) → delivery(실제 ruff/pytest 검증 실행, push/PR은 skip) → 완료. GitHub 실제 상태(라벨·코멘트) 불변 확인, dry-run의 GitHub 쓰기 차단은 신뢰 가능.

**당초 가정("라벨이 최고 레버리지")은 이 단순 케이스에서 재현되지 않았다.** `devbot:blocked`/`devbot:ready` 구분은 정확했음. 대신 다음 세 가지가 실측으로 발견됐고, **B1보다 상류·우선순위가 높다**:

1. **self-update dirty-checkout 게이트가 최상위 병목.** `git status --porcelain`에 커밋 안 한 변경이 하나라도 있으면(`.worktrees/` 제외) 폴링 사이클 진입 전에 전체 실행이 `return 1`로 중단. 데몬/`--once`/dry-run 무관, **우회 플래그 없음**(`doctor --ci`만 예외). 사람이 저장소에서 작업 중이면 데몬이 아예 못 뜨는 구조 — 인간개입 체감의 실제 원인일 가능성이 큼.
2. **dry-run 하에서도 실제 force-push가 가능한 안전 구멍.** REWORK 잡, 또는 기존 PR이 연결된 IMPLEMENT 잡은 워크스페이스 준비 중 `_sync_task_branch_with_main`이 `dry_run`과 무관하게 `git push --force-with-lease`를 실행할 수 있음(`worktree.py`). REVIEW 잡만 `synchronize_with_main=False`로 안전. 이번 테스트는 PR 미연결 케이스라 안 걸렸으나 실서비스 재발 위험.
3. **Timeline dev:start/dev:end 불일치 (경미).** dry-run에서 `dev:start` 기록이 skip되는데 `dev:end`만 시도돼 "대응 시작 이벤트 없음" 에러 로그. 파이프라인은 안 죽지만 내부 정합성 깨짐.

→ **B1 앞에 B0을 신설**하여 1·2번을 먼저 해소한다. B1(라벨 정리) 자체는 폐기하지 않음 — rework/PR-연결 경로, 동시 이슈, blocked/manual-action 경계에서 여전히 유효한 문제로 추정되나, 그건 B0 이후 재관찰로 재확인한다.

**갱신(2026-07-18)**: 대화가 이어지며 self-update/dry-run-push 버그보다 먼저, **"구현 AI가 리뷰 피드백을 못 풀겠다며 반복적으로 사람에게 넘기는" 패턴**(사용자가 실제로 겪은 가장 큰 스트레스, GPT와 진단했던 devbot/devbot#69,#70 재발 사례)을 별도로 진단·수정함. 아래 B0을 이 수정 내용으로 갱신하고, self-update/dry-run-push 항목은 **B0-1로 이동(아직 미착수)**했다 — 코드 주석·테스트·`docs/07-decisions.md`에 이미 "CP-B0"로 박혀 있어 라벨은 그대로 두고 순서만 조정.

## Phase B — 자동화 사다리

각 칸은 독립적으로 개입을 하나 줄인다. 순서는 "체감 스트레스가 가장 빨리 떨어지는 순"으로 고정한다.

### B0. rework 조기 manual-action 오분류 수정 *(완료, 2026-07-18)*

"구현 AI가 리뷰 피드백을 못 풀겠다며 반복적으로 사람에게 넘기는데, 실제론 풀리는" 패턴의 근본 원인 수정. 상세 설계는 `~/.claude/plans/sunny-hopping-kazoo.md`, 결정 기록은 `docs/07-decisions.md`의 "2026-07-18 — Rework action-scope classification" 항목.

- **근본 원인**: `rework.py:classify_rework_action_scope()`가 `"ci"`를 부분문자열로 매칭하는데, `AGENTS.md`가 모든 리뷰에 "CI" 항목을 의무 포함시켜서 정상 리뷰가 구조적으로 거의 항상 `devbot:manual-action`으로 오분류됨 (devbot/devbot#69, #70 재발 사례).
- **수정 방향**: CI/network/dry-run 같은 "기술적으로 시도해볼 수 있는" 항목은 사전 텍스트 판단 자체를 제거 — 구현 AI가 일단 시도하고, 실제로 막히면 `classify_agent_outcome()`(Task 021)의 실행결과 기반 분류로 사후 판단. `"사람"`/`"승인"`(대체할 실행결과 신호가 없는 조직적 요구)만 사전 분류 유지.
- **부수 수정**: rework 실행경로가 exit code 0인데 실제로 승인대기 등으로 막힌 경우를 성공으로 오인하던 별도 버그(Issue #41과 동일 계열)도 함께 닫힘(`AgentOutcomeError` 신설). `review_loop_limit`(하드코딩 3)을 `REVIEW_LOOP_LIMIT` env로 노출.
- **주의(교훈)**: `\b` 단어경계 정규식으로 "방어적으로" 더 고치려다 되돌림 — 한글+영어 혼용 텍스트(조사 결합: "사람이", "label을")에서 단어경계가 오히려 다 깨짐(실증 확인). 최종은 단순 substring 매칭 + 패턴 목록 축소.
- **검증**: 전체 테스트 527 passed, ruff clean. 실사용 DRY_RUN 재현은 생략(REWORK job 재현에 branch+PR+연결이슈 필요해 비용 대비 가치 낮음 — 단위/통합 테스트가 정확한 문제 텍스트로 이미 결정론적으로 검증함).
- **미착수**: git commit 여부는 사용자 확인 대기 중.

### B0-1. 구조적 병목 해소 — self-update 게이트 + dry-run 안전 버그 *(Phase A 실측, 아직 미착수)*

라벨보다 상류에서 루프를 막거나 위험하게 만드는 두 가지.

- **self-update 게이트 완화**: "커밋 안 한 변경 1건이라도 있으면 전체 중단" 대신, 개발 중 안전하게 우회할 수 있는 경로를 만든다(예: `--once`/`--dry-run`에 한해 스킵 허용, 또는 명시적 플래그). 실서비스 자동 실행에서는 여전히 안전하게 막되, 사람이 손으로 개발 중일 때 데몬 자체가 죽지 않게 한다.
- **dry-run 안전 구멍 봉합**: `_sync_task_branch_with_main`(REWORK, PR-연결 IMPLEMENT 경로)이 `dry_run`을 존중하도록 수정 — dry-run 중에는 절대 실제 push가 나가지 않게.
- **(부수) Timeline dev:start/dev:end 정합성**: dry-run에서 start/end 기록 스킵 여부를 일관되게.
- 완료 기준: 손으로 작업 중인 상태(untracked 파일 있음)에서도 `--once --dry-run`이 정상 진행되고, dry-run 중 어떤 job_type에서도 원격 push가 발생하지 않음을 재검증.

### B1. 라벨/상태기 정리

"라벨이 이상해서 루프가 막힌다"는 초기 가정이었으나 Phase A 실측(단순 ready/blocked 케이스)에서는 재현되지 않았다. B0 이후 rework·PR-연결·동시 이슈 시나리오로 재관찰하며 착수한다.

- 대상: `issue_state`(devbot:* 라벨 상태기), 참조 `docs/03-state-machine.md`
- 목표: 상태·전이를 명확히, 애매한 라벨 조합은 **자가 복구**되게. 루프가 "stuck weird state"에 빠지지 않게.
- 완료 기준: 장난감 이슈를 여러 번 돌려도 사람이 라벨을 손으로 고칠 일이 없다.

### B2. 자동 머지 (안전 게이트)

매 태스크마다 있던 최대 수동 관문 제거.

- 대상: 머지 정책. 참조 `docs/06-review-policy.md`, `CONSTITUTION.md`("merge stays manual"은 정책 플래그로 전환)
- 목표: `리뷰 MERGE READY + CI 초록 + 킬스위치/allowlist`로 게이팅해서 자동 머지.
- 안전장치: 도그푸딩 재점화 대비, **자기수정 PR(devbot 자신을 바꾸는 PR)은 사람 승인 유지** 레일을 남긴다.

### B3. 자동 착수 (이슈 → 브랜치/PR)

시작단 개입 제거. "말 한마디" 모바일 입구(`ai/`의 Custom GPT)는 그대로 두고, 그 뒤 착수를 데몬이 알아서.

- 대상: 데몬의 intake→branch/PR 생성. 현재 생성은 Custom GPT(외부)에 있음.
- 목표: 이슈가 뜨면 브랜치/PR 생성까지 데몬이 자동. 사람은 "말 한마디"만.

### B4. 자가복구 (페일오버)

에이전트가 한도/실패 뜰 때 사람을 부르지 않게.

- 대상: `AgentRunner` / `build_agent_runner`(현재 static 2-entry). 참조 `docs/04-agent-system.md`, `docs/11-daemon-reliability.md`
- 목표: 실패/한도 감지 시 다음 프로바이더로 **reactive 전환**(사용량 추적 불필요). 큰 리팩터 없이.
- 부수 효과: 여기서 이중 에이전트 인터페이스 통합을 함께 정리(원칙 3). 나중 라우팅/레지스트리의 씨앗.

### B5. 배포 (repo별)

"배포까지"를 완성.

- 목표: 머지 후 repo별 배포 커맨드 실행. (장차 `bot init` 설정 항목으로 승격)
- 참고: 현재 `release.py`는 devbot 자신만 패키징. 대상 프로젝트 배포는 없음 → 신규.

> **여기서 무인 루프 완성.** 그때 도그푸딩을 안전하게 재점화한다.

## Phase C — 무인 이후 (나중 서랍)

무인 루프가 신뢰되기 전까지 **열지 않는다.** 전부 위 구조에 additive.

- [ ] 도그푸딩 재점화 (Phase B 완료가 전제)
- [ ] 동적 프로바이더/키 레지스트리 (어드민에서 키 추가 → 런타임 등록)
- [ ] 라우팅 전략 고도화: 뺑뺑이 / drain-then-failover는 B4에 근접, **사용량 추적 기반(곧 끝날 애 후순위)** 만 여기
- [ ] 어드민 UI (얇은 뷰)
- [ ] 자기업데이트(main 최신화 → 즉시 반영), 안전장치(last-known-good, 테스트 게이트) 동반
- [ ] `bot init` (대상 repo 부트스트랩)
- [ ] 음성 입구

## 리팩터 원칙 (드리프트 청산)

별도 단계 없음. 아래는 위 작업이 해당 코드를 건드릴 때만 정리한다.

- **에이전트 실행 인터페이스 통합** (`agents/base.py`·`claude.py`·`codex.py`의 구 `run()` vs 신 `run_context()`+`AgentLauncher`, 리플렉션 shim 제거) → B4에서.
- **`polling.py` god-module 분해** (~2,629줄) → B1~B3이 그 모듈을 건드릴 때 함수 단위로 추출. big-bang 금지.
- 곁다리 정리: orphaned `.devbot-worktrees`, `-improvements` 결과 문서 중복.

## 모델 사용 가이드

- **Opus**: 설계·판단이 파급되는 순간(B1 상태기 재설계, B2 안전 게이트 설계, 인터페이스 통합 판단).
- **Sonnet**: 확정된 설계의 구현·테스트·기계적 리팩터·배선·실행/검증. 기본값.
- **haiku / quick-lookup**: 파일 찾기·grep·정의 확인.

## 관련 문서

- `docs/00-roadmap.md` — 엔지니어링 태스크 이력 (Phase 3 "operations"의 launchd/VPS는 이 로드맵 이후)
- `docs/03-state-machine.md` — B1
- `docs/06-review-policy.md` — B2
- `docs/04-agent-system.md`, `docs/11-daemon-reliability.md` — B4
- `CONSTITUTION.md` — 원칙(merge gate, agent-neutrality, PreparedWorkspace)
