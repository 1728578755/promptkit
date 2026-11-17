# Scripts

## Two double-click entries, one implementation

`scripts\deploy.cmd` deploys; `scripts\restore.cmd` is a thin wrapper that restores (it simply calls
`deploy.ps1 -Restore`). Everything else is an engine those two call.

```bat
:: deploy to EVERY discovered installation (CN + AI in one run)
scripts\deploy.cmd
scripts\deploy.cmd -Elevate            :: same, one UAC prompt, also writes every install mirror
scripts\deploy.cmd -Target workbuddy-ai :: one installation only (default: all)
scripts\deploy.cmd -ListTargets
scripts\deploy.cmd -DryRun
scripts\deploy.cmd -Template welcome-mode\work.tpl
scripts\deploy.cmd -Skill team-orchestration
scripts\deploy.cmd -SkipAsarPatch      :: skip the app.asar patch that a deploy otherwise applies

::- restore (one click: put every installation back to its latest backup)
scripts\restore.cmd
scripts\restore.cmd -List
scripts\restore.cmd -BackupPath backups\<timestamp>
scripts\restore.cmd -Template welcome-mode\work.tpl
scripts\restore.cmd -Original
scripts\restore.cmd -Target workbuddy-ai
scripts\restore.cmd -Elevate           :: restore the install mirror too (one UAC)
scripts\restore.cmd -DryRun

::- verify / snapshot / asar
scripts\deploy.cmd -Verify
scripts\deploy.cmd -Pull
scripts\deploy.cmd -Asar                :: app must be closed
scripts\deploy.cmd -AsarRestore
```

`deploy.ps1` resolves the target list once, then runs the chosen action for each installation:
`apply.ps1` → product overrides (when present) → `app.asar` patch (when present) → `verify.ps1`,
with one summary table and a non-zero exit code if any target failed.

Both `.cmd` files only pause when started with no arguments (double-click); a scripted call such as
`deploy.cmd -Target workbuddy` returns instead of waiting for a keypress.

### Installations are discovered, not configured

`scripts\targets.json` ships with empty paths and no default target. **Only installations that the system knows
about are used — the disk is never walked and folders are never guessed.** Resolution order:

| order | source |
|---|---|
| 1 explicit | `-WorkBuddyPath` / `-ConfigDir`, or `installPath` / `configDir` in `targets.json` |
| 2 installed programs | uninstall entries: `DisplayIcon` → folder of the exe, `UninstallString` → folder of the uninstaller, `InstallLocation`; 64- and 32-bit views, HKLM and HKCU |
| 3 App Paths | executable paths registered by the installer, whole subtree, any `WorkBuddy*.exe` |
| 4 data-dir pairing | `<data>\.skill-list-cache.json` → `scopeKey` entry `...\<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills`; falls back to `settings.json` |
| 5 running process | the executable path of a process named `WorkBuddy*` |

When none of the five knows the folder the command fails with the list of registered installations instead of
scanning; an unregistered portable copy is pinned once with `-WorkBuddyPath "<dir>"` or in `targets.json`.

The executable is matched as `WorkBuddy*.exe` (so `WorkBuddy.exe`, `WorkBuddyAI.exe`, `WorkBuddy Beta.exe` all work);
the folder name can be anything as long as it holds `resources\app.asar` or a matching exe.
Target ids are derived from the install folder (`WorkBuddy` → `workbuddy`, `WorkBuddyAI` → `workbuddy-ai`).
The data directory is taken from the pairing above; only when no pairing exists does it fall back to
`%USERPROFILE%\.<id>`. The resolved paths are handed to child scripts through
`PROMPTKIT_TARGET_<ID>_INSTALL` / `_CONFIG`, so `apply` / `verify` / `restore` / `pull` never scan again.
Nothing in the scripts contains a hard-coded product directory: the child shell comes
from `$PSHOME`, and python is looked up in `<data>\binaries\python\versions` before any user-level install.
Fill `installPath` / `configDir` in `targets.json` only to pin a path.

### Prompts vs skills — two different read points

| asset | read from | written by |
|---|---|---|
| prompt templates, fragments, interaction-mode text | `<data>\plugins\cache\workbuddy-builtin\<plugin>\<version>\` | any deploy, **no admin needed** |
| builtin skills (SKILL.md) | `<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills\` (and `<data>\skills\` for user skills) — **never the plugin cache** | `-Elevate` (or an elevated terminal) |

So a non-admin deploy updates the prompt but leaves skills invisible. `deploy.ps1` detects that and prints
`!! SKILLS NOT ACTIVE: n/7 ...` with the exact `-Elevate` command. App updates overwrite the install folder,
so re-run `deploy.cmd -Elevate` after every upgrade.

- Existing files are backed up to `backups/<timestamp>/` (manifest records `target`); every copy is verified with SHA256.
- `-Detailed` prints the full mapping; `-SkipVerify` skips the consistency check; `-Template` / `-Skill` target one file or one skill.

## Verify

```bat
scripts\deploy.cmd -Verify
```

- Prints project / install / cache SHA256 for the three welcome templates and every builtin skill, per target.
- Resolves each `{% include %}` in the newest version directory of every cached plugin.
- Compares the newest trace against the last apply time: a request older than the deploy is reported as
  `STALE` and downgrades "forbidden marker still present" to exit code 2 (inconclusive), not a failure.

## Rollback

```bat
scripts\deploy.cmd -Restore -List
scripts\deploy.cmd -Restore
scripts\deploy.cmd -Restore -BackupPath backups\<timestamp>
scripts\deploy.cmd -Restore -Original
```

Backup sets are filtered by the `target` recorded in their manifest; older sets without the field show as `legacy`.
`-Restore` runs for every discovered installation unless `-Target` narrows it.

## Render / check / cache

```bash
npm install
npm run render                                  # 3 templates × 5 modes into dist/
npm run check                                   # structural assertions on dist/
npm run render-check -- --profile cn            # render all 15 combinations, CN context
npm run render-check -- --profile ai            # international-edition context
npm run render-check -- --root <dir>            # render from another root (e.g. a plugin-cache snapshot)
npm run cache                                   # longest common prefix per change scenario
npm run cache -- --strict                       # fail when a same-mode scenario drops below 95%
npm run check-memory                            # <memory_system>/<memory_paths> across the four switches
```

`render-check.js` also asserts: no leftover `{{`/`{%`, no `<content_policy>` in any mode, and none of
the three blocks removed on 2026-11-17 (`<quality_policy>` / `<agent_loop>` / `<regional_conventions>`)
in any template/mode under either profile — a hit exits with code 1.

`cache-prefix.js` renders two versions of the same prompt that differ in exactly one variable (project
folder, account memory path, model name, working memory, user memory, industry mode) and reports the
longest common prefix as a percentage — that is literally the share of the request the prefix cache can
reuse. Cross-mode switches are reported but not gated by default (ask keeps far fewer blocks than craft);
turn the gate on with `--mode-threshold N`.

## Files

- `deploy.cmd` / `restore.cmd` — the two double-click entries; `restore.cmd` is a wrapper over `deploy.ps1 -Restore`.
- `deploy.ps1` — dispatcher: target discovery, one-shot elevation, then
  deploy / `-Restore` / `-Verify` / `-Pull` / `-Asar` / `-AsarRestore` for every target.
- `target.ps1` / `targets.json` — installation discovery, data-dir pairing, multi-target resolution, optional path pins.
- `apply.ps1` — copy engine: source tree → install mirror + plugin cache, backups, skill registration.
- `pull.ps1` — snapshot the current prompts into `archive/pulls/<target>-<timestamp>/`.
- `verify.ps1` — consistency check across project / install / cache (+ include resolution).
- `restore.ps1` — restore a previous backup or the original baseline, per target.
- `render.js` — Nunjucks render of all 15 template/mode combinations (release layout).
- `check.js` — structural assertions on the renders.
- `render-check.js` — same render from the project layout, with edition profiles; `--root` renders any layout.
- `cache-prefix.js` — prefix-cache retention per change scenario (LCP), optional `--strict` gate.
- `check-memory-blocks.js` — memory-block shape for the four `CloudMemoryPlatformEnabled` × `WorkspaceMemoryDisabled` combinations.
