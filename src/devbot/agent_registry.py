"""Task 041: Role Dispatch and Agent Registry.

DevBot dispatches only **Roles** ("implementer", "reviewer", "planner",
"release", ...) - it never hard-codes a concrete Agent name (Claude/Codex/
GPT/Gemini/...) at a call site. A `Router` resolves the configured **Agent**
for a Role from an **Agent Registry**, using priority routing - the only
routing strategy implemented so far (`RoleRoutingConfig.routing`). This is
the foundation for a future Admin UI, capability-aware Auto routing, and
multi-agent dispatch; none of those are implemented here, only the
abstraction that will let them be added without changing this module's
public API.

Four separated concepts (Role/Capability/Agent/Router in the Task 041
request) map onto this module as:

- **Role**: a plain string identifying *what kind of work* is being
  dispatched ("implementer", "reviewer", ...). Not a distinct type here -
  just a key into `AgentRegistry.roles` / `AgentDescriptor.supported_roles`.
- **Capability**: a plain string tag on an `AgentDescriptor`
  (`AgentDescriptor.capabilities`, e.g. "python", "github", "testing").
  Recorded but **not used by routing yet** - priority routing (this Task)
  ignores it entirely; a future Auto routing strategy would read it.
- **Agent**: `AgentDescriptor` - a registry entry, *not* an `AgentRunner`.
  `AgentDescriptor.id` is the operator-facing registry identity (e.g.
  "claude-local"); `AgentDescriptor.backend` is which concrete execution
  backend `devbot.agents.build_agent_runner` understands ("claude"/"codex"
  today). Decoupling `id` from `backend` is what lets a future registry list
  multiple differently-configured instances of the same backend, or
  backends this codebase doesn't implement yet, without touching this
  module's shape again.
- **Router**: `resolve_agent()` - Role -> Agent Registry -> selected
  `AgentDescriptor`. Callers pass `.backend` to the existing, unchanged
  `build_agent_runner()` - the execution backend itself never changes.

**Backward compatibility is load-bearing.** `load_agent_registry()` reads an
optional `config/agents.yaml`. Every deployment before this Task has no such
file, so it falls back to `synthesize_registry_from_config()`: an Agent
Registry built from `DevBotConfig`'s existing `implementer_agent`/
`reviewer_agent` fields (`devbot.config.load_config`'s own
`IMPLEMENTER_AGENT`/`REVIEWER_AGENT`/`DEFAULT_AGENT` resolution, completely
unchanged). Priority routing over that synthesized registry always resolves
to exactly the same Agent `devbot.config` already picked, so existing
deployments dispatch identically to before this Task - only the *path* by
which `main.py` reaches that Agent name changed (through the Router, not a
direct config-field read).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from devbot.agents import KNOWN_AGENT_NAMES
from devbot.models import DevBotConfig

DEFAULT_AGENTS_REGISTRY_PATH = Path("config/agents.yaml")
AGENTS_REGISTRY_PATH_ENV = "DEVBOT_AGENTS_PATH"

# Priority is the only implemented routing strategy (Task 041 scope). A
# future strategy (e.g. "auto", reading AgentDescriptor.capabilities) would
# add its own entry here and its own branch in `resolve_agent` - the Role/
# Agent/Router shapes above do not need to change to add one.
_SUPPORTED_ROUTING_STRATEGIES = frozenset({"priority"})


class AgentRegistryError(RuntimeError):
    """Raised when `config/agents.yaml` is missing required structure or
    references an unknown execution backend."""


class RoutingError(RuntimeError):
    """Base class for a Role that cannot be resolved to an Agent."""


class RoleNotConfiguredError(RoutingError):
    """The requested Role has no entry in the registry's `roles` mapping and
    no Agent lists it in `supported_roles` either - DevBot has no opinion at
    all about this Role name."""


class NoAgentAvailableError(RoutingError):
    """The Role is configured, but no *enabled* Agent in the registry
    supports it (either none list it in `supported_roles`, or the ones that
    do are all `enabled: false`)."""


@dataclass(frozen=True)
class RoleRoutingConfig:
    name: str
    routing: str = "priority"


@dataclass(frozen=True)
class AgentDescriptor:
    """One Agent Registry entry - not an `AgentRunner`. `backend` is what
    `devbot.agents.build_agent_runner` actually instantiates."""

    id: str
    backend: str
    enabled: bool = True
    priority: int = 0
    supported_roles: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentRegistry:
    roles: dict[str, RoleRoutingConfig]
    agents: tuple[AgentDescriptor, ...]


def resolve_agent(registry: AgentRegistry, role: str) -> AgentDescriptor:
    """The Router: Role -> Agent Registry -> selected `AgentDescriptor`.

    Priority routing only (Task 041 scope): among Agents that are `enabled`
    and list `role` in `supported_roles`, the highest `priority` wins; ties
    are broken deterministically by `id` ascending (not by registry
    declaration order, so the result does not depend on how the caller
    happened to build the `agents` tuple).
    """
    role_config = registry.roles.get(role)
    if role_config is None:
        raise RoleNotConfiguredError(f"role not configured: {role!r}")
    if role_config.routing not in _SUPPORTED_ROUTING_STRATEGIES:
        raise RoutingError(
            f"role {role!r} uses unsupported routing strategy {role_config.routing!r} "
            f"(only {sorted(_SUPPORTED_ROUTING_STRATEGIES)} is implemented)"
        )

    candidates = [
        agent for agent in registry.agents if agent.enabled and role in agent.supported_roles
    ]
    if not candidates:
        raise NoAgentAvailableError(f"no enabled agent supports role {role!r}")

    return sorted(candidates, key=lambda agent: (-agent.priority, agent.id))[0]


def list_roles(registry: AgentRegistry) -> tuple[str, ...]:
    return tuple(sorted(registry.roles))


def _parse_agent_entry(entry: dict[str, Any], index: int) -> AgentDescriptor:
    agent_id = entry.get("id")
    if not agent_id or not isinstance(agent_id, str):
        raise AgentRegistryError(f"agents[{index}] requires a non-empty string 'id'")

    backend = str(entry.get("backend") or agent_id)
    if backend not in KNOWN_AGENT_NAMES:
        raise AgentRegistryError(
            f"agents[{index}] ({agent_id!r}) has unknown backend {backend!r} "
            f"(known backends: {sorted(KNOWN_AGENT_NAMES)})"
        )

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise AgentRegistryError(f"agents[{index}] ({agent_id!r}).enabled must be a boolean")

    priority = entry.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise AgentRegistryError(f"agents[{index}] ({agent_id!r}).priority must be an integer")

    supported_roles = entry.get("supported_roles") or []
    if not isinstance(supported_roles, list) or not all(
        isinstance(role, str) for role in supported_roles
    ):
        raise AgentRegistryError(
            f"agents[{index}] ({agent_id!r}).supported_roles must be a list of strings"
        )

    capabilities = entry.get("capabilities") or []
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) for capability in capabilities
    ):
        raise AgentRegistryError(
            f"agents[{index}] ({agent_id!r}).capabilities must be a list of strings"
        )

    return AgentDescriptor(
        id=agent_id,
        backend=backend,
        enabled=enabled,
        priority=priority,
        supported_roles=tuple(supported_roles),
        capabilities=tuple(capabilities),
    )


def parse_agent_registry(raw: dict[str, Any]) -> AgentRegistry:
    """Parse an already-loaded `config/agents.yaml` mapping. Pure - no file
    I/O - so fully unit-testable without a filesystem fixture."""
    raw_roles = raw.get("roles") or {}
    if not isinstance(raw_roles, dict):
        raise AgentRegistryError("'roles' must be a mapping")

    roles: dict[str, RoleRoutingConfig] = {}
    for name, role_entry in raw_roles.items():
        role_entry = role_entry or {}
        if not isinstance(role_entry, dict):
            raise AgentRegistryError(f"roles.{name} must be a mapping")
        routing = str(role_entry.get("routing", "priority"))
        roles[str(name)] = RoleRoutingConfig(name=str(name), routing=routing)

    raw_agents = raw.get("agents") or []
    if not isinstance(raw_agents, list):
        raise AgentRegistryError("'agents' must be a list")
    agents = tuple(_parse_agent_entry(entry, index) for index, entry in enumerate(raw_agents))

    # A Role an Agent claims to support but that has no explicit `roles.
    # <name>` entry defaults to priority routing, so a minimal agents.yaml
    # (agents only, no `roles` section at all) still works.
    for agent in agents:
        for role_name in agent.supported_roles:
            roles.setdefault(role_name, RoleRoutingConfig(name=role_name))

    return AgentRegistry(roles=roles, agents=agents)


def synthesize_registry_from_config(config: DevBotConfig) -> AgentRegistry:
    """The backward-compatible default when `config/agents.yaml` does not
    exist: an Agent Registry built from `DevBotConfig`'s existing
    `implementer_agent`/`reviewer_agent` (`devbot.config.load_config`'s own
    resolution, unchanged). Each is the sole candidate for its Role, so
    `resolve_agent()` over this registry always returns exactly the Agent
    `devbot.config` already picked - dispatch stays identical to every
    deployment before this Task."""
    role_backends: dict[str, str] = {
        "implementer": config.implementer_agent,
        "reviewer": config.reviewer_agent,
    }
    backend_roles: dict[str, list[str]] = {}
    for role, backend in role_backends.items():
        backend_roles.setdefault(backend, []).append(role)

    agents = tuple(
        AgentDescriptor(
            id=backend,
            backend=backend,
            enabled=True,
            priority=100,
            supported_roles=tuple(roles_for_backend),
        )
        for backend, roles_for_backend in backend_roles.items()
    )
    roles = {role: RoleRoutingConfig(name=role) for role in role_backends}
    return AgentRegistry(roles=roles, agents=agents)


def load_agent_registry(
    config: DevBotConfig, *, agents_path: Path | str | None = None
) -> AgentRegistry:
    """Load `config/agents.yaml` (or the `DEVBOT_AGENTS_PATH` env var /
    `agents_path` override) if it exists; otherwise synthesize from `config`
    (see `synthesize_registry_from_config`)."""
    resolved_path = (
        Path(agents_path)
        if agents_path is not None
        else Path(os.environ.get(AGENTS_REGISTRY_PATH_ENV) or DEFAULT_AGENTS_REGISTRY_PATH)
    )
    if not resolved_path.is_file():
        return synthesize_registry_from_config(config)

    with resolved_path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise AgentRegistryError(f"Invalid YAML in {resolved_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise AgentRegistryError(f"{resolved_path} must contain a mapping at the top level")

    return parse_agent_registry(raw)
