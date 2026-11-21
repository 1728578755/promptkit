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

## Runtime layer: asar, product.json, runtime-templates

Three things reach the request without going through a Nunjucks template. A deploy handles all three.

**`app.asar` patches** (`patch-asar.py`, 7 equal-length replacements): `current-time`, `user-info`,
`connector-status`, `project-layout`, `project-context-once`, `scene-chip`, `user-context-once`.
Electron validates the archive twice — a per-file `integrity.hash`/`blocks` in the asar header, and a
`{"file":"resources\\app.asar","alg":"SHA256","value":"…"}` manifest embedded in `WorkBuddy*.exe`.
The script rewrites content, entry hash and exe manifest as one atomic operation and rolls all three back
if any step fails.

```bat
python scripts\patch-asar.py --status  --asar "<install>\resources\app.asar"
python scripts\patch-asar.py --dry-run --asar "<install>\resources\app.asar"
scripts\deploy.cmd -Asar           :: app must be closed; elevates, patches, relaunches
scripts\deploy.cmd -AsarRestore
scripts\deploy.cmd -SkipAsarPatch
```

Exit codes: `0` patched / already patched, `2` anchors not found for this build (nothing written),
`3` needs elevation, `4` app is running. When `--asar` is omitted the path comes from
`WORKBUDDY_INSTALL`, a pinned `installPath` in `targets.json`, or the uninstall registry
(`DisplayIcon` / `InstallLocation` / `UninstallString`, and the folder must really hold
`WorkBuddy*.exe`); nothing is guessed and the disk is never walked.

**`product.json` overrides** (`apply-product-overrides.py`): `overrides/tools/session-tools.compact.json`
replaces 18 high-frequency tool descriptions by exact `prompts[].name`, 33,422 → 16,727 bytes (−50.0%).
Applied on every deploy (`-SkipProductOverrides` to opt out), backed up to
`backups/<timestamp>-product-overrides/`; `--restore` puts it back.

**Skill descriptions** (`apply-skill-desc.py`): `overrides/skills/desc-compact.json` rewrites the
frontmatter `description` of 31 builtin skills down to 3,943 characters (the runtime budget is 8,000 and
builtin skills are protected). It edits the **source** `plugins/**/SKILL.md`, so re-deploy afterwards.

**`runtime-templates/`** (`render-runtime-templates.js`, `npm run runtime`): the four tpl the host reads
directly from `<install>\resources\templates\`. They strip `<product_identity>`, the custom-instruction
lead-in and the per-turn mode reminders; the script asserts each one renders as expected.

Diagnostics that never write: `dump-system-prompt.py`, `dump-messages.py`, `trace-tokens.py`,
`trace-turns.py`, `measure-prompt-blocks.py`, `measure-injected.py`, `measure-injection.py`,
`list-desc.py`, `asar-grep.py`, `cache-compare.js`, `src-context.py`, `verify-tool-overrides.py`,
`measure-product.py`, `tool-desc-map.py`, `export-tool-desc.py`, `test-user-context-once.js`,
`test-asar-launch.py`.
`uc-seen.py` only touches `<config>\uc-seen\` (list / seed / clear / prune).

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

- `deploy.cmd` / `restore.cmd` — the two double-click entries. **No arguments = full write**: `deploy.cmd` adds
  `-Elevate` for you (one UAC) so cache + install mirror + `resources\templates\` + `product.json` + `app.asar`
  are all written in one go; with arguments it forwards them unchanged. Both scripts end with `exit /b %RC%`,
  so a failed run can no longer look like a successful one.
- `release-manifest.js` — the single list of what a releasable package must contain (9 groups, 69 files) plus the
  byte-sync lists used by the two scripts below. Add a new required input here, nowhere else.
- `release-audit.js` — the release gate (`npm run audit`): completeness, byte equality with the maintainer source,
  one-click full write (`-Elevate` + `exit /b` + CRLF), and the version string in `package.json` / README / CHANGELOG.
  Any failure exits 1. Runs in `package` mode when there is no parent source tree.
- `sync-release.js` — mirrors the source tree into `release/` (prompts, runtime-layer inputs, deploy scripts).
  Refuses to run inside the package itself, so it can never create `release/release/`.
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
- `patch-asar.py` — the 7 asar patches, two-layer integrity, atomic apply/restore, `--status`.
- `apply-product-overrides.py` — merge `overrides/tools/*.compact.json` into `product.json`; `--restore`.
- `apply-skill-desc.py` — compress skill frontmatter `description` from `overrides/skills/desc-compact.json`.
- `render-runtime-templates.js` — render `runtime-templates/` and assert the injected blocks are gone.
- `render-tool-overrides.js` — render every override template against a realistic context.
- `verify-tool-overrides.py` — assert the override table keeps every entry and every variable.
- `export-tool-desc.py` / `tool-desc-map.py` / `measure-product.py` — export, map and size `product.json`.
- `_install.py` — shared resolver for `<install>\resources\...` (env → `targets.json` → error); imported by the python tools.
- `dump-system-prompt.py` / `dump-messages.py` / `trace-tokens.py` / `trace-turns.py` /
  `measure-prompt-blocks.py` / `measure-injected.py` / `measure-injection.py` / `list-desc.py` /
  `asar-grep.py` / `cache-compare.js` / `src-context.py` / `slim-mcp-descriptions.py` /
  `test-user-context-once.js` — read-only diagnostics
  (the last one asserts the `user-context-once` patch is intact).
- `test-asar-launch.py` — asserts the launch-only path: a probe is really started through
  `explorer.exe`, a missing exe or `--no-launch` is skipped, and the source contains no
  process-killing call. 8 checks.
- `uc-seen.py` — manage the `user-context-durable` markers under `<config>\uc-seen\`:
  `--count`, `--seed` (mark every session that already has a transcript, so an old chat stops
  re-injecting), `--clear <sid>`, `--prune <days>`.  `--seed` takes effect immediately: the patch
  re-reads the marker on every compose, no restart needed.
