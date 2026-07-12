# DevBot Project Rules

## Runtime and tooling
- Python 3.13
- `uv` for dependency and command management
- `pytest` for tests
- `ruff` for linting
- Public functions, methods, and data structures must use type hints.
- The project must run on both macOS and Linux VPS environments.

## Safety
- Never commit tokens, API keys, credentials, or production data.
- GitHub write operations and external agent execution default to dry-run.
- Never commit or push directly to `main` or `master`.
- Work only on a task branch.
- Do not declare a task complete while required verification is failing.

## Task workflow
1. Read this file.
2. Read the assigned `tasks/NNN-*.md`.
3. Implement only the stated scope.
4. Write the required unit tests.
5. Map every quality checkpoint to one or more concrete test names.
6. Run all verification gates.
7. Write `results/NNN-*.md`.
8. Commit on the task branch only after all gates pass.
9. Push the branch and create a PR when the Task explicitly permits it.

## Documentation
- Keep implementation, tests, and documentation synchronized.
- Record material architecture decisions in `docs/07-decisions.md`.
