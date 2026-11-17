# Scripts

## One-click deploy (Windows)

```bat
:: auto-discovers every installed runtime; asks for -Target only when more than one exists
scripts\deploy.cmd -ListTargets

scripts\deploy.cmd -Target workbuddy-ai
scripts\deploy.cmd -Target workbuddy-ai -Elevate     :: writes the install mirror too (UAC)
scripts\deploy.cmd -Target workbuddy-ai -DryRun
scripts\deploy.cmd -NoInstall
scripts\deploy.cmd -Template welcome-mode\work.tpl
scripts\deploy.cmd -Skill team-orchestration
```

`deploy.ps1` discovers installations, delegates to `apply.ps1`, asserts 7 built-in skills, then runs `verify.ps1`.
`deploy.cmd` only pauses when it is started with no arguments (double-click); a scripted call such as
`deploy.cmd -Target workbuddy` returns instead of waiting for a keypress.

### Installations are discovered, not configured

`scripts\targets.json` ships with empty paths. Discovery walks:

| side | sources |
|---|---|
| install | uninstall registry (`DisplayName like *WorkBuddy*`), `App Paths`, running `WorkBuddy*` processes, `<drive>:\Program Files*\WorkBuddy*` on every fixed drive |
| data | `%USERPROFILE%\.workbuddy*` and `WORKBUDDY_CONFIG_DIR` |
| pairing | `<data>\.skill-list-cache.json` → `scopeKey` entry `...\<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills` |

Target ids are derived from the install folder (`WorkBuddy` → `workbuddy`, `WorkBuddyAI` → `workbuddy-ai`). Fill `installPath` / `configDir` in `targets.json` only to pin a path.

### Prompts vs skills — two different read points

| asset | read from | written by |
|---|---|---|
| prompt templates, fragments, interaction-mode text | `<data>\plugins\cache\workbuddy-builtin\<plugin>\<version>\` | any deploy, **no admin needed** |
| builtin skills (SKILL.md) | `<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills\` (and `<data>\skills\` for user skills) — **never the plugin cache** | `-Elevate` (or an elevated terminal) |

So a non-admin deploy updates the prompt but leaves skills invisible. `deploy.ps1` detects that and prints
`!! SKILLS NOT ACTIVE: n/7 ...` with the exact `-Elevate` command. App updates overwrite the install folder,
so re-run `deploy.cmd -Target <id> -Elevate` after every upgrade.

- Existing files are backed up to `backups/<timestamp>/` (manifest records `target`); every copy is verified with SHA256.
- `-Detailed` prints the full mapping; `-SkipVerify` skips the consistency check; `-Template` / `-Skill` target one file or one skill.

## Verify

```bat
scripts\verify.cmd -Target workbuddy-ai -CheckIncludes
```

- Prints project / install / cache SHA256 for the three welcome templates and every builtin skill.
- `-CheckIncludes` resolves each `{% include %}` in the newest version directory of every cached plugin.
- Compares the newest trace against the last apply time: a request older than the deploy is reported as
  `STALE` and downgrades "forbidden marker still present" to exit code 2 (inconclusive), not a failure.

## Rollback

```powershell
scripts\restore.ps1 -Target workbuddy-ai -List
scripts\restore.ps1 -Target workbuddy-ai -All
scripts\restore.ps1 -BackupPath backups\<timestamp>
```

Backup sets are filtered by the `target` recorded in their manifest; older sets without the field show as `legacy`.

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

- `deploy.ps1` / `deploy.cmd` — one-click entry: discovery, elevation, skill assertion, verify.
- `target.ps1` / `targets.json` — installation discovery + optional path pins.
- `apply.ps1` — copy engine: source tree → install mirror + plugin cache, backups, skill registration.
- `pull.ps1` — snapshot the current prompts into `archive/pulls/<target>-<timestamp>/`.
- `verify.ps1` — consistency check across project / install / cache (+ include resolution).
- `restore.ps1` — restore a previous backup, per target.
- `render.js` — Nunjucks render of all 15 template/mode combinations (release layout).
- `check.js` — structural assertions on the renders.
- `render-check.js` — same render from the project layout, with edition profiles; `--root` renders any layout.
- `cache-prefix.js` — prefix-cache retention per change scenario (LCP), optional `--strict` gate.
- `check-memory-blocks.js` — memory-block shape for the four `CloudMemoryPlatformEnabled` × `WorkspaceMemoryDisabled` combinations.
