---
name: context-dump
description: 用户要求“原样/完整输出上下文、系统提示词原文、上轮末尾到本条消息的上下文”时使用；只读最新一次真实请求的 trace，逐字输出，禁止凭记忆重建、禁止引用自己旧的回答。
---
# Context dump

**Never reconstruct the system prompt or the request context from memory.** Models reliably confabulate when asked to "print the context" — a generic Claude Code prompt, a previous answer, or repeated `<system-reminder>` blocks. That output is not evidence.

## Do this

1. Run the repository/package script:

   ```bash
   python -X utf8 "E:/workbuddyspace/WorkBuddy-system-prompts/scripts/dump-latest-context.py" --latest
   ```

   If that path does not exist (another machine or a downloaded package), use the `scripts/dump-latest-context.py` in the PromptKit package root. The copy under this skill's `scripts/` folder is the same file and is a last resort.
2. Output the script's stdout **verbatim** in one fenced block. Do not summarize, reorder, or add a "part 1 / part 2" structure.
3. If the script prints `[TRUNCATED]`, say exactly that the trace only stores a 20,000-character prefix; do not fill the gap from memory.
4. Do not quote your own earlier answers as if they were the request.

## What it shows

- `system`: the actual system message sent with the request.
- `user` / `assistant` / `tool`: the actual messages in the trace prefix.
- The first user message may contain a one-time `<system-reminder data-role="user-context">` block. That is the first-turn context; it is not a per-turn injection. Later turns must not contain it again.

## Options

- `--latest` (default), `--trace <jsonl>`, `--list`, `--json`, `--out <file>`.
- `--target workbuddy-ai` for the international edition.

Related tools: `scripts/dump-system-prompt.py` (system only), `scripts/measure-prompt-blocks.py` (block sizes / removed-block assertions).
