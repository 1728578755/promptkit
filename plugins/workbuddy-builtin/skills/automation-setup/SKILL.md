---
name: automation-setup
description: 定时/重复任务、提醒、日程、推迟/改期/暂停/删除自动化时使用；schedule/reminder/cron 触发。
---
# Automation setup

One automation per logical task. Before creating anything, call `automation_update` with `mode: "list"` and reuse or update an existing one instead of adding a duplicate.

## Create or skip

- Create for a recurring cadence (每天 / 每周 / 每月 / 工作日 / 每 N 小时) or a specific future reminder.
- Skip for one-off work you can finish in this turn; a reminder minutes away inside a live session does not need an automation.

## Scheduling

- `scheduleType: "once"` needs an ISO 8601 `scheduledAt`.
- `scheduleType: "recurring"` needs an RFC 5545 `rrule`; the frequency lives there, not in the prompt.
- Several days or dates → one rrule with comma-separated `BYDAY` / `BYMONTHDAY`; never create one automation per day.
- `validFrom` / `validUntil` bound a schedule; leave them out when the task should run indefinitely.

## The prompt

Future runs cannot see this conversation, so the prompt must stand alone:

- Name the exact files, paths, data sources and output location.
- State the success check the run must perform before reporting done.
- Describe only the task; the schedule belongs in the tool fields.
- If the brief is long, write it to a file and point the prompt at that file.

## After the call

Report the cadence in natural language ("每周一 09:00"), what it will do, and where it writes. Never quote the raw rrule.
