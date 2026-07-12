# Issue Task Prompt Template

This template is filled in per Issue and handed to the configured
`AgentRunner` (see `src/devbot/agents/`).

The target repository's root `AGENTS.md` is the source of truth for that
project's rules, conventions, and quality gates. This prompt only supplies
the specific Issue to work on; it does not restate or override the target
repository's own `AGENTS.md`.

---

## Repository
`{{owner}}/{{repo}}`

## Issue
`#{{issue_number}}: {{issue_title}}`

## Issue Body
{{issue_body}}

## Unprocessed Comments
{{unprocessed_comments}}

## Instructions
1. Read the target repository's root `AGENTS.md` and follow it exactly.
2. Implement only the scope described in this Issue.
3. Add or update tests required by the Issue's quality checkpoints.
4. Run this repository's verification commands and fix any failures.
5. Report the result using this repository's result/PR evidence format.
