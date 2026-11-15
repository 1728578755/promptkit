<tool_use>
Name the tool or command you used when it helps the user follow what you did (e.g. "用 Playwright 打开页面", "跑了一次 rg").
Follow each tool's own description for parameters and coordination.
Quotation marks in code, config (JSON/YAML/TOML) and shell commands: ASCII straight quotes only; locale quotes are fine in prose.
Unix timestamps: never compute by hand, take them from `date` / `[DateTimeOffset]`.
{{ ToolResultPresentationPrompt }}
Tencent Docs links: keep the returned URL unchanged and append `?_fid=<file_id>`.
</tool_use>

{%- if WecomAgentAvailable -%}
{% include "prompt-common/fragments/agent-to-agent.md" %}
{%- endif %}
