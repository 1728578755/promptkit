#!/usr/bin/env node
// 语义自测：验证 user-context-once 补丁替换后的两段表达式行为，而不是只做语法检查。
//
// 被测的两段源码**逐字**取自打完补丁的 bundle（见 WORKLOG-2026-11-17-user-context-once.md 的打印）：
//   渲染器：w=self.localStorage?.getItem(a)   !=null
//   渲染器：self.localStorage?.setItem(a,`1`     )
//   正文  ：x=r?0:renderConnectorStatusBlock(o);x[0]  &&ee.push(…)
//
// 用法：node scripts/test-user-context-once.js
let failures = 0;
function check(name, ok, extra) {
  console.log(`${ok ? "OK  " : "FAIL"}  ${name}${extra ? "  " + extra : ""}`);
  if (!ok) failures++;
}

// ---- 1. 渲染器：标记读取 ----------------------------------------------------
function readFlag(storage, sessionId) {
  const a = sessionId;
  const self = { localStorage: storage };
  return eval("self.localStorage?.getItem(a)   !=null"); // 与补丁逐字一致
}
function writeFlag(storage, sessionId) {
  const a = sessionId;
  const self = { localStorage: storage };
  return eval("self.localStorage?.setItem(a,`1`     )");
}

const store = new Map();
const fakeLS = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, v),
};

check("首轮：标记不存在 → false（会注入）", readFlag(fakeLS, "sess-A") === false);
writeFlag(fakeLS, "sess-A");
check("写标记后：同一会话 → true（不再注入）", readFlag(fakeLS, "sess-A") === true);
check("未写过的会话 → false（新会话仍首轮注入）", readFlag(fakeLS, "sess-B") === false);
check("标记值为 `1`", store.get("sess-A") === "1", `实际=${JSON.stringify(store.get("sess-A"))}`);

// localStorage 不可用（worker / 被拒）：必须降级为 false，绝不抛错
let threw = false;
let degraded;
try {
  degraded = eval("self.localStorage?.getItem(a)   !=null".replace("self", "({})  "));
} catch (e) {
  threw = true;
}
check("没有 localStorage 时不抛错，退化为 false", threw === false && degraded === false,
  `threw=${threw} value=${String(degraded)}`);

// ---- 2. 正文：connector-status 与 v 一起被门禁 ---------------------------------
function computeX(r, block) {
  const o = {};
  const renderConnectorStatusBlock = () => block;
  return eval("x=r?0:renderConnectorStatusBlock(o)");
}
const lines = ["<connector-status>", "a: connected", "</connector-status>"];

check("首轮（r=false）→ x 是数组", Array.isArray(computeX(false, lines)));
check("非首轮（r=true）→ x=0", computeX(true, lines) === 0);

function pushes(r, block) {
  let x = computeX(r, block);
  const ee = [];
  const src = "x[0]  &&ee.push(x.join(`\\n`))";
  eval(src);
  return ee.length;
}
check("首轮：push 1 段 connector-status", pushes(false, lines) === 1);
check("非首轮：push 0 段（reminder 整段不产生）", pushes(true, lines) === 0);
check("首轮但 connector 为空数组：push 0 段", pushes(false, []) === 0);

console.log(failures === 0 ? "\nUSER-CONTEXT-ONCE OK" : `\nFAILURES: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
