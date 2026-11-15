# Scripts

## One-click deploy (Windows)

```bat
scripts\deploy.cmd
scripts\deploy.cmd -DryRun
scripts\deploy.cmd -TargetRoot D:\my-runtime
```

`deploy.ps1` reads `deploy.config.json` from the repository root:

- `targetRoot` — destination root; `%VAR%` is expanded. Defaults to `%USERPROFILE%/.promptkit/runtime`.
- `layout` — path templates: `{file}`, `{mode}`, `{name}`, `{path}`.
- `backup` — back up existing destination files to `backups/<timestamp>/` before overwrite (default `true`).

Every copied file is verified with SHA256. `-DryRun` prints the plan without writing; `-TargetRoot` overrides the config; `-NoBackup` skips the backup step. No admin is required for user-writable targets.

## Render / check

```bash
npm install
npm run render   # renders 3 templates × 5 modes into dist/
npm run check    # checks for template leftovers and required blocks
```

## What they do

- `deploy.ps1` — builds the source-to-target plan, backs up existing files, copies, and verifies hashes.
- `render.js` — builds a Nunjucks loader that maps `prompt-common/fragments/*` to `fragments/*` and `interactionmode-*/fragments/*` to the matching plugin directory, then renders all 15 combinations using `examples/context.json`.
- `check.js` — verifies every render has no `{{` / `{%` leftovers, contains the expected policy/contract/routing blocks, and respects the Ask/Quick mode boundaries.
