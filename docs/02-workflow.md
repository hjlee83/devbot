# Workflow

## Labels
- `devbot:ready`
- `devbot:working`
- `devbot:review`
- `devbot:blocked`
- `devbot:manual-action`
- `devbot:done`
- `priority:high`
- `priority:medium`
- `priority:low`

## Global concurrency rule
Across every managed repository:

```text
new work may start only when:
working count == 0
AND review count == 0
```

The first version supports exactly one globally active task.

## State flow

```text
ready -> working -> review -> done
                  -> working -> review   # review feedback
working -> blocked
blocked -> ready                         # after human clarification
```

## Selection order
1. `priority:high`
2. `priority:medium`
3. `priority:low`
4. no priority label
5. oldest Issue first within the same priority
