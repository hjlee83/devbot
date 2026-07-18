# Task 042: Specification Generator

## Goal

Introduce a Specification Generator as a first-class artifact in the DevBot
workflow. The Specification becomes the authoritative implementation document a
future Agent Dispatch would hand to an implementation Agent (Claude, Codex, GPT,
Gemini, ...) instead of a manually written prompt - generated deterministically from
real repository evidence, never from speculation.

## Context

Current workflow: Goal → Planner → Issue → Contract. Task Contracts
(`tasks/NNN-slug.md`) already carry the real implementation scope, but this
repository's 41 prior Contracts use noticeably different heading vocabularies -
English and Korean both appear ("Goal"/"목표", "Out of Scope"/"제외 범위", "Git
Rules"/"Git 규칙", ...), and Tasks 001-026 either have no tracked GitHub Issue at all
(001-009) or used the pre-Task-022 "Execute Task NNN: ..." two-issue naming instead
of the canonical single-Issue "Task NNN: ..." title `docs/12-planner-workflow.md`
established. A Specification Generator that reorganizes this heterogeneous evidence
into one fixed, predictable structure - while never inventing content a Contract
doesn't actually contain - is the natural next step before any future Dispatch
mechanism can consume it uniformly.

New workflow: Goal → Planner → Issue → Contract → **Specification** → Dispatch.

## In Scope

1. `src/devbot/specification.py`: evidence gathering (`gather_task_evidence`),
   deterministic rendering (`render_specification`), schema validation
   (`validate_specification_schema`), and file writing (`write_specification`).
2. `devbot specification generate --task <task-number>` (writes
   `specifications/NNN-slug.md`) and `devbot specification show --task <task-number>`
   (read-only preview, no write).
3. Fail-closed error handling for every listed unsafe condition (see Safety).
4. Documentation of the Specification lifecycle, the Contract/Specification
   relationship, generation rules, and future Dispatch integration.

## Out of Scope

- Any change to `devbot.workspace.build_agent_prompt`, `devbot.polling`,
  `devbot.review`, or `devbot.rework` - this Task prepares the artifact a future
  Dispatch mechanism could read; it does not wire it in.
- Invoking any implementation Agent - `specification.py` never imports
  `devbot.agents` and never calls an `AgentRunner`.
- Bulk-generating and committing Specifications for all 41 prior Tasks - live
  verification (below) generates several as read-only proof, but only Task 042's own
  Specification is committed as a deliverable.
- Modifying any existing `tasks/*.md` or `results/*.md` file.
- A formal schema/grammar for `tasks/*.md` Contracts (would require rewriting 41
  existing files); this Task's parser instead uses a best-effort bilingual heading
  alias table plus a verbatim full-Contract fallback, so nothing is ever lost even
  when the alias table doesn't recognize a heading.

## Specification Structure

```
# Specification: Task NNN — <Title>

## Provenance                      (Issue link, Contract path, generation note)

# Overview
## Goal
## Scope                           (In scope / Out of scope)
## Background
## Roadmap Context

# Functional Requirements
## Required Behaviour
## Acceptance Criteria             (from the Contract's Quality Gates/Checkpoints)
## Out of Scope

# Technical Design
## Architecture                    ("Not specified" unless the Contract has one)
## Files Expected to Change
## Dependencies
## Constraints                     (from Risk)
## Migration Notes                 (from Rollback Strategy)

# Validation
## Required Tests and Quality Gates
## Validation Commands
## Success Criteria                (from Definition of Done)

# Safety
## Things the Implementation Agent Must NOT Do   (from Out of Scope + Risk)

# Completion
## Expected Deliverables
## Result Document
## PR Expectations                 (from Git Rules)

# Handoff
## Required Handoff Procedure      ("Not specified" + a grounded reference to
## Token-Limit Behaviour            docs/12-planner-workflow.md's role boundary)

# Full Task Contract Reference      (the complete, unmodified Contract text -
                                     authoritative if anything above disagrees)
```

Every value not resolvable from the Contract/Issue/roadmap renders the fixed string
"Not specified in the Task Contract." - never invented prose.

## Functional Requirements

1. `devbot specification generate --task <N>` and `devbot specification show --task
   <N>` gather evidence from exactly one `tasks/NNN-*.md` Contract, exactly one
   GitHub Issue titled `Task NNN: <title>` (matching the Contract's own title
   exactly), and (optionally) the corresponding `docs/00-roadmap.md` entry.
2. Generation is a pure function of that gathered evidence - no timestamps, no
   random ordering, no network call inside the render step. Identical repository
   state (same Contract bytes, same Issue title/body, same roadmap text) always
   renders byte-identical output.
3. A Contract section this module's alias table cannot map renders "Not specified in
   the Task Contract." - never guessed at. The complete, unmodified Contract text is
   always appended verbatim in a final "Full Task Contract Reference" section, so no
   information is ever lost even when the alias mapping misses something.
4. `devbot specification generate` writes to `specifications/NNN-slug.md`;
   `devbot specification show` (and `generate --dry-run`) only print to stdout.
5. Every generated Specification contains all of `REQUIRED_TOP_LEVEL_SECTIONS`
   (`validate_specification_schema` returns empty for it).
6. This Task changes nothing about how any existing Agent is dispatched.

## Safety

Generation fails closed (`SpecificationError` subclasses, never a partial/best-guess
Specification) when:

- the Task Contract is missing (`ContractMissingError`);
- more than one Contract file matches the Task number (`ContractAmbiguousError`);
- the Task number is not a positive integer, or the Contract file does not start
  with the canonical `# Task NNN: <title>` heading (`InvalidTaskError`);
- no GitHub Issue titled `Task NNN: ...` exists (`IssueMissingError`);
- more than one such Issue exists, or the Issue's title text disagrees with the
  Contract's own title (`AmbiguousTaskError`);
- the Contract has no identifiable Goal, Background, Scope, or Functional
  Requirements content at all (`PlannerEvidenceMissingError`).

## Quality Gates

### CP-042-1 - Specification generation and determinism

Required tests:
- `test_gather_task_evidence_succeeds_for_well_formed_task`
- `test_generate_specification_end_to_end`
- `test_render_specification_is_deterministic`
- `test_repeated_generation_produces_identical_files`

### CP-042-2 - Missing Contract / missing Issue

Required tests:
- `test_missing_contract_raises`
- `test_missing_issue_raises`

### CP-042-3 - Invalid Task / ambiguous Task

Required tests:
- `test_invalid_task_number_raises_for_zero_and_negative`
- `test_invalid_task_raises_when_contract_lacks_canonical_title`
- `test_ambiguous_contract_raises_when_multiple_files_match`
- `test_ambiguous_task_raises_when_multiple_issues_match`
- `test_ambiguous_task_raises_on_contract_issue_title_mismatch`
- `test_planner_evidence_missing_raises_for_empty_contract`

### CP-042-4 - Output schema validation and required sections

Required tests:
- `test_render_specification_includes_required_top_level_sections`
- `test_validate_specification_schema_reports_missing_sections`
- `test_validate_specification_schema_empty_when_complete`

### CP-042-5 - Repository evidence only, no hallucinated content

Required tests:
- `test_render_specification_uses_not_specified_default_for_missing_sections`
- `test_render_specification_includes_full_contract_verbatim`
- `test_render_specification_only_contains_grounded_content`
- `test_goal_text_extracted_from_goal_executor_issue_body`
- `test_roadmap_excerpt_included_when_entry_exists`
- `test_roadmap_excerpt_none_when_roadmap_missing`

### CP-042-6 - Contract parsing correctness

Required tests:
- `test_parse_contract_extracts_title_and_sections`
- `test_parse_contract_handles_missing_title`
- `test_parse_contract_keeps_level3_headings_inside_parent_body`
- `test_parse_contract_concatenates_duplicate_headings`

### CP-042-7 - CLI: read-only `show`, local-write `generate`

Required tests:
- `test_specification_show_is_read_only`
- `test_specification_generate_writes_file`
- `test_specification_generate_dry_run_does_not_write`
- `test_specification_error_returns_failure_exit_code`
- `test_specification_command_does_not_acquire_daemon_lock`
- `test_write_specification_creates_directory_if_missing`

### CP-042-8 - Documentation and evidence

Required evidence:
- `results/042-specification-generator.md`
- `docs/00-roadmap.md` (Task 042 entry)
- `docs/08-beta-runbook.md` (Specification lifecycle, Contract/Specification
  relationship, generation rules, future Dispatch integration)

### CP-042-9 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Validation Gate

```bash
uv run ruff check .
uv run pytest
```

Read-only live verification against `hjlee83/devbot`: `devbot specification show
--task <N>` for several historical Tasks spanning different Contract heading styles
(recorded in the Result document, including the expected `IssueMissingError` for a
pre-Task-022 Task, which is correct fail-closed behavior, not a bug). No historical
Task's `tasks/*.md`/`results/*.md` is modified. No Agent is dispatched.

## Files Expected to Change

- `src/devbot/specification.py` (new)
- `src/devbot/main.py` (`devbot specification generate|show` CLI wiring)
- `tests/test_specification.py` (new), `tests/test_main.py`
- `docs/00-roadmap.md`, `docs/08-beta-runbook.md`
- `tasks/042-specification-generator.md`, `results/042-specification-generator.md`
- `specifications/042-specification-generator.md` (this Task's own generated
  Specification, committed as a genuine self-referential deliverable)

## Risk

- The bilingual heading-alias table is hand-curated from a survey of this
  repository's actual `tasks/*.md` headings; a future Contract using an
  unanticipated heading phrase would fall back to "Not specified in the Task
  Contract" for that concept - safe (never wrong information) but less complete.
  Mitigated by the verbatim full-Contract fallback section, which always has the
  true content regardless.
- Contract/Issue title cross-checking (`AmbiguousTaskError`) is an exact string
  match after stripping the `Task NNN: ` prefix - a Contract whose H1 differs from
  its Issue title by even punctuation fails closed rather than guessing which is
  authoritative. This is a deliberate, conservative choice, not an oversight.
- `specifications/` is a new top-level directory; no `.gitignore`/tooling changes
  were needed since it holds ordinary tracked markdown, matching `tasks/`/`results/`.

## Rollback Strategy

Revert this Task's commits. `specification.py` and its CLI are fully additive; no
existing module's behavior changes.

## Reviewer Focus

- Confirm `specification.py` never imports `devbot.agents` and never calls
  `AgentRunner`/`build_agent_runner`.
- Confirm every "Not specified in the Task Contract." occurrence in a test's
  expected output corresponds to a concept genuinely absent from that test's
  synthetic Contract - not a parsing bug masquerading as an honest fallback.
- Confirm `test_render_specification_includes_full_contract_verbatim` and
  `test_render_specification_only_contains_grounded_content` actually assert
  content equality/containment against the literal fixture text, not a loose
  substring check that could pass accidentally.
- Confirm no `tasks/*.md` or `results/*.md` file was modified by this Task.

## Definition of Done

- All Quality Gates and required tests pass (`uv run pytest`, `uv run ruff check .`).
- `devbot specification show --task <N>` was run for real against `hjlee83/devbot`
  for several historical Tasks with different Contract heading styles, recorded in
  the Result document.
- `specifications/042-specification-generator.md` was generated from this Task's
  own finished Contract and committed as a deliverable.
- No existing historical Task's Contract or Result was modified.
- No Agent was dispatched during this Task's own verification.
- Result and PR Evidence record the actual implementation, files changed, and
  validation outcomes.
- DevBot reaches a mergeable state on Issue #89 and its Pull Request.
- Final merge remains manual.

## Result 문서 경로

`results/042-specification-generator.md`

## Git Rules

- Task Issue: #89
- Branch: `task/042-specification-generator`
- Pull Request: #90
- Result: `results/042-specification-generator.md`
- Do not create another Issue, Branch, or Pull Request for this Task.
