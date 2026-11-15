# Scripts

Public helpers for rendering and validating the prompt set. These are not deployment scripts: how to place the files depends on your runtime.

## Usage

```bash
npm install
npm run render   # renders 3 templates × 5 modes into dist/
npm run check    # checks for template leftovers and required blocks
```

## What they do

- `render.js` — builds a Nunjucks loader that maps `prompt-common/fragments/*` to `fragments/*` and `interactionmode-*/fragments/*` to the matching plugin directory, then renders all 15 combinations using `examples/context.json`.
- `check.js` — verifies every render has no `{{` / `{%` leftovers, contains the expected policy/contract/routing blocks, and respects the Ask/Quick mode boundaries.

Deployment scripts are environment-specific and intentionally not included; the main README documents the source-to-runtime path mapping.
