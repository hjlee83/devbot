# Task 036: Release Tag Git Identity

## Goal

Fix the GitHub Release workflow so annotated `v0.1.0` tag creation succeeds on GitHub Actions runners by configuring the local Git committer identity before `git tag --annotate`.

## Context

The v0.1.0 Release workflow reached `publish-release` and failed at `Create immutable tag`.

Evidence:

- Failed run: https://github.com/hjlee83/devbot/actions/runs/29632925759
- Failed job: `publish-release`
- Failed step: `Create immutable tag`
- Error: `Committer identity unknown` / `fatal: empty ident name ... not allowed`

The release pipeline had already passed validation, release planning, platform artifact builds, packaged CLI smoke tests, and checksum generation before the tag step failed.

## In Scope

- Update `.github/workflows/release.yml` to configure Git identity before annotated tag creation.
- Add workflow regression coverage proving the Git identity commands appear before `git tag --annotate`.
- Update Result and roadmap evidence.

## Out of Scope

- Creating or publishing `v0.1.0`.
- Creating, moving, or deleting tags manually.
- Manual GitHub Release workaround.
- Changing artifact contents, artifact names, release notes, version planning, draft-first publication, or idempotent retry behavior.

## Functional Requirements

1. Before `git tag --annotate`, the `Create immutable tag` step must run:

   ```bash
   git config user.name "github-actions[bot]"
   git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
   ```

2. Existing release safety policies remain unchanged:
   - immutable tag
   - exact target commit
   - draft-first publication
   - idempotent retry
   - no manual tag movement
   - no manual Release workaround
3. The workflow must still create the tag only when `env.release_state == 'create'`.
4. This task must not create or publish `v0.1.0`.

## Quality Gates

### CP-036-1 - Git identity before annotated tag

Required test:
- `test_release_workflow_configures_git_identity_before_annotated_tag`

### CP-036-2 - Release safety preserved

Required tests:
- Existing release workflow tests in `tests/test_release.py`

### CP-036-3 - Documentation and evidence

Required evidence:
- `results/036-release-tag-git-identity.md`
- `docs/00-roadmap.md`

### CP-036-4 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Git Rules

- Task Issue: #77
- Branch: `task/036-release-tag-git-identity`
- Pull Request: TBD
- Result: `results/036-release-tag-git-identity.md`
- Do not create another Issue, Branch, or Pull Request for this task.
- Do not create or publish `v0.1.0` as part of this task.
