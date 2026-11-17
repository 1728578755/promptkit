---
name: team-orchestration
description: 复杂任务要多角色并行、拆给多个 agent、或要实时查看/管理团队状态时使用；走团队面板 TeamCreate → SendMessage → TeamDelete，而不是零散子代理。
---
# Team orchestration (团队面板)

The team panel is the default for parallel or multi-role work: every teammate stays visible and controllable.

## Staff the panel

1. Derive the roles from the deliverable, not from the tool list (e.g. researcher, implementer, verifier).
2. `TeamCreate` with a `name` and a short `description`.
3. Give each teammate **one** task per message and a self-contained brief: goal, files/paths, constraints, expected output, done check.
4. A large task normally needs 3–20 teammates; never create more than the work needs.

## Run it

- Send work with `SendMessage` (`type: "message"`, `recipient`, `content`, `summary`).
- Keep the lead's context lean: delegate reading and summarising, not decisions.
- Track status honestly: `in_progress` before starting, `completed` only when the check passes.
- Broadcast only for a real team-wide blocker; otherwise message one recipient.

## Close

- Ask idle teammates to stop with `type: "shutdown_request"`, then `TeamDelete`.
- Relay findings and failures to the user yourself; teammate replies are not shown to them.

## When not to use it

- A single one-off subtask with no coordination can use the `Agent` tool directly.
- Do not open the panel for work you can finish in one or two calls.
