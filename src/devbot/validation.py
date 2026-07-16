"""Prepared-workspace validation helpers.

Validation commands must run from the Task workspace itself and must not inherit
the operator checkout's active virtual environment. This module keeps that
policy in one place for delivery and rework.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from devbot.models import RepositoryConfig

ENVIRONMENT_PREPARATION_COMMAND: tuple[str, ...] = ("uv", "sync")
DEFAULT_VALIDATION_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "pytest"),
)


class ValidationFailureCategory(StrEnum):
    """Precise class for validation failures that need different recovery."""

    ENVIRONMENT_PREPARATION_FAILED = "environment_preparation_failed"
    DEPENDENCY_NETWORK_UNAVAILABLE = "dependency_network_unavailable"
    VALIDATION_COMMAND_FAILED = "validation_command_failed"
    FORBIDDEN_HOST_FALLBACK = "forbidden_host_fallback"


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """One validation command and its observed outcome."""

    command: tuple[str, ...]
    returncode: int
    output: str = ""


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    """Structured evidence suitable for Result and PR Evidence rendering."""

    workspace_path: str
    command: tuple[str, ...]
    exit_code: int
    output_summary: str = ""


def workspace_validation_env(
    repository: RepositoryConfig,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment pinned to `repository.local_path`'s `.venv`.

    Removing `VIRTUAL_ENV` prevents a shell or parent process from leaking the
    operator checkout's activated virtualenv into a prepared workspace. The
    workspace `.venv/bin` path is prepended so subprocesses that consult PATH
    resolve local tools first after `uv sync` creates the environment.
    """

    env = dict(os.environ if base_env is None else base_env)
    env.pop("VIRTUAL_ENV", None)
    workspace_bin = repository.local_path / ".venv" / "bin"
    existing_path = env.get("PATH", "")
    env["PATH"] = (
        f"{workspace_bin}{os.pathsep}{existing_path}" if existing_path else str(workspace_bin)
    )
    env["DEVBOT_VALIDATION_WORKSPACE"] = str(repository.local_path)
    return env


def classify_validation_failure(
    *,
    command: Sequence[str],
    returncode: int,
    output: str,
    host_checkout_path: str | None = None,
) -> ValidationFailureCategory:
    """Classify validation failures without hiding repository-fixable errors."""

    if host_checkout_path and host_checkout_path in output:
        return ValidationFailureCategory.FORBIDDEN_HOST_FALLBACK
    lowered_output = output.lower()
    if tuple(command) == ENVIRONMENT_PREPARATION_COMMAND:
        if any(token in lowered_output for token in ("network", "download", "resolve", "dns")):
            return ValidationFailureCategory.DEPENDENCY_NETWORK_UNAVAILABLE
        return ValidationFailureCategory.ENVIRONMENT_PREPARATION_FAILED
    if returncode != 0:
        return ValidationFailureCategory.VALIDATION_COMMAND_FAILED
    return ValidationFailureCategory.VALIDATION_COMMAND_FAILED


def run_validation_command(
    repository: RepositoryConfig,
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> CommandExecution:
    """Run one command in the prepared workspace and capture combined output."""

    completed = subprocess.run(
        list(command),
        cwd=str(repository.local_path),
        env=dict(env) if env is not None else workspace_validation_env(repository),
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandExecution(
        command=tuple(command),
        returncode=completed.returncode,
        output=(completed.stdout or "") + (completed.stderr or ""),
    )


def validation_commands_with_environment(
    commands: Sequence[Sequence[str]] = DEFAULT_VALIDATION_COMMANDS,
) -> tuple[tuple[str, ...], ...]:
    """Return the environment preparation command followed by validations."""

    return (ENVIRONMENT_PREPARATION_COMMAND, *(tuple(command) for command in commands))


def validation_evidence_from_execution(
    repository: RepositoryConfig,
    execution: CommandExecution,
    *,
    output_limit: int = 500,
) -> ValidationEvidence:
    """Convert one command execution into compact evidence."""

    return ValidationEvidence(
        workspace_path=str(repository.local_path),
        command=execution.command,
        exit_code=execution.returncode,
        output_summary=execution.output[:output_limit],
    )
