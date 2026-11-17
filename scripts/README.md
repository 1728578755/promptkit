# Scripts

## One entry: deploy, restore, verify, pull, asar

`scripts\deploy.cmd` is the only entry point. Everything else is an engine it calls.

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

::- restore
scripts\deploy.cmd -Restore
scripts\deploy.cmd -Restore -List
scripts\deploy.cmd -Restore -BackupPath backups\<timestamp>
scripts\deploy.cmd -Restore -Template welcome-mode\work.tpl
scripts\deploy.cmd -Restore -Original
scripts\deploy.cmd -Restore -DryRun

::- verify / snapshot / asar
scripts\deploy.cmd -Verify
scripts\deploy.cmd -Pull
scripts\deploy.cmd -Asar                :: app must be closed
scripts\deploy.cmd -AsarRestore
```

`deploy.ps1` resolves the target list once, then runs the chosen action for each installation:
`apply.ps1` → product overrides (when present) → `app.asar` patch (when present) → `verify.ps1`,
with one summary table and a non-zero exit code if any target failed.

`deploy.cmd` only pauses when it is started with no arguments (double-click); a scripted call such as
`deploy.cmd -Target workbuddy` returns instead of waiting for a keypress.

### Installations are discovered, not configured

`scripts\targets.json` ships with empty paths and no default target. **The install folder is not assumed to be
under `Program Files`** — resolution tries, in order:

| order | source |
|---|---|
| 1 explicit | `-WorkBuddyPath` / `-ConfigDir`, or `installPath` / `configDir` in `targets.json` |
| 2 pairing (best) | `<data>\.skill-list-cache.json` → `scopeKey` entry `...\<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills`; falls back to `settings.json` |
| 3 registry | `App Paths` (whole subtree, any `WorkBuddy*.exe`), uninstall entries (DisplayName / Publisher / InstallLocation) |
| 4 runtime traces | running processes, `PATH`, Start Menu shortcuts (machine + user) |
| 5 common places | `%LOCALAPPDATA%\Programs`, `%APPDATA%`, `scoop\apps`, `%ProgramData%`, `<drive>:\Program Files*\WorkBuddy*`, the drive root and one level below it |
| 6 drive scan | only when 1–5 fail and `-Target` was named explicitly: `<drive>:\WorkBuddy*`, `<drive>:\*\WorkBuddy*`, `<drive>:\Program Files*\WorkBuddy*` |

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

`render-check.js` also asserts: no leftover `{{`/`{%`, no `<regional_conventions>` under the AI profile,
and no `<content_policy>` in any mode.

`cache-prefix.js` renders two versions of the same prompt that differ in exactly one variable (project
folder, account memory path, model name, working memory, user memory, industry mode) and reports the
longest common prefix as a percentage — that is literally the share of the request the prefix cache can
reuse. Cross-mode switches are reported but not gated by default (ask keeps far fewer blocks than craft);
turn the gate on with `--mode-threshold N`.

## Files

- `deploy.cmd` / `deploy.ps1` — the only entry: target discovery, one-shot elevation, then
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
