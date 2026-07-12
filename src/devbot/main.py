"""DevBot CLI entry point.

`uv run devbot` loads configuration, acquires the single-process lock,
validates enabled repository paths, prints the managed repositories, prints
`DevBot started`, then exits cleanly, releasing the lock.
"""

from __future__ import annotations

import sys
from pathlib import Path

from devbot.config import ConfigError, load_config
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.workspace import WorkspaceValidationError, validate_repository_paths


def main(
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
) -> int:
    try:
        config = load_config(env_path=env_path, repositories_path=repositories_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        with ProcessLock(config.lock_file):
            try:
                validated = validate_repository_paths(config.repositories)
            except WorkspaceValidationError as exc:
                print(f"Workspace validation error: {exc}", file=sys.stderr)
                return 1

            print("Managed repositories:")
            if validated:
                for repository in validated:
                    print(f"  - {repository.full_name} -> {repository.local_path}")
            else:
                print("  (none enabled)")

            print("DevBot started")
    except LockAcquisitionError as exc:
        print(f"Lock error: {exc}", file=sys.stderr)
        return 1

    return 0


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
