---
name: ardot-canvas-verify
description: 设计稿/画布视觉验收：改完截图、截屏、看效果、检查、对比前后时使用；capture_screenshot → ViewImage → 清理临时文件，含 includeSchema 规则。
---

# Canvas visual verification

A design change is not done until you have looked at the rendered pixels.

1. Call `capture_screenshot` with the target node ID only. Do not pass `screenShotDir` or write screenshots into the project.
2. If it returns an image you can view, inspect it directly. If it returns a URL or path, download it into a managed temp directory such as `%TEMP%\wb-design-check\`.
3. Inspect the real image with `ViewImage` — never claim a visual check from a tool return value alone.
4. Delete the temp file afterwards. Never surface the path, and never save verification screenshots into the project, `.workbuddy/screenshots`, or the home directory.
5. If no image can be obtained, say plainly that visual verification did not happen.
6. When calling `fetch_editor_state`, always set `includeSchema: false`; canvas design rules come from the design assistant's `design-rules.md`, loaded on demand.
