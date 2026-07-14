"""Task 017: GitHub Status Timeline Protocol 문서 검증 테스트.

이 Task는 런타임 코드가 아니라 문서/프로토콜 계약이므로, 필수 테스트는 문서
내용이 계약(tasks/017-github-status-timeline-protocol.md)의 품질 게이트를
만족하는지 정적으로 확인한다.
"""

from pathlib import Path

PROTOCOL_DOC = Path("docs/10-github-status-timeline.md")
AGENTS_DOC = Path("AGENTS.md")
ROADMAP_DOC = Path("docs/00-roadmap.md")
RESULT_DOC = Path("results/017-github-status-timeline-protocol.md")

REQUIRED_STATE_LABELS = [
    "devbot:ready",
    "devbot:working",
    "devbot:review",
    "devbot:manual-action",
    "devbot:blocked",
    "devbot:done",
]

REQUIRED_EVENTS = [
    "ready",
    "dev:start",
    "dev:end",
    "review:start",
    "review:end",
]

REQUIRED_MARKER_FIELDS = [
    "devbot-timeline:v1",
    "issue",
    "cycle",
    "phase",
    "event",
    "result",
    "at",
]

REQUIRED_WAITING_SEGMENTS = [
    "Queue",
    "Wait reviewer",
    "Wait implementer",
]


def test_task_017_protocol_doc_exists() -> None:
    assert PROTOCOL_DOC.is_file(), f"{PROTOCOL_DOC} 가 존재해야 한다 (CP-017-1)"

    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    assert text.strip(), f"{PROTOCOL_DOC} 는 비어 있으면 안 된다"

    # 목적, source of truth, marker, 상태 카드 형식을 모두 설명해야 한다 (CP-017-1).
    assert "Source of Truth" in text or "source of truth" in text.lower()
    assert "Timeline Marker" in text
    assert "상태 카드" in text or "Status Card" in text


def test_task_017_protocol_documents_required_markers() -> None:
    text = PROTOCOL_DOC.read_text(encoding="utf-8")

    # CP-017-2: 상태 라벨 의미가 모두 정의되어야 한다.
    for label in REQUIRED_STATE_LABELS:
        assert label in text, f"상태 라벨 {label} 이 문서에 정의되어야 한다"

    # CP-017-3: 필수 이벤트가 모두 정의되어야 한다.
    for event in REQUIRED_EVENTS:
        assert event in text, f"이벤트 {event} 가 문서에 정의되어야 한다"

    # CP-017-4: marker는 최소 다음 필드를 포함해야 한다.
    for field in REQUIRED_MARKER_FIELDS:
        assert field in text, f"marker 필드 {field} 가 문서에 정의되어야 한다"

    # CP-017-6: phase 사이 waiting gap이 별도 구간으로 문서화되어야 한다.
    for segment in REQUIRED_WAITING_SEGMENTS:
        assert segment in text, f"waiting 구간 {segment} 가 문서에 정의되어야 한다"

    # CP-017-7: 아직 종료되지 않은 구간은 start -> now와 경과 시간으로 표시해야 한다.
    assert "now" in text
    assert "경과" in text


def test_task_017_status_card_requires_start_end_duration() -> None:
    text = PROTOCOL_DOC.read_text(encoding="utf-8")

    # CP-017-5: 완료된 구간은 시작/종료/소요 시간을 모두 포함해야 한다.
    assert "시작 시간" in text
    assert "종료 시간" in text
    assert "소요 시간" in text

    # 상태 카드 표준 형식(Task 계약 항목 6)이 모두 문서화되어야 한다.
    for field in [
        "State",
        "Waiting",
        "Queue",
        "Dev",
        "Wait reviewer",
        "Review",
        "Wait implementer",
        "Result",
        "Total active",
        "Total waiting",
        "Total elapsed",
    ]:
        assert field in text, f"상태 카드 항목 {field} 가 문서에 정의되어야 한다"

    # 예시 상태 카드 안에 실제 시작 -> 종료 (소요 시간) 패턴이 존재해야 한다.
    assert "→" in text and "(" in text


def test_task_017_agents_status_rule_is_documented() -> None:
    text = AGENTS_DOC.read_text(encoding="utf-8")

    assert "상태 질문 응답 규칙" in text
    assert "GitHub" in text
    assert "VPS" in text or "로컬" in text
    assert "docs/10-github-status-timeline.md" in text


def test_task_017_roadmap_updated() -> None:
    text = ROADMAP_DOC.read_text(encoding="utf-8")
    assert "Task 017" in text


def test_task_017_result_doc_exists() -> None:
    assert RESULT_DOC.is_file(), f"{RESULT_DOC} 가 존재해야 한다 (CP-017-10)"
    text = RESULT_DOC.read_text(encoding="utf-8")
    assert text.strip()
