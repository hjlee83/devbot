"""Shared GitHub API retry policy."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import requests

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.25
DEFAULT_MAX_DELAY_SECONDS = 5.0
DEFAULT_JITTER_RATIO = 0.2
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class GitHubRetryConfig:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS
    jitter_ratio: float = DEFAULT_JITTER_RATIO
    sleep: Callable[[float], None] = time.sleep
    random: Callable[[], float] = random.random


@dataclass(frozen=True, slots=True)
class GitHubRetryDiagnostic:
    status: int | None
    attempt: int
    delay_seconds: float
    endpoint_category: str
    outcome: str
    error_type: str | None = None


class GitHubTransientError(RuntimeError):
    """Raised after transient GitHub failures exhaust bounded retries."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        endpoint_category: str = "unknown",
        attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.endpoint_category = endpoint_category
        self.attempts = attempts


def is_transient_status(status: int) -> bool:
    return status in TRANSIENT_HTTP_STATUSES


def classify_github_failure(exc: BaseException | None = None, *, status: int | None = None) -> str:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return "transient"
    if status is None:
        return "unknown"
    if is_transient_status(status):
        return "transient"
    if status in (401, 403):
        return "authentication_or_permission"
    if status == 404:
        return "not_found"
    return "permanent"


def retry_after_delay(value: str | None, *, now: Callable[[], float] = time.time) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        seconds = float(stripped)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        seconds = parsed.timestamp() - now()
    return max(0.0, seconds)


def bounded_backoff_delay(
    attempt: int,
    config: GitHubRetryConfig,
    *,
    retry_after: str | None = None,
) -> float:
    retry_after_seconds = retry_after_delay(retry_after)
    if retry_after_seconds is not None:
        return min(retry_after_seconds, config.max_delay_seconds)

    base = config.base_delay_seconds * (2 ** max(attempt - 1, 0))
    jitter = base * config.jitter_ratio * config.random()
    return min(base + jitter, config.max_delay_seconds)


def execute_with_github_retry[T](
    operation: Callable[[], T],
    *,
    config: GitHubRetryConfig,
    endpoint_category: str,
    logger: logging.Logger | None = None,
) -> T:
    attempts = max(config.max_attempts, 1)
    last_status: int | None = None
    last_error_type: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            response_or_result = operation()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error_type = type(exc).__name__
            if attempt >= attempts:
                _log_retry(
                    logger,
                    GitHubRetryDiagnostic(
                        status=None,
                        attempt=attempt,
                        delay_seconds=0.0,
                        endpoint_category=endpoint_category,
                        outcome="exhausted",
                        error_type=last_error_type,
                    ),
                )
                raise GitHubTransientError(
                    f"Transient GitHub API failure exhausted after {attempts} attempts",
                    endpoint_category=endpoint_category,
                    attempts=attempts,
                ) from exc
            delay = bounded_backoff_delay(attempt, config)
            _log_retry(
                logger,
                GitHubRetryDiagnostic(
                    status=None,
                    attempt=attempt,
                    delay_seconds=delay,
                    endpoint_category=endpoint_category,
                    outcome="retrying",
                    error_type=last_error_type,
                ),
            )
            config.sleep(delay)
            continue

        status_code = getattr(response_or_result, "status_code", None)
        if isinstance(status_code, int) and is_transient_status(status_code):
            last_status = response_or_result.status_code
            if attempt >= attempts:
                _log_retry(
                    logger,
                    GitHubRetryDiagnostic(
                        status=last_status,
                        attempt=attempt,
                        delay_seconds=0.0,
                        endpoint_category=endpoint_category,
                        outcome="exhausted",
                    ),
                )
                message = (
                    f"Transient GitHub API failure {last_status} exhausted "
                    f"after {attempts} attempts"
                )
                raise GitHubTransientError(
                    message,
                    status=last_status,
                    endpoint_category=endpoint_category,
                    attempts=attempts,
                )
            delay = bounded_backoff_delay(
                attempt, config, retry_after=response_or_result.headers.get("Retry-After")
            )
            _log_retry(
                logger,
                GitHubRetryDiagnostic(
                    status=last_status,
                    attempt=attempt,
                    delay_seconds=delay,
                    endpoint_category=endpoint_category,
                    outcome="retrying",
                ),
            )
            config.sleep(delay)
            continue

        if attempt > 1:
            _log_retry(
                logger,
                GitHubRetryDiagnostic(
                    status=getattr(response_or_result, "status_code", last_status),
                    attempt=attempt,
                    delay_seconds=0.0,
                    endpoint_category=endpoint_category,
                    outcome="succeeded",
                    error_type=last_error_type,
                ),
            )
        return response_or_result

    raise GitHubTransientError(
        "Transient GitHub API failure exhausted",
        status=last_status,
        endpoint_category=endpoint_category,
        attempts=attempts,
    )


def _log_retry(logger: logging.Logger | None, diagnostic: GitHubRetryDiagnostic) -> None:
    if logger is None:
        return
    logger.info(
        "github_api_retry status=%s attempt=%d delay_seconds=%.3f endpoint_category=%s "
        "outcome=%s error_type=%s",
        diagnostic.status,
        diagnostic.attempt,
        diagnostic.delay_seconds,
        diagnostic.endpoint_category,
        diagnostic.outcome,
        diagnostic.error_type,
    )


__all__ = [
    "GitHubRetryConfig",
    "GitHubRetryDiagnostic",
    "GitHubTransientError",
    "bounded_backoff_delay",
    "classify_github_failure",
    "execute_with_github_retry",
    "is_transient_status",
    "retry_after_delay",
]
