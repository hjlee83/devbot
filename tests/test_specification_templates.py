from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubIssue
from devbot.models import RepositoryConfig
from devbot.specification import (
    REQUIRED_TOP_LEVEL_SECTIONS,
    gather_task_evidence,
    generate_specification,
    parse_contract,
    render_specification,
    write_specification,
)
from devbot.specification_templates import (
    DuplicateSpecificationTemplateError,
    SpecificationTemplate,
    SpecificationTemplateRegistry,
    TemplateSelectionSource,
    UnknownSpecificationTemplateError,
    get_specification_template,
    list_specification_templates,
    render_template_policy,
    select_specification_template,
)
from devbot.specification_validation import (
    REQUIRED_SUBSECTIONS,
    validate_specification_text,
)

_TEMPLATE_IDS = ("bugfix", "docs", "feature", "generic", "internal", "refactor")


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/nonexistent-devbot-repo"),
        default_branch="main",
    )


def _issue() -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=92,
        title="Task 099: Template Sample",
        body="",
        state="open",
        labels=(),
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


def _contract_text(template_id: str | None = None) -> str:
    type_section = (
        f"\n## Specification Type\n\n{template_id}\n" if template_id is not None else ""
    )
    return f"""# Task 099: Template Sample
{type_section}
## Goal

Build deterministic template support.

## In Scope

- Keep one canonical schema.

## Out of Scope

- Do not invoke Agents.

## Functional Requirements

1. Render a valid Specification.

## Quality Gates

### CP-099-1 - validation

Required tests:
- `test_template_sample`

## Validation Gate

```bash
uv run pytest
```

## Files Expected to Change

- `src/devbot/specification_templates.py`

## Risk

- Keep selection deterministic.

## Definition of Done

- Template output validates.

## Git Rules

- Task Issue: #92
"""


def _evidence(tmp_path: Path, template_id: str | None = None):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "099-template-sample.md").write_text(_contract_text(template_id), encoding="utf-8")
    client = MagicMock()
    client.list_issues.return_value = [_issue()]
    return gather_task_evidence(client, _repository(), 99, tasks_dir=tasks_dir)


def _render_for_template(tmp_path: Path, template_id: str) -> str:
    evidence = _evidence(tmp_path, template_id)
    selection = select_specification_template(evidence.contract.sections)
    return render_specification(evidence, template_selection=selection)


def test_builtin_registry_contains_exactly_six_deterministic_ids() -> None:
    assert tuple(template.id for template in list_specification_templates()) == _TEMPLATE_IDS


def test_registry_rejects_duplicate_ids() -> None:
    template = SpecificationTemplate("generic", "one", ("guidance",))
    with pytest.raises(DuplicateSpecificationTemplateError):
        SpecificationTemplateRegistry((template, template))


def test_unknown_template_lookup_fails_clearly() -> None:
    with pytest.raises(UnknownSpecificationTemplateError, match="unknown Specification template"):
        get_specification_template("release")


@pytest.mark.parametrize("template_id", _TEMPLATE_IDS)
def test_explicit_contract_type_selects_matching_template(template_id: str) -> None:
    contract = parse_contract(_contract_text(f"  {template_id.upper()}  "))

    selection = select_specification_template(contract.sections)

    assert selection.template.id == template_id
    assert selection.source is TemplateSelectionSource.CONTRACT


def test_missing_contract_type_selects_generic() -> None:
    contract = parse_contract(_contract_text())

    selection = select_specification_template(contract.sections)

    assert selection.template.id == "generic"
    assert selection.source is TemplateSelectionSource.DEFAULT


def test_unknown_explicit_contract_type_fails_closed() -> None:
    contract = parse_contract(_contract_text("release"))

    with pytest.raises(UnknownSpecificationTemplateError):
        select_specification_template(contract.sections)


def test_repeated_selection_is_deterministic() -> None:
    contract = parse_contract(_contract_text("feature"))

    assert select_specification_template(contract.sections) == select_specification_template(
        contract.sections
    )


def test_cli_override_takes_precedence_and_records_source(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path, "bugfix")
    selection = select_specification_template(evidence.contract.sections, override="docs")
    content = render_specification(evidence, template_selection=selection)

    assert selection.template.id == "docs"
    assert selection.source is TemplateSelectionSource.OVERRIDE
    assert "Specification Template: `docs` selected from `override`" in content
    assert "## Specification Type\n\nbugfix" in evidence.contract.full_text


def test_invalid_cli_override_fails() -> None:
    contract = parse_contract(_contract_text("feature"))

    with pytest.raises(UnknownSpecificationTemplateError):
        select_specification_template(contract.sections, override="unknown")


@pytest.mark.parametrize("template_id", _TEMPLATE_IDS)
def test_every_template_output_preserves_schema_and_passes_validator(
    tmp_path: Path, template_id: str
) -> None:
    content = _render_for_template(tmp_path, template_id)

    positions = [content.index(heading) for heading in REQUIRED_TOP_LEVEL_SECTIONS]
    assert positions == sorted(positions)
    for heading in REQUIRED_TOP_LEVEL_SECTIONS:
        assert heading in content
    for subsections in REQUIRED_SUBSECTIONS.values():
        for subsection in subsections:
            assert f"## {subsection}" in content

    result = validate_specification_text(
        content, task_number=99, specification_path=Path("specifications/099-template.md")
    )
    assert result.passed is True
    assert result.errors == ()


def test_templates_are_observably_different_without_invented_bugfix_root_cause(
    tmp_path: Path,
) -> None:
    feature = _render_for_template(tmp_path, "feature")
    bugfix = _render_for_template(tmp_path, "bugfix")
    refactor = _render_for_template(tmp_path, "refactor")
    docs = _render_for_template(tmp_path, "docs")
    internal = _render_for_template(tmp_path, "internal")
    generic = _render_for_template(tmp_path, "generic")

    assert "externally observable behaviour" in feature
    assert "reproduction" in bugfix
    assert "without inventing a root cause" in bugfix
    assert "Root cause:" not in bugfix
    assert "preserved behaviour" in refactor
    assert "target audience" in docs
    assert "operational constraints" in internal
    assert "generic Task 042 evidence mapping" in generic
    assert feature != bugfix


def test_generate_specification_uses_contract_selected_template(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "099-template-sample.md").write_text(_contract_text("internal"), encoding="utf-8")
    client = MagicMock()
    client.list_issues.return_value = [_issue()]

    specification = generate_specification(client, _repository(), 99, tasks_dir=tasks_dir)

    assert "Specification Template: `internal` selected from `contract`" in specification.content
    assert "operational constraints" in specification.content


def test_generate_specification_override_is_deterministic(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "099-template-sample.md").write_text(_contract_text("feature"), encoding="utf-8")
    client = MagicMock()
    client.list_issues.return_value = [_issue()]

    first = generate_specification(
        client, _repository(), 99, tasks_dir=tasks_dir, template_id="docs"
    )
    second = generate_specification(
        client, _repository(), 99, tasks_dir=tasks_dir, template_id="docs"
    )

    assert first.content == second.content
    assert "Specification Template: `docs` selected from `override`" in first.content


def test_generate_dry_run_style_render_does_not_write(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path, "feature")
    specifications_dir = tmp_path / "specifications"

    content = render_specification(evidence)

    assert content
    assert not specifications_dir.exists()


def test_write_path_only_changes_when_caller_writes(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "099-template-sample.md").write_text(_contract_text("feature"), encoding="utf-8")
    client = MagicMock()
    client.list_issues.return_value = [_issue()]
    specification = generate_specification(client, _repository(), 99, tasks_dir=tasks_dir)

    path = write_specification(specification, directory=tmp_path / "specifications")

    assert path.is_file()


def test_template_policy_render_is_deterministic() -> None:
    template = get_specification_template("bugfix")

    assert render_template_policy(template) == render_template_policy(template)
    assert "template: bugfix" in render_template_policy(template)
