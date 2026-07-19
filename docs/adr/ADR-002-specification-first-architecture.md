# ADR-002: Specification-First Architecture

- **Status:** Accepted
- **Date:** 2026-07-19
- **Decision owners:** DevBot Architecture

## Context

AI agents differ in planning style, tool access, memory, native commands, model quality, and runtime behavior. Prompts alone are not a stable handoff format between planners, implementers, reviewers, and verifiers.

DevBot needs one durable artifact that survives session loss, agent replacement, retries, and changes in implementation strategy.

## Decision

The Specification is the authoritative contract for development work.

A Task expresses intent. The Specification converts that intent into an executable and verifiable contract. Implementations, reviews, verification, and release classification must reference the Specification rather than relying on conversational context.

A Specification should define at least:

- Objective and scope
- Inputs and constraints
- Acceptance criteria
- Expected artifacts
- Verification requirements
- Explicit exclusions where necessary

The workflow is therefore:

```text
Task
  -> Specification
  -> Role Assignment
  -> Implementation
  -> Review
  -> Verification against Specification
  -> Completion or Rework
  -> Release Classification
```

Conversational prompts and agent-local goals are compiled execution inputs. They are not the source of truth.

## Consequences

### Positive

- Work can be handed between different agents and sessions.
- Completion is judged against explicit criteria rather than agent claims.
- Reviews and releases can use the same contract as implementation.
- Vendor-specific prompt formats can change without changing the task definition.
- Missing requirements become visible before or during execution.

### Negative

- Specification creation adds initial overhead.
- Poor specifications can constrain implementation incorrectly.
- Small tasks may not require the same level of detail as large changes.
- Specification evolution and versioning must be managed.

## Rejected Alternatives

### Conversation as the source of truth

Rejected because conversation history is channel-specific, difficult to transfer, and vulnerable to truncation or session loss.

### Issue body as the only specification

Rejected because issue formats vary by tracker and often mix discussion, status, and implementation notes. An external issue may reference or contain a Specification, but DevBot must preserve the contract independently.

### Agent-generated plan as the authoritative contract

Rejected because plans are runtime-specific execution aids and may change during implementation.

## Implementation Guidance

Specification depth should be proportional to task risk and size. The core must support concise specifications for small changes and richer contracts for architectural or high-risk work.

Execution results must map evidence back to acceptance criteria wherever possible.
