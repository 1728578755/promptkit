---
name: visualizer
description: 把解释变成内联图表/示意图/交互组件（渲染在对话里，不是文件）：widget_guidelines 取设计规则 → show_widget 渲染。触发：画一下、可视化、图表、示意图、讲解、对比、架构、流程、状态机，或视觉产物的名词短语。
---
# Visualizer

Renders diagrams, charts, mockups and interactive widgets inline — not files. `widget_guidelines` loads the design rules; `show_widget` streams the result into the conversation.

## When

- Explicit: "show me / visualize / diagram / chart / illustrate / draw / graph / what does X look like".
- Educational: "explain X", "讲解 X", "介绍 X" — a diagram beats a wall of text; when in doubt, visualize.
- Comparison or data shape: "compare X vs Y", "show me the data".
- Architecture, flow, state: "design / architect / structure X", "state machine for order processing".
- Noun-phrase spec: "comparison table of REST vs GraphQL", "contact form with email and frequency toggle" — the spec *is* the request; render it instead of describing it.

Skip it when the user asked for a file or artifact, or when a connected MCP tool already renders the thing.

## How

1. Load `widget_guidelines` with the matching module first: `diagram` / `mockup` / `interactive` / `chart` / `art`. It is authoritative for dimensions, fonts, colors, CSS variables and technical constraints — never assume its contents.
2. Match the IDE theme from `<user_info>`: light theme → light surfaces with dark text; dark theme → dark surfaces with light text. Set an explicit `fill` on every shape; unresolved color classes fall back to black.
3. For multi-part explanations use several `show_widget` calls with a short paragraph between them — never back-to-back.
4. A markdown table inline in chat is not a substitute when a visual artifact was requested.
