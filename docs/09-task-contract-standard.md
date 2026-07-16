# Task Contract Standard

Version: 1.2.0
Last Updated: 2026-07-15

이 문서는 DevBot에서 Task 계약서를 작성하고 발행할 때 적용하는 운영 표준이다.
모든 Task Publisher와 구현 AI는 Task 계약서를 만들거나 수정하기 전에 이 문서를 따른다.

Planner/Implementer/Reviewer/Operator 역할 경계, 명명 및 번호 규칙, Task
Issue/PR 템플릿, 최소 리뷰 진입 계약(`Review PR #<number>.`), Planner
작업공간 검증 헬퍼(`devbot.planner`)는 `docs/12-planner-workflow.md`
(Task 022)에 정의되어 있다. 이 문서는 계약서 발행 순서와 완성도 검증을
계속 정의하고, `docs/12-planner-workflow.md`는 그 위에 역할 경계와
기계 검증 가능한 워크플로 계약을 추가한다.

## 핵심 원칙

- Task는 실행 가능한 완성형 계약서여야 한다.
- 빈 파일, 뼈대, 자리표시자, 미완성 초안은 Commit하지 않는다.
- 계약서가 완성되기 전에는 Task Issue와 Pull Request에 `devbot:ready`를 부착하지 않는다.
- 계약서 내용과 Task Issue는 동일한 작업 범위를 가리켜야 한다.
- 승인된 설계에서 Task Issue, Branch, Contract, Pull Request를 하나씩만 생성한다.

## 필수 항목

모든 Task 계약서는 최소 다음 항목을 포함한다.

1. 목표
2. 배경
3. Dependencies
4. 구현 범위
5. 제외 범위
6. 품질 게이트
7. 필수 테스트 이름
8. 검증 명령
9. Files Expected to Change
10. Risk
11. Rollback Strategy
12. Reviewer Focus
13. Definition of Done
14. Result 문서 경로

항목이 적용되지 않는 경우 삭제하지 말고 `해당 없음`과 그 이유를 명시한다.

## 발행 순서

Task 발행은 반드시 다음 순서로 수행한다.

1. Repository Sync
2. 이전 Result와 Roadmap 확인
3. 다음 Task와 범위 결정
4. Preview 작성
5. 사용자 승인
6. 작업 Branch 생성
7. 완성된 Task 계약서 작성
8. 계약서 완성도 검증
9. Commit
10. Pull Request 생성
11. Task Issue와 Pull Request 상호 링크 확인
12. Task Issue에 `devbot:ready` 라벨 부착

순서를 바꾸지 않는다.

## 계약서 완성도 검증

Task Issue와 PR을 실행 가능 상태로 전환하기 전에 다음을 확인한다.

- 필수 항목이 모두 존재한다.
- 각 품질 게이트에 하나 이상의 고정된 테스트 이름이 있다.
- 목표와 제외 범위가 충돌하지 않는다.
- 검증 명령이 현재 저장소에서 실행 가능하다.
- 예상 변경 파일이 과도하게 넓지 않다.
- 위험과 Rollback 전략이 구체적이다.
- 완료 조건이 관찰 가능한 결과로 작성되어 있다.
- 문서에 깨진 문자나 잘못된 인코딩이 없다.
- `TODO`, `TBD`, 빈 제목처럼 실행을 막는 자리표시자가 없다.

하나라도 만족하지 않으면 발행을 중단하고 계약서를 먼저 수정한다.

## Task Issue 규칙

- Issue 본문에는 Task 계약서 경로와 Task 목적을 포함한다.
- `devbot:ready`는 계약서, Branch, PR이 준비된 뒤에만 부착한다.
- 계약서 PR이 닫히거나 폐기되면 Task Issue도 `not_planned`로 닫는다.
- 동일 Task에 중복 Issue를 만들지 않는다.

## 단일 Task 추적 정책

- 하나의 Task는 하나의 작업 Branch와 하나의 Pull Request로 추적한다.
- 같은 Task의 수정, 리뷰 반영, 재검증은 기존 Branch와 Pull Request를 업데이트한다.
- 별도 `-impl` Branch, 임시 자동 Branch, 중복 Pull Request를 만들지 않는다.
- Task Issue에는 Task 계약서 경로, 작업 Branch, Pull Request 번호, Result 문서 경로를 명시한다.
- GitHub Issue 번호와 Pull Request 번호는 Task 번호와 독립적인 식별자다.
  세 식별자를 일치시키려 하지 말고, 대신 서로를 명시적으로 cross-link한다
  (`docs/12-planner-workflow.md` 4절).

## PR Evidence 필수 항목

Pull Request 본문 또는 최신 댓글에는 최소 다음 Evidence가 있어야 한다.

- 연결된 Task Issue와 Task 계약서 경로
- 작업 Branch와 Pull Request 번호
- Result 문서 경로
- Checkpoint별 대응 테스트
- 실행한 검증 명령과 결과
- CI 상태 또는 CI를 확인하지 못한 명확한 이유
- Task 범위, Result, PR 변경 사항이 일치한다는 근거

Evidence가 누락되거나 실제 변경 사항과 맞지 않으면 리뷰는 통과할 수 없다.

## Review Gate 표준

저장소 컨텍스트가 있으면 `Review PR #<number>.`처럼 짧은 리뷰 요청만으로도
충분하다 - Reviewer는 이 게이트를 적용하기 위해 필요한 `AGENTS.md`, 이
Review Gate 절, 연결된 Task 계약서, Result, PR Evidence, CI를 스스로 찾아
읽는다(최소 리뷰 진입 계약, `docs/12-planner-workflow.md` 2절,
`devbot.planner.resolve_review_entry`).

리뷰 역할은 Task 계약, Result, PR Evidence, CI, 운영 정책을 하나의 게이트로 검증한다.
다음 중 하나라도 불일치하면 코드와 테스트가 통과했더라도 `REQUEST CHANGES`로 판단한다.

- Task 계약과 구현 범위가 다르다.
- Result가 실제 변경 파일, 테스트, 검증 결과를 반영하지 않는다.
- PR Evidence가 누락되었거나 최신 head commit 기준이 아니다.
- CI 실패 또는 검증 미실행 상태를 성공처럼 보고한다.
- 단일 Task 추적 정책이나 AGENTS.md 운영 정책을 위반한다.

위 Review Gate는 구현 관여 여부와 관계없이 모든 리뷰어에게 예외 없이 적용되는 기본
기준이다. 구현에 관여했는지, 어떤 Agent가 Reviewer 역할을 맡았는지에 따라 기준을 완화하거나
강화하지 않는다. 모든 Reviewer는 PR Evidence와 Result가 실제 diff, 테스트, 검증 결과를
빠짐없이 증명하는지 같은 strict gate를 적용한다.

## 변경 관리

작업 중 계약 범위가 바뀌면 다음을 함께 갱신한다.

- Task 계약서
- Task Issue 설명
- Result 문서
- 필요한 경우 Roadmap 또는 설계 문서

구현 결과에 맞추기 위해 Task를 사후에 축소하거나 품질 게이트를 삭제해서는 안 된다.
