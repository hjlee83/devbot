#!/usr/bin/env bash
set -euo pipefail

uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
