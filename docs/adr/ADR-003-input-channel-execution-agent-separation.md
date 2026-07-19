# ADR-003: Separate Input Channels from Execution Agents

- **Status:** Accepted
- **Date:** 2026-07-19
- **Decision owners:** DevBot Architecture

## Context

Users may interact with DevBot from ChatGPT, Claude, Grok, Slack, a PWA, or future channels. The channel that receives a request may provide useful reasoning through a subscription product, but it does not necessarily need to perform the actual planner, implementer, reviewer, or verifier role.

Binding the input channel to the execution agent would make workflows depend on one application, prevent flexible routing, and confuse subscription-assisted conversations with autonomous API execution.

## Decision

Input channels and execution agents are separate concepts.

An input channel is responsible for:

- Accepting user intent
- Identifying the user and project
- Normalizing commands or task requests
- Presenting status, approvals, and results

An execution agent is responsible for one or more workflow roles:

- Planner
- Implementer
- Reviewer
- Verifier
- Release manager

A request received through one channel may be executed by any configured agent according to project policy.

```text
ChatGPT / Claude / Grok / Slack / PWA
                  |
                  v
          Channel Gateway
                  |
                  v
             DevBot Task
                  |
                  v
       Role and Routing Policy
          /       |        \
     Planner  Implementer  Reviewer
```

The originating channel is recorded for traceability but does not determine role assignment.

## Execution Modes

### Subscription-assisted mode

A user converses in a subscription application, such as ChatGPT or Claude, and that application calls or instructs DevBot. Reasoning available within the subscription remains inside that application.

### Autonomous mode

A PWA, Slack command, scheduler, or webhook triggers DevBot, which invokes configured APIs or runtimes directly. This mode may incur separate API or infrastructure costs.

Both modes share the same Task, Specification, workflow, and execution result contracts.

## Consequences

### Positive

- Users can keep using their preferred subscription application.
- Planner and implementation roles can be assigned independently.
- Slack and PWA can coexist with AI application channels.
- Channel changes do not alter core workflow state.
- Subscription-assisted and autonomous operation can be supported together.

### Negative

- Each channel needs its own authentication and command adapter.
- Consumer AI applications expose different external-action capabilities.
- Some channels cannot support fully automated execution without API usage.
- Identity and permission mapping become explicit product concerns.

## Rejected Alternatives

### The input AI always performs planning

Rejected because it forces role assignment based on where the user typed rather than project policy, cost, or quality preferences.

### PWA as a replacement AI chat application

Rejected because subscription entitlements such as ChatGPT Plus generally cannot be reused as API credits in an external PWA.

### One universal chat gateway backed only by APIs

Rejected as the only mode because it would discard the cost advantage and capabilities users already receive through AI application subscriptions.

## Implementation Guidance

Initial channel support may prioritize PWA management and one subscription-assisted channel. Channel adapters should emit normalized commands and references rather than embedding workflow logic.
