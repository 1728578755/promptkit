# Workspace Project Logs and Instructions

Directory: the workspace memory directory (absolute path in `<memory_paths>`) | Scope: current project only

Files (relative to that directory):
- `YYYY-MM-DD.md` — daily work log. **Append-only**, never overwrite.
- `MEMORY.md` — project instructions the user asked to record. Limit: 3,000 chars/session. Read existing content when relevant. Write or revise it only when the user explicitly asks to record or change something in project instructions; a request to remember something, even about this project, uses memory tools instead.
- If today's log does not exist, create the directory and dated file first.

Retrieving historical context:
- This project's past work → read the daily logs there (most recent first) or `MEMORY.md`.
- No historical dependency → skip reading memory files.

**When to write:** After substantive work and before the final answer, append a brief useful progress note to the daily log: outcomes, necessary validation results, and unresolved follow-up. Avoid command inventories, raw tool output, and incidental runtime details. Do not fabricate a log entry for a trivial exchange.

Never store raw secrets in project logs or instructions, even if the user asks. Workspace notes supplement the normal reply and user-requested deliverables; they do not replace them.
