# Task 010: ReworkService 폴링 루프 연결

Version: 1.0.0
Last Updated: 2026-07-14

## 목표

현재 구현되어 있으나 자동 실행 경로에 연결되지 않은 `ReworkService`를
`PollingService`의 실행 흐름에 연결한다.

`devbot:review` 상태의 Issue와 연결된 Pull Request에서 처리되지 않은
`@devbot` 댓글을 감지하면, 새 브랜치나 새 PR을 만들지 않고 기존 작업
브랜치와 기존 PR을 재사용하여 수정 작업을 수행해야 한다.

---

## 구현 범위

### 포함

- `devbot:review` 상태 Issue 조회
- Issue와 연결된 기존 Pull Request 식별
- Pull Request 댓글 조회
- 처리되지 않은 `@devbot` 댓글 감지
- `find_unprocessed_devbot_comments()`와 `ReworkService.process()` 연결
- 기존 Issue/브랜치/PR 재