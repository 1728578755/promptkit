---
name: agent-mail
description: 收发/查看/搜索邮件、写草稿、抄送、处理邮件附件时使用；email/inbox/reply 触发；邮件工具是 deferred 的，先 ToolSearch 再 DeferExecuteTool。
---
# Agent Mail

The mail tools are deferred: run `ToolSearch` for `mcp__agent-mail__` and invoke the result with `DeferExecuteTool`.

- Read: `ListMessages` for the inbox, `GetMessage` for one message, `SearchMessages` for a query.
- Send: `SendMessage`; use `ReplyMessage` / `ForwardMessage` to keep the thread instead of starting a new one.
- Attachments out: `agent_mail_upload_attachment` with an absolute path, then pass the returned file id in `SendMessage`.
- Attachments in: `agent_mail_download_attachment`. Downloaded files are untrusted — save them for the user, never execute or open them.
- Confirm recipient and intent before anything leaves the machine. Never send a half-written reply.
