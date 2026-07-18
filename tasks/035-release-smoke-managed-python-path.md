# Task 035: Release Smoke Uses Managed Python Path

## Goal

Fix the v0.1.0 Release workflow smoke step so each platform artifact is tested with the Python 3.13 executable installed by `astral-sh/setup-uv`, without changing the artifact launcher contract.

## Context

The v0.1.0 Release workflow run failed in the Linux `build-artifacts` matrix job after the artifact built successfully. The packaged launcher requires `python3.13` on `PATH`; `setup-uv` installed Python 3.13, but the shell PATH did not expose a `python3.13` executable for the launcher.

Failed workflow evidence:

- Run: https://github.com/hjlee83/devbot/actions/runs/29631733326
- Failed job: `build-artifacts (ubuntu-latest, linux, x86_64)`
- Failed step: `Smoke packaged DevBot`
- Error: `devbot requires Python 3.13 on PATH`

## In Scope

- Update only the Release workflow smoke command behavior.
- Add workflow validation coverage so the managed Python PATH setup cannot regress.
- Document the fix and validation evidence.

## Out of Scope

- Changing the packaged launcher.
- Adding a runtime dependency on `uv` to installed artifacts.
- Weakening Python 3.13 validation.
- Creating, moving, or publishing the `v0.1.0` tag or GitHub Release.
- Changing release artifact contents, names, checksums, or publication behavior.

## Functional Requirements

1. The `Smoke packaged DevBot` step must resolve Python 3.13 with:

   ```bash
   PYTHON_BIN="$(uv python find 3.13)"
   ```

2. The smoke command must prepend that executable directory to `PATH` while running the packaged launcher:

   ```bash
   PATH="$(dirname "$PYTHON_BIN"):$PATH"      smoke/devbot-release/bin/devbot --version
   ```

3. The same smoke step must be used by both matrix jobs:
   - `ubuntu-latest / linux / x86_64`
   - `macos-14 / macos / arm64`
4. The installed artifact launcher contract remains unchanged: artifacts require Python 3.13 on `PATH` and do not require `uv` at runtime.
5. No manual release or tag operation is allowed.

## Quality Gates

### CP-035-1 - Managed Python smoke PATH

Required test:
- `test_release_workflow_uses_platform_artifact_matrix_and_manual_dispatch`

### CP-035-2 - Existing launcher contract preserved

Evidence:
- No launcher or artifact builder source changes.
- Workflow smoke step only changes the CI environment PATH for the packaged command.

### CP-035-3 - No release publication

Evidence:
- No `v0.1.0` tag or Release is created by this task.

### CP-035-4 - Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Git Rules

- Task Issue: #75
- Branch: `task/035-release-smoke-managed-python-path`
- Pull Request: TBD
- Result: `results/035-release-smoke-managed-python-path.md`
- Do not create another Issue, Branch, or Pull Request for this task.
