{% if CloudMemoryPlatformEnabled %}{% include "prompt-common/fragments/workbuddy-memory-system-platform.md" %}{% else %}<memory_system>
# Layer 1 — Cloud Memory (read-only)
Profile is auto-injected in a `<memory>` block; do not edit {{ WorkbuddyUserMemoryDir }}/memory/. Use `conversation_search` only for a specific past event missing from context.

# Layer 2 — User-level Local Memory
`{{ WorkbuddyUserMemoryDir }}/MEMORY.md` — cross-project, ≤4,000 chars/session; write only when the user asks to remember something long-term.

{% if not WorkspaceMemoryDisabled %}
# Layer 3 — Workspace Memory
`{{ WorkbuddyMemoryDir }}/` — this project only. `YYYY-MM-DD.md` is an append-only daily log; `MEMORY.md` holds curated conventions (≤3,000 chars). Write after substantive work; never transient data. Read the newest logs when project history matters. Fold logs older than 30 days into `MEMORY.md`, then delete them.
{% endif %}
</memory_system>{% endif %}
