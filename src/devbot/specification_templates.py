"""Task 044: deterministic Specification template registry and selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SpecificationTemplateError(ValueError):
    """Base class for template lookup and selection failures."""


class UnknownSpecificationTemplateError(SpecificationTemplateError):
    """A requested or explicit template ID is not registered."""


class DuplicateSpecificationTemplateError(SpecificationTemplateError):
    """The built-in registry contains a duplicate template ID."""


class TemplateSelectionSource(StrEnum):
    CONTRACT = "contract"
    OVERRIDE = "override"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class SpecificationTemplate:
    id: str
    description: str
    guidance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplateSelection:
    template: SpecificationTemplate
    source: TemplateSelectionSource


_BUILTIN_TEMPLATES = (
    SpecificationTemplate(
        id="feature",
        description="User-visible feature work with compatibility and rollout guidance.",
        guidance=(
            "Emphasize externally observable behaviour and user-facing inputs and outputs.",
            "Call out compatibility, rollout, and migration evidence only when the Contract "
            "provides it.",
            "Keep Acceptance Criteria focused on observable behaviour and validation evidence.",
        ),
    ),
    SpecificationTemplate(
        id="bugfix",
        description="Bug fix work with reproduction and regression-protection guidance.",
        guidance=(
            "Emphasize problem reproduction, expected behaviour, and actual behaviour from "
            "Contract evidence.",
            "Define the fix boundary and regression protection without inventing a root cause.",
            "Keep validation tied to the failing scenario and any named regression tests.",
        ),
    ),
    SpecificationTemplate(
        id="refactor",
        description="Refactor work that preserves behaviour while changing structure.",
        guidance=(
            "Emphasize current design, target design, and preserved behaviour that must "
            "remain unchanged.",
            "Call out compatibility constraints, migration notes, and rollback evidence from "
            "the Contract.",
            "Keep regression protection explicit so structural changes do not alter "
            "user-visible behaviour.",
        ),
    ),
    SpecificationTemplate(
        id="docs",
        description="Documentation work with audience, examples, and source-accuracy guidance.",
        guidance=(
            "Emphasize target audience, documentation surfaces, examples, and source accuracy.",
            "Validate links, commands, and examples when the Contract requires them.",
            "Keep compatibility and migration notes grounded in documented behaviour.",
        ),
    ),
    SpecificationTemplate(
        id="internal",
        description="Internal operational work with safety, observability, and rollback guidance.",
        guidance=(
            "Emphasize operational constraints, internal interfaces, observability, and "
            "safety boundaries.",
            "Document rollback and failure handling from Contract evidence.",
            "Do not imply public API changes unless the Contract explicitly says so.",
        ),
    ),
    SpecificationTemplate(
        id="generic",
        description=(
            "Historical Task 042-compatible fallback for Contracts without a "
            "Specification Type."
        ),
        guidance=(
            "Preserve the generic Task 042 evidence mapping and avoid task-type assumptions.",
            "Use fixed fallback text for missing evidence rather than inventing missing facts.",
            "Keep all guidance grounded in Goal, Issue, Task Contract, and Roadmap evidence.",
        ),
    ),
)


class SpecificationTemplateRegistry:
    def __init__(self, templates: tuple[SpecificationTemplate, ...]) -> None:
        ids = [template.id for template in templates]
        duplicates = sorted({template_id for template_id in ids if ids.count(template_id) > 1})
        if duplicates:
            raise DuplicateSpecificationTemplateError(
                f"duplicate Specification template IDs: {', '.join(duplicates)}"
            )
        self._templates = tuple(sorted(templates, key=lambda template: template.id))
        self._by_id = {template.id: template for template in self._templates}

    def list(self) -> tuple[SpecificationTemplate, ...]:
        return self._templates

    def get(self, template_id: str) -> SpecificationTemplate:
        normalized = normalize_template_id(template_id)
        try:
            return self._by_id[normalized]
        except KeyError as exc:
            raise UnknownSpecificationTemplateError(
                f"unknown Specification template: {template_id!r}"
            ) from exc


REGISTRY = SpecificationTemplateRegistry(_BUILTIN_TEMPLATES)


def normalize_template_id(value: str) -> str:
    return value.strip().lower()


def list_specification_templates() -> tuple[SpecificationTemplate, ...]:
    return REGISTRY.list()


def get_specification_template(template_id: str) -> SpecificationTemplate:
    return REGISTRY.get(template_id)


def select_specification_template(
    contract_sections: dict[str, str], *, override: str | None = None
) -> TemplateSelection:
    if override is not None:
        return TemplateSelection(
            template=get_specification_template(override),
            source=TemplateSelectionSource.OVERRIDE,
        )

    explicit = contract_sections.get("Specification Type")
    if explicit is None or not explicit.strip():
        return TemplateSelection(
            template=get_specification_template("generic"),
            source=TemplateSelectionSource.DEFAULT,
        )

    return TemplateSelection(
        template=get_specification_template(explicit),
        source=TemplateSelectionSource.CONTRACT,
    )


def render_template_policy(template: SpecificationTemplate) -> str:
    lines = [
        f"template: {template.id}",
        f"description: {template.description}",
        "guidance:",
    ]
    lines.extend(f"- {item}" for item in template.guidance)
    return "\n".join(lines) + "\n"
