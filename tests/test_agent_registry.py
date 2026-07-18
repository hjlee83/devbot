from __future__ import annotations

from pathlib import Path

import pytest

from devbot.agent_registry import (
    AgentDescriptor,
    AgentRegistry,
    AgentRegistryError,
    NoAgentAvailableError,
    RoleNotConfiguredError,
    RoleRoutingConfig,
    list_roles,
    load_agent_registry,
    parse_agent_registry,
    resolve_agent,
    synthesize_registry_from_config,
)
from devbot.models import DevBotConfig


def _config(**overrides: object) -> DevBotConfig:
    defaults: dict[str, object] = dict(
        workspace_root=Path("/tmp/nonexistent-devbot-workspace"),
        poll_interval_seconds=60,
        lock_file=Path("/tmp/devbot.lock"),
        default_agent="codex",
        implementer_agent="codex",
        reviewer_agent="claude",
        max_concurrent_jobs=1,
        dry_run=True,
        github_token="token",
        repositories=(),
    )
    defaults.update(overrides)
    return DevBotConfig(**defaults)  # type: ignore[arg-type]


def _registry(
    agents: tuple[AgentDescriptor, ...], roles: dict[str, RoleRoutingConfig]
) -> AgentRegistry:
    return AgentRegistry(roles=roles, agents=agents)


# --------------------------------------------------------------------------
# resolve_agent (the Router) - the 9 required scenarios
# --------------------------------------------------------------------------


def test_role_exists_resolves_configured_agent() -> None:
    registry = _registry(
        (AgentDescriptor(id="claude-local", backend="claude", supported_roles=("implementer",)),),
        {"implementer": RoleRoutingConfig(name="implementer")},
    )

    agent = resolve_agent(registry, "implementer")

    assert agent.id == "claude-local"


def test_role_missing_raises_role_not_configured() -> None:
    registry = _registry((), {})

    with pytest.raises(RoleNotConfiguredError, match="totally-unknown-role"):
        resolve_agent(registry, "totally-unknown-role")


def test_no_enabled_agents_raises_no_agent_available() -> None:
    registry = _registry((), {"implementer": RoleRoutingConfig(name="implementer")})

    with pytest.raises(NoAgentAvailableError, match="implementer"):
        resolve_agent(registry, "implementer")


def test_unsupported_role_raises_no_agent_available() -> None:
    """Agents exist and are enabled, but none list this role in
    `supported_roles` - distinct from "no enabled agents at all"."""
    registry = _registry(
        (
            AgentDescriptor(id="claude-local", backend="claude", supported_roles=("reviewer",)),
            AgentDescriptor(id="codex-local", backend="codex", supported_roles=("reviewer",)),
        ),
        {"implementer": RoleRoutingConfig(name="implementer")},
    )

    with pytest.raises(NoAgentAvailableError, match="implementer"):
        resolve_agent(registry, "implementer")


def test_priority_ordering_selects_highest_priority() -> None:
    registry = _registry(
        (
            AgentDescriptor(
                id="low", backend="codex", priority=10, supported_roles=("implementer",)
            ),
            AgentDescriptor(
                id="high", backend="claude", priority=90, supported_roles=("implementer",)
            ),
        ),
        {"implementer": RoleRoutingConfig(name="implementer")},
    )

    assert resolve_agent(registry, "implementer").id == "high"


def test_deterministic_tie_break_by_id() -> None:
    registry = _registry(
        (
            AgentDescriptor(
                id="zzz-agent", backend="claude", priority=50, supported_roles=("implementer",)
            ),
            AgentDescriptor(
                id="aaa-agent", backend="codex", priority=50, supported_roles=("implementer",)
            ),
        ),
        {"implementer": RoleRoutingConfig(name="implementer")},
    )

    # Same result regardless of declaration order - not "first in the list
    # wins", but "alphabetically first id wins" among equal-priority ties.
    assert resolve_agent(registry, "implementer").id == "aaa-agent"

    reordered = _registry(
        tuple(reversed(registry.agents)),
        {"implementer": RoleRoutingConfig(name="implementer")},
    )
    assert resolve_agent(reordered, "implementer").id == "aaa-agent"


def test_disabled_agents_are_skipped() -> None:
    registry = _registry(
        (
            AgentDescriptor(
                id="high-disabled",
                backend="claude",
                priority=999,
                enabled=False,
                supported_roles=("implementer",),
            ),
            AgentDescriptor(
                id="low-enabled",
                backend="codex",
                priority=10,
                enabled=True,
                supported_roles=("implementer",),
            ),
        ),
        {"implementer": RoleRoutingConfig(name="implementer")},
    )

    assert resolve_agent(registry, "implementer").id == "low-enabled"


def test_unsupported_routing_strategy_raises() -> None:
    registry = _registry(
        (AgentDescriptor(id="a", backend="claude", supported_roles=("implementer",)),),
        {"implementer": RoleRoutingConfig(name="implementer", routing="auto")},
    )

    with pytest.raises(Exception, match="unsupported routing strategy"):
        resolve_agent(registry, "implementer")


# --------------------------------------------------------------------------
# list_roles
# --------------------------------------------------------------------------


def test_list_roles_returns_sorted_role_names() -> None:
    registry = _registry(
        (),
        {
            "reviewer": RoleRoutingConfig(name="reviewer"),
            "implementer": RoleRoutingConfig(name="implementer"),
        },
    )

    assert list_roles(registry) == ("implementer", "reviewer")


# --------------------------------------------------------------------------
# parse_agent_registry
# --------------------------------------------------------------------------


def test_parse_agent_registry_builds_roles_and_agents() -> None:
    raw = {
        "roles": {"implementer": {"routing": "priority"}, "reviewer": {"routing": "priority"}},
        "agents": [
            {
                "id": "claude-local",
                "backend": "claude",
                "enabled": True,
                "priority": 100,
                "supported_roles": ["implementer", "planner"],
                "capabilities": ["github", "python", "testing"],
            },
            {
                "id": "codex-local",
                "backend": "codex",
                "enabled": True,
                "priority": 90,
                "supported_roles": ["implementer", "reviewer"],
                "capabilities": ["github", "python", "review"],
            },
        ],
    }

    registry = parse_agent_registry(raw)

    assert set(registry.roles) == {"implementer", "reviewer", "planner"}
    assert len(registry.agents) == 2
    claude = next(agent for agent in registry.agents if agent.id == "claude-local")
    assert claude.capabilities == ("github", "python", "testing")
    assert claude.supported_roles == ("implementer", "planner")


def test_parse_agent_registry_infers_role_without_explicit_roles_section() -> None:
    raw = {
        "agents": [
            {"id": "claude-local", "backend": "claude", "supported_roles": ["implementer"]},
        ]
    }

    registry = parse_agent_registry(raw)

    assert "implementer" in registry.roles
    assert registry.roles["implementer"].routing == "priority"


def test_parse_agent_registry_defaults_backend_to_id() -> None:
    raw = {"agents": [{"id": "claude", "supported_roles": ["implementer"]}]}

    registry = parse_agent_registry(raw)

    assert registry.agents[0].backend == "claude"


def test_parse_agent_registry_rejects_unknown_backend() -> None:
    raw = {"agents": [{"id": "gpt-cloud", "backend": "gpt", "supported_roles": ["implementer"]}]}

    with pytest.raises(AgentRegistryError, match="unknown backend"):
        parse_agent_registry(raw)


def test_parse_agent_registry_rejects_missing_id() -> None:
    raw = {"agents": [{"backend": "claude", "supported_roles": ["implementer"]}]}

    with pytest.raises(AgentRegistryError, match="requires a non-empty string 'id'"):
        parse_agent_registry(raw)


def test_parse_agent_registry_rejects_non_boolean_enabled() -> None:
    raw = {"agents": [{"id": "a", "backend": "claude", "enabled": "yes"}]}

    with pytest.raises(AgentRegistryError, match="enabled must be a boolean"):
        parse_agent_registry(raw)


def test_parse_agent_registry_rejects_non_integer_priority() -> None:
    raw = {"agents": [{"id": "a", "backend": "claude", "priority": "high"}]}

    with pytest.raises(AgentRegistryError, match="priority must be an integer"):
        parse_agent_registry(raw)


def test_parse_agent_registry_rejects_non_list_supported_roles() -> None:
    raw = {"agents": [{"id": "a", "backend": "claude", "supported_roles": "implementer"}]}

    with pytest.raises(AgentRegistryError, match="supported_roles must be a list"):
        parse_agent_registry(raw)


def test_parse_agent_registry_rejects_non_mapping_roles() -> None:
    with pytest.raises(AgentRegistryError, match="'roles' must be a mapping"):
        parse_agent_registry({"roles": ["implementer"], "agents": []})


def test_parse_agent_registry_rejects_non_list_agents() -> None:
    with pytest.raises(AgentRegistryError, match="'agents' must be a list"):
        parse_agent_registry({"agents": {"id": "a"}})


# --------------------------------------------------------------------------
# synthesize_registry_from_config: backward compatibility
# --------------------------------------------------------------------------


def test_synthesize_registry_matches_legacy_config_fields() -> None:
    config = _config(implementer_agent="codex", reviewer_agent="claude")

    registry = synthesize_registry_from_config(config)

    assert resolve_agent(registry, "implementer").backend == "codex"
    assert resolve_agent(registry, "reviewer").backend == "claude"


def test_synthesize_registry_dedupes_same_backend_for_both_roles() -> None:
    config = _config(implementer_agent="claude", reviewer_agent="claude")

    registry = synthesize_registry_from_config(config)

    assert len(registry.agents) == 1
    agent = registry.agents[0]
    assert set(agent.supported_roles) == {"implementer", "reviewer"}


# --------------------------------------------------------------------------
# load_agent_registry: file present vs. backward-compatible fallback
# --------------------------------------------------------------------------


def test_load_agent_registry_falls_back_to_synthesis_when_file_missing(tmp_path: Path) -> None:
    config = _config(implementer_agent="codex", reviewer_agent="claude")

    registry = load_agent_registry(config, agents_path=tmp_path / "missing-agents.yaml")

    assert registry == synthesize_registry_from_config(config)


def test_load_agent_registry_reads_real_file(tmp_path: Path) -> None:
    agents_path = tmp_path / "agents.yaml"
    agents_path.write_text(
        "agents:\n"
        "  - id: claude-local\n"
        "    backend: claude\n"
        "    priority: 100\n"
        "    supported_roles: [implementer]\n",
        encoding="utf-8",
    )
    config = _config()

    registry = load_agent_registry(config, agents_path=agents_path)

    assert resolve_agent(registry, "implementer").id == "claude-local"


def test_load_agent_registry_rejects_invalid_yaml(tmp_path: Path) -> None:
    agents_path = tmp_path / "agents.yaml"
    agents_path.write_text("agents: [\n", encoding="utf-8")
    config = _config()

    with pytest.raises(AgentRegistryError, match="Invalid YAML"):
        load_agent_registry(config, agents_path=agents_path)


def test_load_agent_registry_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    agents_path = tmp_path / "agents.yaml"
    agents_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    config = _config()

    with pytest.raises(AgentRegistryError, match="must contain a mapping"):
        load_agent_registry(config, agents_path=agents_path)
