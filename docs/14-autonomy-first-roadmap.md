# Autonomy-first Roadmap

## B2: Automatic Merge Safety Gate

Status: done.

Automatic merge is now policy-gated rather than manual-only. DevBot may merge a
`devbot:ready-to-merge` PR only when all gates pass:

1. `AUTOMERGE_ENABLED=true`.
2. The repository has `automerge_allowed: true`.
3. The repository is not marked `is_self_repo: true`.
4. GitHub check-runs for the PR head are complete and green.

DevBot self-modification PRs always remain on the human merge rail. Gate
failures keep the PR's `devbot:ready-to-merge` label, log/comment the reason,
and preserve the existing manual merge path.
