---
name: mcp-setup
description: 配置/新增/修改 MCP server（~/.workbuddy/mcp.json）：按 provider 官方字段写 command/args/env/headers/url，合并 mcpServers 而不覆盖，写凭据，并提示用户在连接器管理里 Trust。
---
# MCP setup

Config lives at `~/.workbuddy/mcp.json` — **not** `~/.workbuddy/.mcp.json`.

1. Check the provider's official docs or repo for the exact shape: `command`, `args`, `env`, `headers`, `url`. Never guess unsupported fields or arguments.
2. Read the existing file first; merge the new entry into `mcpServers`; do not overwrite other servers.
3. Write credentials where the docs say (`env`, `headers`, or args). If credentials are required but missing, ask the user for them.
4. Do not start the MCP server yourself. After writing, tell the user it will not activate by itself, and guide them to the custom connectors entry (top-right of connector management) → **Trust** on the new server.

Example (Playwright): `"command": "npx"`, `"args": ["@playwright/mcp@latest"]`.
