#!/usr/bin/env node
/**
 * 用真实上下文渲染压缩后的工具描述模板，断言：
 *   1. 没有残留的 {{ }} / {% %}
 *   2. 关键变量被正确替换（defaultShell / bashDefaultTimeoutMs / 清单块）
 *   3. 渲染结果里没有 undefined / [object Object]
 *
 * 用法：node scripts/render-tool-overrides.js
 */
'use strict';
const path = require('path');
const fs = require('fs');
// 依赖与数据根的解析都在 _deps.js 里做，包内与工作区共用同一份脚本实现。
const { loadNunjucks, dataFile } = require('./_deps');

const nunjucks = loadNunjucks();
// overrides/ 在两种布局里都在内容树根下（包根 / 工作区里的 release/）
const FILE = dataFile('overrides/tools/session-tools.compact.json');
const data = JSON.parse(fs.readFileSync(FILE, 'utf8'));

const ctx = {
  defaultShell: '/bin/bash',
  platform: 'win32',
  bashDefaultTimeoutMs: '120000',
  bashMaxTimeoutMs: '600000',
  psEdition: 'core',
  toolSearchDeferredTools: '- foo: bar\n- baz: qux',
  skillGroups: [
    { folder: 'workbuddy-builtin', skills: [
      { name: 'visualizer', description: 'render inline diagrams', truncatedDescription: 'render inline diagrams' },
      { name: 'library', description: 'x'.repeat(400), truncatedDescription: 'x'.repeat(80) + '…' },
    ] },
  ],
  skillsOverview: { omittedCount: 2 },
  agents: [
    { name: 'sheet-agent', description: 'sheet helper', asTool: true, tools: ['Read', 'Bash'] },
    { name: 'hidden', description: 'nope', asTool: false, tools: [] },
  ],
  teamEnabled: true,
};

const EXPECT = {
  'tool-bash-description': ['/bin/bash', 'Git Bash', '120000', '600000'],
  'tool-agent-description': ['sheet-agent', 'Read,Bash'],
  'tool-skill-description': ['folder: workbuddy-builtin', 'visualizer: render inline diagrams', 'Omitted skills: 2.'],
  'tool-toolsearch-description': ['<available_deferred_tools>', '- foo: bar'],
  'tool-powershell-description': ['PowerShell 7+'],
};

let fails = 0;
const pad = (s, n) => String(s).padEnd(n);
const num = (s, n) => String(s).padStart(n);
console.log(pad('key', 42) + num('in', 8) + num('out', 8) + '  checks');
console.log('-'.repeat(96));
for (const p of data.prompts) {
  const name = p.name;
  let out;
  try {
    out = nunjucks.renderString(p.template, ctx);
  } catch (e) {
    console.log(pad(name, 42) + num(p.template.length, 8) + num('-', 8) + '  RENDER ERROR: ' + e.message);
    fails++;
    continue;
  }
  const notes = [];
  if (/\{\{|\{%/.test(out)) { notes.push('LEFTOVER TAGS'); fails++; }
  if (/undefined|\[object Object\]/.test(out)) { notes.push('BAD VALUE'); fails++; }
  for (const token of (EXPECT[name] || [])) {
    if (!out.includes(token)) { notes.push('MISSING "' + token + '"'); fails++; }
  }
  console.log(pad(name, 42) + num(p.template.length, 8) + num(out.length, 8) + '  ' +
    (notes.length ? notes.join('; ') : 'OK'));
}
console.log('-'.repeat(96));
console.log(fails === 0 ? 'RENDER OK: all templates render clean' : 'RENDER FAIL: ' + fails + ' issue(s)');
process.exit(fails === 0 ? 0 : 1);
