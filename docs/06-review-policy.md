# PR Review Policy

The reviewer checks:

1. Implementation matches the Task contract.
2. Every checkpoint maps to actual test code.
3. Named tests assert meaningful behavior rather than superficial execution.
4. Boundary conditions and failure paths are covered.
5. Verification commands passed.
6. No unrelated refactoring or scope expansion occurred.
7. Secrets and environment-specific values were not committed.

The PR must contain a checkpoint-to-test evidence table.
