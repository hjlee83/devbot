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
    "DRY_RUN",
    "LOG_LEVEL",
    "DEVBOT_INSTALL_ROOT",
    "DEVBOT_INSTALL_METADATA",
    "DEVBOT_REPOSITORIES_PATH",
)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
