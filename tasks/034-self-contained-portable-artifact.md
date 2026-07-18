# Task 034: Self-contained Portable Release Artifact

## Goal

Fix the v0.1.0 portable release artifact so the packaged DevBot CLI runs from the extracted artifact without relying on Python packages already installed on the host.

## Context

The attempted v0.1.0 publication was stopped before tag or Release creation. The existing Task 032/033 release pipeline built `devbot-0.1.0-portable-python.tar.gz`, but the smoke test failed at `smoke/devbot-release/bin/devbot --version` with `ModuleNotFoundError: No module named 'dotenv'`. The artifact contained DevBot source and `uv.lock`, but the launcher invoked system Python with only `src` on `PYTHONPATH`.

## In scope

- Keep v0.1.0 unpublished; do not create a GitHub Release or tag.
- Make the portable Python artifact self-contained for supported macOS/Linux builds by embedding locked runtime dependencies.
- Preserve dependency versions from `uv.lock`.
- Ensure the launcher uses the artifact-local runtime environment before any host Python packages.
- Keep deterministic artifact generation and `SHA256SUMS` behavior.
- Add clean-environment smoke tests for the extracted artifact.
- Update the Result with implementation and validation evidence.

## Out of scope

- Publishing v0.1.0.
- Creating or moving `v0.1.0` tag.
- Changing package-manager distribution or native installers.
- Creating additional branches, issues, or PRs.

## Deliverables

- `tasks/034-self-contained-portable-artifact.md`
- `results/034-self-contained-portable-artifact.md`
- Release artifact builder changes
- Regression tests for clean-host artifact execution

## Functional requirements

1. `build_artifact()` must include DevBot runtime dependencies inside `devbot-release/vendor/`.
2. Embedded dependency versions must match `uv.lock` for the runtime dependency graph.
3. The artifact launcher must set `PYTHONPATH` so `vendor` and `src` are used before host packages.
4. The launcher must not require `python-dotenv`, PyYAML, Requests, or transitive runtime dependencies to be installed on the host.
5. Extracted artifact smoke command `devbot-release/bin/devbot --version` must print `devbot 0.1.0` in a clean Python virtual environment.
6. Artifact generation must remain deterministic and `SHA256SUMS` must cover the artifact exactly.

## Quality gates

### CP-034-1 — Locked dependency strategy

Required test:
- `test_release_artifact_vendors_locked_runtime_dependencies`

### CP-034-2 — Artifact-local launcher

Required test:
- `test_packaged_cli_uses_artifact_vendor_before_host_packages`

### CP-034-3 — Clean environment smoke

Required test:
- `test_packaged_cli_runs_in_clean_virtualenv_without_runtime_dependencies`

### CP-034-4 — Deterministic artifact and checksum

Required tests:
- `test_release_artifact_generation_is_reproducible`
- `test_checksum_manifest_covers_every_release_artifact`

### CP-034-5 — Existing release behavior preserved

Required test:
- Existing release planning and notes tests in `tests/test_release.py`

### CP-034-6 — Validation gate

Required commands:
- `uv run ruff check .`
- `uv run pytest`

## Git rules

- Task Issue: #73
- Branch: `task/034-self-contained-portable-artifact`
- Pull Request: TBD
- Do not create another Issue, Branch, or Pull Request.
- Do not publish v0.1.0 or create `v0.1.0` tag.
