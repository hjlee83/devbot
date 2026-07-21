# Repository Registration (`devbot init`)

Issue #122's architecture: replaces mandatory `WORKSPACE_ROOT`-based
repository discovery with explicit registration initiated from each target
repository, so the long-running DevBot daemon can manage repositories that
live under different parent directories than DevBot's own checkout.

## Why

The original model assumed every managed repository shared one parent
directory (`WORKSPACE_ROOT`), with `local_path` derived as
`WORKSPACE_ROOT / repo` (`config/repositories.yaml`). Once DevBot runs from
its own dedicated checkout (Implementation Context: "under tmux"), this
assumption no longer holds - a managed repository is not necessarily a
sibling directory of DevBot itself.

## Two files, deliberately kept separate

```text
<repo>/.devbot/config.yaml        ~/.devbot/registry.yaml (default)
  repository-local policy           only absolute paths
  owner, repo, enabled,
  default_branch,
  automerge_allowed,
  is_self_repo, publish_strategy
```

- **Repository-local config** (`<repo>/.devbot/config.yaml`) carries exactly
  the fields a `config/repositories.yaml` entry already carries per
  repository - it is meant to be committed to the target repository, the
  same way a `.eslintrc` is: it is project policy, not a personal secret or
  a machine-specific path.
- **The global registry** (`~/.devbot/registry.yaml` by default, overridable
  via `DEVBOT_REGISTRY_PATH`) stores only absolute repository paths - it is
  inherently per-machine (the same repository can live at a different path
  on different machines) and is never committed to any managed repository.

`devbot.repository_registry` (`src/devbot/repository_registry.py`) owns
both. No other module writes either file.

## `devbot init`

Run from inside a target Git repository (any working directory within it -
`find_git_repository_root()` walks upward to the nearest `.git`, the same
way `git` itself resolves a repository root):

```bash
cd /path/to/some-repo
devbot init [--owner OWNER] [--repo REPO] [--default-branch BRANCH]
            [--automerge-allowed] [--unregister]
```

1. Locate the repository root.
2. Resolve `owner`/`repo`: explicit `--owner`/`--repo`, else parsed from the
   `origin` remote URL (`detect_owner_repo_from_git_remote()`, both SSH and
   HTTPS forms) - if neither is available, `devbot init` fails with an
   actionable message rather than guessing.
3. Write `<repo>/.devbot/config.yaml` - idempotent: an unchanged re-run
   writes nothing, and any already-set field (e.g. `automerge_allowed` from
   a previous `--automerge-allowed` run) is preserved rather than reset to
   its default when re-run without that flag.
4. Register the repository's absolute path in the global registry -
   idempotent: registering the same resolved path twice is a no-op. Registry
   updates hold a registry-path-specific advisory lock across the whole
   load/mutate/atomic-replace sequence, so concurrent `devbot init` or
   `devbot init --unregister` processes cannot overwrite each other's
   updates.

`devbot init` deliberately runs **before** `devbot.config.load_config()` -
see `main()`'s dispatch order (`src/devbot/main.py`). It must work even when
`WORKSPACE_ROOT`/`GITHUB_TOKEN`/`config/repositories.yaml` do not exist yet,
since creating that configuration (or its registry-based replacement) is
exactly its job.

`devbot init --unregister` removes the repository's registry entry only -
`<repo>/.devbot/config.yaml` is left in place, so re-registering later
(`devbot init` again) does not need to rediscover `owner`/`repo`/settings
from scratch.

## Reading side: `load_config()`

`devbot.config.load_config()` now has two independent, additive repository
sources:

```text
legacy_repositories    <- WORKSPACE_ROOT + config/repositories.yaml,
                          loaded when WORKSPACE_ROOT is set and the legacy
                          file is explicitly configured or exists in the
                          runtime directory
registry_repositories   <- devbot.repository_registry
                           .resolve_registered_repositories()

repositories = legacy_repositories + registry_repositories
```

`WORKSPACE_ROOT` is optional (`DevBotConfig.workspace_root: Path | None`); a
deployment may use either source, both at once (migrating repository by
repository), or - if neither yields anything - `load_config()` raises
`ConfigError("No repositories configured: ...")`, the same fail-closed
discipline the rest of this codebase already uses for missing required
configuration.

The default legacy path (`config/repositories.yaml`) is optional when one or
more registry repositories resolve successfully. This is what lets an installed
DevBot daemon run from `~/runtime/devbot` or another empty operational
directory after repositories have been registered with `devbot init`.
Explicit legacy paths remain strict: if `DEVBOT_REPOSITORIES_PATH` or a caller
provided `repositories_path` points at a missing or malformed file,
`load_config()` raises `ConfigError` instead of silently ignoring the
operator's requested source.

Startup self-update has a separate source-checkout resolution policy. It uses
`DEVBOT_OPERATOR_CHECKOUT`, `DEVBOT_PROJECT_ROOT`, or the installed DevBot
module path to find the DevBot Git checkout, so a runtime directory is never
mistaken for the operator checkout.

## Diagnostics: never crash on one bad registration

`resolve_registered_repositories()` never raises for a single broken
registry entry - one moved/deleted path, one `owner/repo` registered twice
under different paths, or one unreadable `.devbot/config.yaml` must not
prevent every other repository (legacy or registered) from being managed.
Each problem becomes a `RegistryDiagnostic` instead:

| `kind` | Cause | Effect |
|---|---|---|
| `missing_path` | Registered path no longer exists (moved/deleted) | That entry excluded, `registry_diagnostics` records it |
| `duplicate_repository` | Same `owner/repo` registered at two paths | Every entry for that ambiguous `owner/repo` excluded |
| `invalid_config` | `.devbot/config.yaml` missing/unreadable/malformed | That entry excluded |

The **same** `owner/repo` declared once in `config/repositories.yaml` and
once more via `devbot init` is a different case - genuinely ambiguous
across sources, not just within the registry - and `load_config()` raises a
`ConfigError` for it (`_require_no_cross_source_duplicates()`), rather than
silently picking one.

`DevBotConfig.registry_diagnostics: tuple[str, ...]` carries every
diagnostic message forward from `load_config()`. Two places surface it:

- The daemon still starts (informational, not fatal) - a broken
  registration limits what gets managed, not whether DevBot runs at all.
- `devbot doctor`'s `repository_registrations` check
  (`devbot.doctor.check_repository_registrations`) reports the same
  diagnostics explicitly, the same "informational, not fatal" pattern
  every other `devbot doctor` check already follows (`check_worktree_health`,
  `check_github_connectivity`, ...).

## Non-goals (Issue #122's own Scope)

- No redesign of Goal/Task/verification-gate architecture.
- No change to implementer/reviewer/rework role selection.
- No repository-map or automatic context-selection feature - registration is
  always explicit (`devbot init`), never inferred by scanning parent
  directories.
- No GitHub Release or package-distribution change.
- `WORKSPACE_ROOT` compatibility is not removed - both sources remain
  supported indefinitely until a future, separate decision changes that.
