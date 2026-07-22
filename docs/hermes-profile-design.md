# Hermes Profile Architecture

## Goal
Profile-based AI orchestration independent of model vendors.

## Profiles

### orchestrator
- Poll GitHub
- Read labels
- Select next profile
- Never implement code

### developer-primary
- Default implementation
- Follow developer.md
- Create/update PR

### developer-secondary
- Activated after repeated review failures
- Focus on reviewer feedback
- Same contract as developer-primary
- Different profile/prompt is allowed

### reviewer
- Read reviewer.md
- Review architecture and code
- Apply workflow labels
- APPROVE / REQUEST_CHANGES / BLOCKED

### merger
- Read merger.md
- Verify merge conditions
- Merge only when all conditions are satisfied

## Label -> Profile

agent:ready -> developer-primary
agent:changes-1 -> developer-primary
agent:changes-2 -> developer-secondary
agent:review -> reviewer
agent:merge-ready -> merger

## Design Principles
- Labels represent state only.
- Profiles represent actors only.
- Contracts define responsibilities.
- AI vendors are configurable through profiles without changing contracts or workflow.
