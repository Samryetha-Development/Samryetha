#!/usr/bin/env node
// 变异测试：故意改坏计数/闸门逻辑，验证自测套件必然变红（避免"永远为绿"的假测试）。
// Mutation test: deliberately break the counting/gate logic and prove the suite turns red,
// which is what rules out an always-green test suite.
//
// 用法 / Usage:
//   node .github/scripts/dev-to-main-sync.test-mutations.mjs

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(HERE, "dev-to-main-sync.mjs");
const SUITE = path.join(HERE, "dev-to-main-sync.test.mjs");

// 每个变异都是一处"看起来合理但语义错"的改动，必须被自测套件抓住。
// Each mutation is a plausible-looking but semantically wrong edit that the suite must catch.
const MUTATIONS = [
  {
    name: "threshold-gate-always-open",
    description: "阈值闸门永远放行（未达阈值也会建 PR）",
    find: "  if (result.count < CONFIG.threshold && !CONFIG.force) {",
    replace: "  if (false) {",
  },
  {
    name: "threshold-off-by-one",
    description: "阈值判断差一（刚好 10 个时不再建 PR）",
    find: "  if (result.count < CONFIG.threshold && !CONFIG.force) {",
    replace: "  if (result.count <= CONFIG.threshold && !CONFIG.force) {",
  },
  {
    name: "anchor-ignored",
    description: "忽略上次同步锚点（把历史 PR 全部计入）",
    find: "    windowStart = anchor.mergedAt;",
    replace: "    windowStart = new Date(0).toISOString();",
  },
  {
    name: "open-pr-gate-disabled",
    description: "已有未合并同步 PR 时仍继续建（重复 PR）",
    find: "  if (openSyncPr) {",
    replace: "  if (false) {",
  },
  {
    name: "dry-run-ignored",
    description: "dry_run 失效（演练也会真的建 PR）",
    find: "  if (CONFIG.dryRun) {",
    replace: "  if (false) {",
  },
];

// 跑一次自测套件；返回退出码（0=绿）。
// Run the suite once and return its exit code (0 means green).
function runSuite(scriptPath, tag, dir) {
  const logPath = path.join(dir, `${tag}.log`);
  const fd = fs.openSync(logPath, "w");
  const child = spawnSync(process.execPath, [SUITE], {
    env: { ...process.env, SYNC_SCRIPT: scriptPath, NO_PROXY: "127.0.0.1,localhost" },
    stdio: ["ignore", fd, fd],
  });
  fs.closeSync(fd);
  return { status: child.status, log: fs.readFileSync(logPath, "utf8") };
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "d2m-mut-"));
const source = fs.readFileSync(SCRIPT, "utf8");
let ok = true;

// 对照组：未改动的脚本必须是绿的，否则后续"变红"毫无意义。
// Control: the unmutated script must be green, otherwise "turning red" proves nothing.
const baseline = runSuite(SCRIPT, "baseline", dir);
if (baseline.status !== 0) {
  ok = false;
  console.log("[mutation-test] CONTROL FAILED: unmutated script is not green");
  console.log(baseline.log.trim());
} else {
  console.log("[mutation-test] control: unmutated script is GREEN (as expected)");
}

for (const mutation of MUTATIONS) {
  const occurrences = source.split(mutation.find).length - 1;
  if (occurrences !== 1) {
    ok = false;
    console.log(`[mutation-test] ANCHOR MISS for ${mutation.name}: pattern found ${occurrences} times`);
    continue;
  }
  const mutantPath = path.join(dir, `mutant-${mutation.name}.mjs`);
  fs.writeFileSync(mutantPath, source.replace(mutation.find, mutation.replace), "utf8");
  const run = runSuite(mutantPath, mutation.name, dir);
  if (run.status === 0) {
    ok = false;
    console.log(`[mutation-test] SURVIVED (bad): ${mutation.name} — ${mutation.description}`);
  } else {
    console.log(`[mutation-test] killed: ${mutation.name} — ${mutation.description}`);
  }
}

console.log(`[mutation-test] ${ok ? "ALL MUTANTS KILLED" : "FAILED"}`);
if (ok) fs.rmSync(dir, { recursive: true, force: true });
else console.log(`[mutation-test] logs kept at ${dir}`);
process.exitCode = ok ? 0 : 1;
