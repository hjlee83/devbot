from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubIssue
from devbot.models import RepositoryConfig
from devbot.specification import (
    REQUIRED_TOP_LEVEL_SECTIONS,
    AmbiguousTaskError,
    ContractAmbiguousError,
    ContractMissingError,
    InvalidTaskError,
    IssueMissingError,
    PlannerEvidenceMissingError,
    gather_task_evidence,
    generate_specification,
    parse_contract,
    render_specification,
    specification_path,
    validate_specification_schema,
    write_specification,
)


def _repository(**overrides: object) -> RepositoryConfig:
    defaults: dict[str, object] = dict(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/nonexistent-devbot-repo"),
        default_branch="main",
    )
    defaults.update(overrides)
    return RepositoryConfig(**defaults)  # type: ignore[arg-type]


def _issue(number: int, title: str, body: str = "", state: str = "open") -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=number,
        title=title,
        body=body,
        state=state,
        labels=(),
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


_SAMPLE_CONTRACT = """# Task 099: Sample Task Marker Zebra

## Goal

Distinctive goal text marker QUOKKA that must appear verbatim.

## In Scope

- distinctive scope item NARWHAL

## Out of Scope

- distinctive out-of-scope item PLATYPUS

## Functional Requirements

1. distinctive requirement marker AXOLOTL

## Quality Gates

### CP-099-1 - marker

Required tests:
- `test_marker_pangolin`

## Validation Gate

```bash
uv run pytest
```

## Files Expected to Change

- `src/devbot/sample.py`

## Risk

- distinctive risk marker OKAPI

## Definition of Done

- distinctive done marker CAPYBARA

## Git Rules

- Task Issue: #99
"""


def _write_contract(
    tmp_path: Path, text: str = _SAMPLE_CONTRACT, name: str = "099-sample.md"
) -> Path:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    path = tasks_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def _client_with_issue(number: int, title: str, body: str = "") -> MagicMock:
    client = MagicMock()
    client.list_issues.return_value = [_issue(number, title, body=body)]
    return client


# --------------------------------------------------------------------------
# parse_contract
# --------------------------------------------------------------------------


def test_parse_contract_extracts_title_and_sections() -> None:
    parsed = parse_contract(_SAMPLE_CONTRACT)

    assert parsed.title == "Sample Task Marker Zebra"
    assert "QUOKKA" in parsed.sections["Goal"]
    assert "NARWHAL" in parsed.sections["In Scope"]
    assert "PLATYPUS" in parsed.sections["Out of Scope"]


def test_parse_contract_handles_missing_title() -> None:
    parsed = parse_contract("no title line here\n\n## Goal\n\nsomething\n")

    assert parsed.title == ""


def test_parse_contract_keeps_level3_headings_inside_parent_body() -> None:
    parsed = parse_contract(_SAMPLE_CONTRACT)

    assert "### CP-099-1 - marker" in parsed.sections["Quality Gates"]
    assert "test_marker_pangolin" in parsed.sections["Quality Gates"]


def test_parse_contract_concatenates_duplicate_headings() -> None:
    text = "# Task 001: X\n\n## Goal\n\nfirst\n\n## Goal\n\nsecond\n"
    parsed = parse_contract(text)

    assert "first" in parsed.sections["Goal"]
    assert "second" in parsed.sections["Goal"]


def test_parse_contract_ignores_headings_inside_fenced_code_blocks() -> None:
    text = (
        "# Task 001: X\n\n"
        "## Out of Scope\n\n"
        "- real out-of-scope bullet\n\n"
        "## Example Template\n\n"
        "```\n"
        "# Technical Design\n"
        "## Out of Scope\n"
        "## Architecture\n"
        "```\n\n"
        "## Files Expected to Change\n\n"
        "- real files bullet\n"
    )
    parsed = parse_contract(text)

    assert parsed.sections["Out of Scope"].strip() == "- real out-of-scope bullet"
    assert "# Technical Design" not in parsed.sections["Out of Scope"]
    assert "## Architecture" not in parsed.sections
    assert parsed.sections["Files Expected to Change"].strip() == "- real files bullet"
    assert "# Technical Design" in parsed.sections["Example Template"]


# --------------------------------------------------------------------------
# gather_task_evidence: happy path + all required failure scenarios
# --------------------------------------------------------------------------


def test_gather_task_evidence_succeeds_for_well_formed_task(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")

    evidence = gather_task_evidence(
        client,
        _repository(),
        99,
        tasks_dir=tmp_path / "tasks",
        roadmap_path=tmp_path / "no-roadmap.md",
    )

    assert evidence.task_number == 99
    assert evidence.slug == "sample"
    assert evidence.issue_number == 1
    assert "QUOKKA" in evidence.contract.sections["Goal"]


def test_invalid_task_number_raises_for_zero_and_negative(tmp_path: Path) -> None:
    client = MagicMock()

    for bad_number in (0, -1, -99):
        with pytest.raises(InvalidTaskError):
            gather_task_evidence(client, _repository(), bad_number, tasks_dir=tmp_path / "tasks")


def test_missing_contract_raises(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    client = MagicMock()

    with pytest.raises(ContractMissingError, match="no Task Contract found"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_ambiguous_contract_raises_when_multiple_files_match(tmp_path: Path) -> None:
    _write_contract(tmp_path, name="099-sample.md")
    _write_contract(tmp_path, name="099-duplicate.md")
    client = MagicMock()

    with pytest.raises(ContractAmbiguousError, match="multiple candidate Contracts"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_missing_issue_raises(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = MagicMock()
    client.list_issues.return_value = []

    with pytest.raises(IssueMissingError, match="no GitHub Issue titled"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_ambiguous_task_raises_when_multiple_issues_match(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = MagicMock()
    client.list_issues.return_value = [
        _issue(1, "Task 099: Sample Task Marker Zebra"),
        _issue(2, "Task 099: Sample Task Marker Zebra"),
    ]

    with pytest.raises(AmbiguousTaskError, match="multiple GitHub Issues match"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_ambiguous_task_raises_on_contract_issue_title_mismatch(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Completely Different Title")

    with pytest.raises(AmbiguousTaskError, match="does not match"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_planner_evidence_missing_raises_for_empty_contract(tmp_path: Path) -> None:
    _write_contract(
        tmp_path,
        text="# Task 099: Empty Task\n\n## Git Rules\n\n- Task Issue: #1\n",
    )
    client = _client_with_issue(1, "Task 099: Empty Task")

    with pytest.raises(PlannerEvidenceMissingError, match="no identifiable Goal"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_invalid_task_raises_when_contract_lacks_canonical_title(tmp_path: Path) -> None:
    _write_contract(tmp_path, text="Not a canonical heading\n\n## Goal\n\nx\n")
    client = MagicMock()

    with pytest.raises(InvalidTaskError, match="canonical"):
        gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")


def test_goal_text_extracted_from_goal_executor_issue_body(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    body = (
        'Generated by `devbot goal execute` (Task 040) from the Goal: '
        '"Distinctive goal-executor goal text WOMBAT".\n\n- Contract...'
    )
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra", body=body)

    evidence = gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")

    assert evidence.goal_text == "Distinctive goal-executor goal text WOMBAT"


def test_roadmap_excerpt_included_when_entry_exists(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    roadmap_path = tmp_path / "roadmap.md"
    roadmap_path.write_text(
        "# Roadmap\n\n- [x] Task 099: Sample Task Marker Zebra. Distinctive roadmap marker "
        "IGUANA.\n",
        encoding="utf-8",
    )
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")

    evidence = gather_task_evidence(
        client, _repository(), 99, tasks_dir=tmp_path / "tasks", roadmap_path=roadmap_path
    )

    assert evidence.roadmap_excerpt is not None
    assert "IGUANA" in evidence.roadmap_excerpt


def test_roadmap_excerpt_none_when_roadmap_missing(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")

    evidence = gather_task_evidence(
        client,
        _repository(),
        99,
        tasks_dir=tmp_path / "tasks",
        roadmap_path=tmp_path / "no-roadmap.md",
    )

    assert evidence.roadmap_excerpt is None


# --------------------------------------------------------------------------
# render_specification: determinism, schema, grounding
# --------------------------------------------------------------------------


def _evidence(tmp_path: Path, **overrides: object):
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")
    return gather_task_evidence(
        client, _repository(), 99, tasks_dir=tmp_path / "tasks", **overrides
    )


def test_render_specification_is_deterministic(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)

    first = render_specification(evidence)
    second = render_specification(evidence)

    assert first == second


def test_render_specification_includes_required_top_level_sections(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)

    content = render_specification(evidence)

    for heading in REQUIRED_TOP_LEVEL_SECTIONS:
        assert heading in content


def test_render_specification_uses_not_specified_default_for_missing_sections(
    tmp_path: Path,
) -> None:
    _write_contract(
        tmp_path,
        text=(
            "# Task 099: Minimal Task\n\n"
            "## Goal\n\nDistinctive minimal goal marker SERVAL.\n"
        ),
    )
    client = _client_with_issue(1, "Task 099: Minimal Task")

    evidence = gather_task_evidence(client, _repository(), 99, tasks_dir=tmp_path / "tasks")
    content = render_specification(evidence)

    assert "Not specified in the Task Contract." in content
    assert "SERVAL" in content


def test_render_specification_includes_full_contract_verbatim(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)

    content = render_specification(evidence)

    assert _SAMPLE_CONTRACT.strip() in content


def test_render_specification_only_contains_grounded_content(tmp_path: Path) -> None:
    """No hallucination: every distinctive marker in the rendered output must
    trace back to the Contract text (or the fixed template strings) - this
    module never invents prose."""
    evidence = _evidence(tmp_path)

    content = render_specification(evidence)

    for marker in ("QUOKKA", "NARWHAL", "PLATYPUS", "AXOLOTL", "OKAPI", "CAPYBARA"):
        assert marker in _SAMPLE_CONTRACT
        assert content.count(marker) >= 1
    # A marker that was never in any evidence source must never appear.
    assert "HALLUCINATED_MARKER_NOT_IN_EVIDENCE" not in content


# --------------------------------------------------------------------------
# validate_specification_schema
# --------------------------------------------------------------------------


def test_validate_specification_schema_reports_missing_sections() -> None:
    missing = validate_specification_schema("# Overview\n\nonly this\n")

    assert "# Functional Requirements" in missing
    assert "# Overview" not in missing


def test_validate_specification_schema_empty_when_complete(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    content = render_specification(evidence)

    assert validate_specification_schema(content) == ()


# --------------------------------------------------------------------------
# generate_specification / write_specification: end to end + repeatability
# --------------------------------------------------------------------------


def test_generate_specification_end_to_end(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")

    specification = generate_specification(
        client, _repository(), 99, tasks_dir=tmp_path / "tasks"
    )

    assert specification.task_number == 99
    assert specification.slug == "sample"
    assert specification.path == specification_path(99, "sample")
    assert validate_specification_schema(specification.content) == ()


def test_repeated_generation_produces_identical_files(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")
    specifications_dir = tmp_path / "specifications"

    spec1 = generate_specification(client, _repository(), 99, tasks_dir=tmp_path / "tasks")
    path1 = write_specification(spec1, directory=specifications_dir)
    first_bytes = path1.read_bytes()

    spec2 = generate_specification(client, _repository(), 99, tasks_dir=tmp_path / "tasks")
    path2 = write_specification(spec2, directory=specifications_dir)
    second_bytes = path2.read_bytes()

    assert path1 == path2
    assert first_bytes == second_bytes


def test_write_specification_creates_directory_if_missing(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    client = _client_with_issue(1, "Task 099: Sample Task Marker Zebra")
    specification = generate_specification(client, _repository(), 99, tasks_dir=tmp_path / "tasks")
    specifications_dir = tmp_path / "brand-new-specifications-dir"

    written_path = write_specification(specification, directory=specifications_dir)

    assert written_path.is_file()
    assert written_path.parent == specifications_dir
