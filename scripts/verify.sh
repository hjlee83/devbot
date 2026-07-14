#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.uv-cache}"

uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
