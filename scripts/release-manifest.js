// release-manifest.js —— 公开包"必须有什么"的唯一清单。
// release-audit.js（查）与 sync-release.js（同步）共用这一份，避免两边清单漂移。
// 新增一类需要随包发布的输入时，只改这里。
"use strict";

const REQUIRED = {
  "入口模板": [
    "welcome-mode/work.tpl",
    "welcome-mode/code.tpl",
    "welcome-mode/design.tpl",
  ],
  "公共片段": [
    "fragments/policy-common.md",
    "fragments/agent-contract.md",
    "fragments/tool-routing.md",
    "fragments/asking-questions.md",
    "fragments/workspace-safety.md",
    "fragments/memory-paths.md",
    "fragments/workbuddy-memory-system.md",
  ],
  "运行时模板（写安装目录，需管理员）": [
    "runtime-templates/user-context-identity.tpl",
    "runtime-templates/user-context-expert-identity.tpl",
    "runtime-templates/craft-mode-reminder.tpl",
    "runtime-templates/ask-mode-reminder.tpl",
  ],
  "工具描述精简表（overrides）": [
    "overrides/tools/session-tools.compact.json",
    "overrides/skills/desc-compact.json",
  ],
  "交互模式片段": [
    "plugins/workbuddy-builtin/interactionmode/craft/fragments/interaction.md",
    "plugins/workbuddy-builtin/interactionmode/plan/fragments/interaction.md",
    "plugins/workbuddy-builtin/interactionmode/ask/fragments/interaction.md",
    "plugins/workbuddy-builtin/interactionmode/quick/fragments/interaction.md",
    "plugins/workbuddy-builtin/interactionmode/expert/fragments/interaction.md",
  ],
  "技能（抽查）": [
    "plugins/workbuddy-builtin/skills/workbuddy-docs/SKILL.md",
    "plugins/workbuddy-builtin/skills/team-orchestration/SKILL.md",
  ],
  "部署脚本（一键全量必需）": [
    "scripts/deploy.cmd",
    "scripts/deploy.ps1",
    "scripts/apply.ps1",
    "scripts/restore.cmd",
    "scripts/restore.ps1",
    "scripts/target.ps1",
    "scripts/verify.ps1",
    "scripts/pull.ps1",
    "scripts/targets.json",
    "scripts/apply-product-overrides.py",
    "scripts/patch-asar.py",
    "scripts/_install.py",
  ],
  "渲染与诊断脚本（公开能力，不许悄悄丢）": [
    "scripts/render.js",
    "scripts/check.js",
    "scripts/render-check.js",
    "scripts/cache-prefix.js",
    "scripts/cache-compare.js",
    "scripts/check-memory-blocks.js",
    "scripts/render-runtime-templates.js",
    "scripts/render-tool-overrides.js",
    "scripts/verify-tool-overrides.py",
    "scripts/apply-skill-desc.py",
    "scripts/slim-mcp-descriptions.py",
    "scripts/tool-desc-map.py",
    "scripts/export-tool-desc.py",
    "scripts/asar-grep.py",
    "scripts/dump-messages.py",
    "scripts/dump-system-prompt.py",
    "scripts/dump-templates.py",
    "scripts/measure-injected.py",
    "scripts/measure-injection.py",
    "scripts/measure-product.py",
    "scripts/measure-prompt-blocks.py",
    "scripts/list-desc.py",
    "scripts/src-context.py",
    "scripts/trace-tokens.py",
    "scripts/trace-turns.py",
    "scripts/test-asar-launch.py",
    "scripts/test-user-context-once.js",
    "scripts/release-manifest.js",
    "scripts/release-audit.js",
    "scripts/sync-release.js",
  ],
  "包元数据": ["package.json", "CHANGELOG.md", "README.md", "examples/context.json"],
};

// 不进公开包的生成物（相对包根的前缀）。overrides/_dump 是 export-tool-desc.py 的抓取快照，
// 随时可重生成、部署不读它，留在源目录做证据即可。
const SYNC_IGNORE = ["overrides/_dump/"];

// 与源逐字节同步的目录（整个目录镜像）
const SYNC_DIRS = ["welcome-mode", "fragments", "plugins", "runtime-templates", "overrides"];

// 与源逐字节同步的单文件：部署链路 + 诊断脚本（这两棵树里必须一模一样）
const SYNC_FILES = [
  // 部署链路
  "scripts/deploy.cmd",
  "scripts/deploy.ps1",
  "scripts/apply.ps1",
  "scripts/restore.cmd",
  "scripts/restore.ps1",
  "scripts/target.ps1",
  "scripts/verify.ps1",
  "scripts/pull.ps1",
  "scripts/targets.json",
  // python 工具链
  "scripts/apply-product-overrides.py",
  "scripts/patch-asar.py",
  "scripts/_install.py",
  "scripts/apply-skill-desc.py",
  "scripts/slim-mcp-descriptions.py",
  "scripts/tool-desc-map.py",
  "scripts/export-tool-desc.py",
  "scripts/asar-grep.py",
  "scripts/dump-messages.py",
  "scripts/dump-system-prompt.py",
  "scripts/dump-templates.py",
  "scripts/measure-injected.py",
  "scripts/measure-injection.py",
  "scripts/measure-product.py",
  "scripts/measure-prompt-blocks.py",
  "scripts/list-desc.py",
  "scripts/src-context.py",
  "scripts/trace-tokens.py",
  "scripts/uc-seen.py",
  "scripts/trace-turns.py",
  "scripts/test-asar-launch.py",
  "scripts/verify-tool-overrides.py",
  // 发布工具与 JS 诊断
  "scripts/release-manifest.js",
  "scripts/release-audit.js",
  "scripts/sync-release.js",
  "scripts/cache-compare.js",
  "scripts/test-user-context-once.js",
  "scripts/test-removed-blocks.js",
];

// 有意保留两份实现的脚本：包内用同目录 node_modules / examples，工作区里指向 release\
// 只用"存在"来验，不做字节比较（各自的渲染用 npm run 真跑一遍）。
const SCRIPT_DIFF_ALLOWED = [
  "scripts/render-check.js",
  "scripts/cache-prefix.js",
  "scripts/check-memory-blocks.js",
  "scripts/render-runtime-templates.js",
  "scripts/render-tool-overrides.js",
];

module.exports = { REQUIRED, SYNC_DIRS, SYNC_FILES, SCRIPT_DIFF_ALLOWED, SYNC_IGNORE };
