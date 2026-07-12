#!/usr/bin/env bash
set -euo pipefail

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

exit "$failed"
