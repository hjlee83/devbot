# Agent System

DevBot depends on an `AgentRunner` interface, not on one vendor.

Initial implementation:
- `CodexRunner`

Possible later implementations:
- `ClaudeRunner`
- `DeepSeekRunner`

The target repository's root `AGENTS.md` is the project-specific source of truth.
DevBot does not duplicate those rules into its own repository-specific configuration.
