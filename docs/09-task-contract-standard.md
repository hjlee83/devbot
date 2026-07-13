# Task Contract Standard

Version: 1.0.0
Last Updated: 2026-07-14

이 문서는 DevBot에서 Task 계약서를 작성하고 발행할 때 적용하는 운영 표준이다.
모든 Task Publisher와 구현 AI는 Task 계약서를 만들거나 수정하기 전에 이 문서를 따른다.

## 핵심 원칙

- Task는 실행 가능한 완성형 계약서여야 한다.
- 빈 파일, 뼈대, 자리표시자, 미완성 초안은 Commit하지 않는다.
- 계약서가 완성되기 전에는 Pull Request와 `devbot:ready` 실행용 Issue를 생성하지 않는다.
- 계약서 내용과 실행용 Issue는 동일한 작업 범위를 가리켜야 한다.
- Task 계약서 PR이 생성된 뒤에만 실행용 Issue를 생성한다.

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
11. 실행용 Issue 생성
12. `devbot:ready` 라벨 부착

순서를 바꾸지 않는다.

## 계약서 완성도 검증

PR과 실행용 Issue를 생성하기 전에 다음을 확인한다.

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

## 실행용 Issue 규칙

- Issue 본문에는 Task 계약서 경로와 Task 목적을 포함한다.
- `devbot:ready`는 계약서 PR 생성 후에만 부착한다.
- 계약서 PR이 닫히거나 폐기되면 실행용 Issue도 `not_planned`로 닫는다.
- 동일 Task에 중복 실행용 Issue를 만들지 않는다.

## 변경 관리

작업 중 계약 범위가 바뀌면 다음을 함께 갱신한다.

- Task 계약서
- 실행용 Issue 설명
- Result 문서
- 필요한 경우 Roadmap 또는 설계 문서

구현 결과에 맞추기 위해 Task를 사후에 축소하거나 품질 게이트를 삭제해서는 안 된다.
