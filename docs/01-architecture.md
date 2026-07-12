# Architecture

```text
Custom GPT
  -> GitHub Issue / PR / Actions
  -> Local DevBot (Mac, later Linux VPS)
  -> AgentRunner
  -> Codex CLI initially
  -> code, build, test, repair loop
  -> branch push and PR
  -> user review
  -> merge
  -> GitHub Actions / Vercel
```

## Responsibilities

### Custom GPT
- Create structured Issues.
- Read Issue, PR, and GitHub Actions state.
- Add review comments.
- Review whether PR tests cover the Task quality checkpoints.

### GitHub
- Source of truth for queue state and collaboration history.
- Issues represent work.
- Labels represent state.
- PRs represent reviewable implementation.

### DevBot
- Poll managed repositories.
- Enforce one globally active task.
- Select the next eligible Issue.
- Run the configured agent.
- Execute verification.
- Commit, push, and open PR when allowed.

### Agent
- Implement the Task.
- Add tests required by each checkpoint.
- Repair failures until gates pass.
- Produce structured result evidence.
