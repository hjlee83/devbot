# ADR-004: PWA as the DevBot Control Center

- **Status:** Accepted
- **Date:** 2026-07-19
- **Decision owners:** DevBot Architecture

## Context

DevBot needs a user-friendly way to configure AI agents, development tools, routing policies, credentials, projects, and workflow state. A PWA provides a portable interface across desktop and mobile without requiring DevBot to replace every AI provider's native application.

Users may already receive strong reasoning capabilities through subscriptions such as ChatGPT Plus or Claude plans. Those subscription entitlements are not generally transferable to an external PWA as API usage. Building the PWA primarily as another AI chat application would therefore increase cost and duplicate existing products.

## Decision

The PWA is defined as the **DevBot control center**.

Its primary responsibilities are:

- Project creation and selection
- AI runtime and agent profile registration
- Source-control integration configuration
- Work-tracker integration configuration
- Communication and input-channel configuration
- Planner, implementer, reviewer, verifier, and release-role policies
- Primary and fallback assignment
- Workflow status and approval handling
- Execution logs, evidence, artifacts, cost, and failure visibility

The PWA may provide task input and limited chat-assisted experiences, but it is not required to be the primary reasoning surface.

```text
PWA Control Center
├── Projects
├── Agents and Runtimes
├── Role Policies
├── Integrations
│   ├── Source Control
│   ├── Work Tracking
│   └── Communication
├── Workflow and Approvals
└── Results, Evidence, Cost, and Logs
```

## Integration Configuration

Source control and work tracking are configured separately.

Example:

```yaml
integrations:
  source_control:
    provider: github
  work_tracker:
    provider: github_issues
  communication:
    provider: none
```

A future installation may use GitHub for code, Jira for tasks, and Slack for communication without changing the DevBot workflow core.

## Agent Policy Configuration

Initial role routing should use explicit and understandable policies:

```yaml
roles:
  planner:
    primary: gpt
    fallback: claude
  implementer:
    primary: codex
    fallback: claude_code
  reviewer:
    primary: codex
    fallback: gpt
```

Weighted selection, trust scores, measured performance, temporary credit boosts, and ensemble execution may be introduced later, but they are not required for the first control-center experience.

## Consequences

### Positive

- Agent and integration setup becomes accessible without editing configuration files.
- Mobile and desktop administration can share one interface.
- Users can retain native AI applications and subscriptions.
- The product exposes workflow state and evidence instead of hiding orchestration in prompts.
- GitHub-only users and future Jira or Slack users can share the same core product.

### Negative

- The PWA requires secure credential and permission management.
- UX must represent runtime, model, role, and integration concepts without overwhelming users.
- A control center does not by itself provide subscription AI reasoning inside the PWA.
- Direct autonomous reasoning from the PWA requires separately configured APIs or runtimes.

## Rejected Alternatives

### PWA as the primary AI chat replacement

Rejected because it duplicates mature AI applications and cannot reuse their consumer subscription allowances as general API credits.

### Configuration only through files and environment variables

Rejected as the primary user experience because DevBot aims to make multi-agent workflow setup usable by people who should not need to understand every underlying runtime detail.

### Hard-coded GitHub and GPT/Codex setup

Rejected because it would encode one user's current environment instead of supporting different primary tools and subscriptions.

## Implementation Guidance

The first PWA release should favor a guided setup flow:

1. Create or select a project.
2. Connect a source-control provider.
3. Select or connect a work tracker.
4. Register available AI runtimes.
5. Assign primary and fallback profiles to roles.
6. Validate connections and permissions.
7. Show the resulting workflow before activation.

Advanced routing and performance analytics should be added only after the basic configuration and workflow experience is stable.
