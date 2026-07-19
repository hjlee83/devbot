# ADR-001: DevBot Core Philosophy

- **Status:** Accepted
- **Date:** 2026-07-19
- **Decision owners:** DevBot Architecture

## Context

AI software-engineering environments evolve rapidly. Each agent runtime exposes different native features, such as persistent goals, skills, MCP integrations, planning modes, memory, review commands, and sub-agents. These capabilities vary by product, version, model, configuration, and subscription.

If DevBot attempts to normalize every vendor-specific capability inside its core, the core will need to follow continuous changes across Codex, Claude, Gemini, Grok, and future runtimes. That creates capability explosion, vendor coupling, and high maintenance cost.

By contrast, the development workflow remains comparatively stable:

1. Task
2. Specification
3. Implementation
4. Review
5. Verification
6. Release

DevBot therefore needs a stable boundary between the long-lived workflow and rapidly changing execution environments.

## Decision

DevBot is defined as a **specification-based software development workflow engine and control center**, not as a replacement AI agent or a framework that reproduces every agent-native feature.

The DevBot core owns:

- Task
- Specification
- Workflow state
- Role dispatch
- Contract
- Verification evidence
- Release

Vendor- and runtime-specific behavior is owned by adapters.

Adapters may use native features such as goals, skills, MCP, memory, agent teams, sub-agents, or self-review, but these features are not modeled as mandatory concepts in the core.

The core exchanges stable execution requests and normalized results with adapters.

```text
Input Channels
ChatGPT / Claude / Grok / Slack / PWA
                 |
                 v
+--------------------------------------+
|             DevBot Core              |
| Task / Specification / Workflow      |
| Dispatch / Contract / Verification   |
+--------------------------------------+
                 |
                 v
+--------------------------------------+
|               Adapters               |
| Codex / Claude / GPT / Grok / Gemini |
| GitHub / Jira / Slack / Others       |
+--------------------------------------+
                 |
                 v
Execution Results / Evidence / Artifacts
```

## Principles

1. **The core does not depend on vendor-specific commands or product features.**
2. **Adapters know DevBot contracts and translate them into runtime-specific execution strategies.**
3. **DevBot state is authoritative; agent-local sessions and goals are execution state.**
4. **Different agents may use different methods but must return compatible results and evidence.**
5. **Input channels and execution agents are independent.**
6. **Repositories, trackers, communication systems, and AI runtimes are integrations configured by the user.**
7. **PWA is the DevBot control center, not necessarily the primary AI conversation surface.**

## Consequences

### Positive

- Core behavior remains stable when AI vendors update their products.
- Users can choose different planner, implementer, reviewer, and verifier combinations.
- New AI agents and development tools can be added without redesigning the workflow core.
- Subscription-assisted channels and API-driven autonomous execution can coexist.
- GitHub, Jira, Slack, and future integrations can follow the same boundary principles.

### Negative

- Each adapter requires maintenance for its runtime and vendor changes.
- Some native capabilities cannot be represented uniformly.
- Execution quality may vary even when agents satisfy the same result contract.
- Adapter-specific testing is required.

## Rejected Alternatives

### Normalize all AI capabilities in the core

Examples include modeling Goal, Skills, MCP, Memory, Planning, and Sub-agent as universal core capabilities.

Rejected because these concepts change frequently and differ significantly across products. Maintaining a universal capability model would make the core follow vendor roadmaps.

### Implement separate workflows per AI vendor

Examples include Codex Workflow, Claude Workflow, and Grok Workflow inside the core.

Rejected because this would duplicate workflow logic and make DevBot vendor-dependent.

### Use one fixed AI combination

Example: GPT as planner and Codex as implementer for every installation.

Rejected because this reflects one user's subscription and cost constraints rather than the product's general purpose.

## Implementation Guidance

The first implementation may support only a small number of integrations, currently GitHub and the GPT/Codex workflow. However, boundaries must allow later additions without changing core domain concepts.

Initial routing may use simple `primary` and `fallback` policies. Weighted selection, trust scores, performance history, and automated capability discovery are deferred until actual usage justifies them.

## Related Future Decisions

- Specification-first architecture
- Role dispatch model
- Input channel versus execution agent
- PWA as DevBot control center
- Integration ports for source control, work tracking, and communication
- Subscription-assisted versus autonomous execution
