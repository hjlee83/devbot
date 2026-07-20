from pathlib import Path

import pytest

# `load_config` calls `load_dotenv`, which writes directly into the real
# process `os.environ` (bypassing `monkeypatch`'s own change tracking).
# Without clearing these before every test, a value set by one test (e.g.
# an invalid DRY_RUN) would leak into unrelated tests later in the session.
_CONFIG_ENV_VARS = (
    "WORKSPACE_ROOT",
    "GITHUB_TOKEN",
    "POLL_INTERVAL_SECONDS",
    "DEVBOT_LOCK_FILE",
    "DEFAULT_AGENT",
    "MAX_CONCURRENT_JOBS",
    "AI_CONCURRENCY",
    "DRY_RUN",
    "LOG_LEVEL",
    "DEVBOT_REGISTRY_PATH",
)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Issue #122: `devbot.repository_registry.default_registry_path()`
    # falls back to `~/.devbot/registry.yaml` when `DEVBOT_REGISTRY_PATH`
    # is unset - point every test at a per-test path that is guaranteed not
    # to exist, so no test's result depends on whatever `devbot init` may
    # or may not have written to the *real* home directory on the machine
    # running the suite.
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(tmp_path / "unused-registry.yaml"))
