# Scripts

## One-click deploy (Windows)

```bat
scripts\deploy.cmd
scripts\deploy.cmd -DryRun
scripts\deploy.cmd -NoInstall
scripts\deploy.cmd -Template welcome-mode\work.tpl
scripts\deploy.cmd -Skill team-orchestration
```

`deploy.ps1` auto-detects the installed runtime (install directory + plugin cache), delegates to `apply.ps1`, asserts 7 built-in skills, then runs `verify.ps1`.

- Admin: writes the install mirror and the plugin cache; non-admin: cache only (the runtime read point).
- If the install folder is not found, non-admin runs automatically fall back to cache-only; `verify` also switches to cache-only. Admin runs still ask for `-WorkBuddyPath`.
- Existing files are backed up to `backups/<timestamp>/`; every copy is verified with SHA256.
- `-Detailed` prints the full mapping; `-SkipVerify` skips the consistency check; `-Template` / `-Skill` target one file or one skill.

## Rollback

```powershell
scripts\restore.ps1 -BackupPath backups\<timestamp>
```

## Render / check

```bash
npm install
npm run render   # renders 3 templates × 5 modes into dist/
npm run check    # checks for template leftovers and required blocks
```

## Files

- `deploy.ps1` / `deploy.cmd` — one-click entry, auto-detects the runtime.
- `apply.ps1` — copy engine: source tree → install mirror + plugin cache, backups, skill registration.
- `verify.ps1` — consistency check across project / install / cache.
- `restore.ps1` — restore a previous backup.
- `render.js` — Nunjucks render of all 15 template/mode combinations.
- `check.js` — structural assertions on the renders.
