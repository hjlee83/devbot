#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.uv-cache}"

required=(git uv python3 codex)
failed=0

for command_name in "${required[@]}"; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'OK   %s: %s\n' "$command_name" "$(command -v "$command_name")"
  else
    printf 'MISS %s\n' "$command_name"
    failed=1
  fi
done

mkdir -p "$UV_CACHE_DIR"
if [[ -w "$UV_CACHE_DIR" ]]; then
  printf 'OK   UV_CACHE_DIR: %s\n' "$UV_CACHE_DIR"
else
  printf 'MISS UV_CACHE_DIR not writable: %s\n' "$UV_CACHE_DIR"
  failed=1
fi

exit "$failed"
