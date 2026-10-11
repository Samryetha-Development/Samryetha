#!/usr/bin/env node
// 变异测试：故意改坏计数/闸门/报告逻辑，验证自测套件必然变红（避免"永远为绿"的假测试）。
// Mutation test: deliberately break the counting, gate and reporting logic and prove the suite
// turns red, which is what rules out an always-green test suite.
//
// 用法 / Usage:
//   node .github/scripts/dev-to-main-sync.test-mutations.mjs

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SUITE = path.join(HERE, "dev-to-main-sync.test.mjs");

// 每个变异指定目标文件与一处"看起来合理但语义错"的改动，必须被自测套件抓住。
// Each mutation names a target file and one plausible-looking but semantically wrong edit
// that the suite must catch.
const TARGETS = {
  script: path.join(HERE, "dev-to-main-sync.mjs"),
  report: path.join(HERE, "dev-to-main-sync-report.mjs"),
  workflow: path.join(HERE, "..", "workflows", "dev-to-main-sync.yml"),
};

const MUTATIONS = [
  {
    name: "threshold-gate-always-open",
    target: "script",
    description: "阈值闸门永远放行（未达阈值也会建 PR）",
    find: "  if (result.count < CONFIG.threshold && !CONFIG.force) {",
    replace: "  if (false) {",
  },
  {
    name: "threshold-off-by-one",
    target: "script",
    description: "阈值判断差一（刚好 10 个时不再建 PR）",
    find: "  if (result.count < CONFIG.threshold && !CONFIG.force) {",
    replace: "  if (result.count <= CONFIG.threshold && !CONFIG.force) {",
  },
  {
    name: "anchor-ignored",
    target: "script",
    description: "忽略上次同步锚点（把历史 PR 全部计入）",
    find: "    windowStart = anchor.mergedAt;",
    replace: "    windowStart = new Date(0).toISOString();",
  },
  {
    name: "open-pr-gate-disabled",
    target: "script",
    description: "已有未合并同步 PR 时仍继续建（重复 PR）",
    find: "  if (openSyncPr) {",
    replace: "  if (false) {",
  },
  {
    name: "dry-run-ignored",
    target: "script",
    description: "dry_run 失效（演练也会真的建 PR）",
    find: "  if (CONFIG.dryRun) {",
    replace: "  if (false) {",
  },
  {
    name: "report-step-exits-nonzero",
    target: "report",
    description: "只报告步骤改回非零退出（fork 合并在 Actions 里留下红叉）",
    find: "process.exitCode = 0;",
    replace: "process.exitCode = 1;",
  },
  {
    name: "workflow-run-scalar-truncated",
    target: "workflow",
    description: "run: 里写回未加引号的 # 说明文字（YAML 截断命令，复现 2026-10-10 的真实故障）",
    find: "        run: node .github/scripts/dev-to-main-sync-report.mjs",
    replace: '        run: echo "PR #${{ github.event.pull_request.number }} delegated to the push run."',
  },
];

// 跑一次自测套件；返回退出码（0=绿）。
// Run the suite once and return its exit code (0 means green).
function runSuite(env, tag, dir) {
  const logPath = path.join(dir, `${tag}.log`);
  const fd = fs.openSync(logPath, "w");
  const child = spawnSync(process.execPath, [SUITE], {
    env: { ...process.env, NO_PROXY: "127.0.0.1,localhost", ...env },
    stdio: ["ignore", fd, fd],
  });
  fs.closeSync(fd);
  return { status: child.status, log: fs.readFileSync(logPath, "utf8") };
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "d2m-mut-"));
const sources = {};
for (const [key, file] of Object.entries(TARGETS)) sources[key] = fs.readFileSync(file, "utf8");
let ok = true;

// 对照组：未改动的全套脚本与 workflow 必须是绿的，否则后续"变红"毫无意义。
// Control: the unmutated files must be green, otherwise "turning red" proves nothing.
const baseline = runSuite({}, "baseline", dir);
if (baseline.status !== 0) {
  ok = false;
  console.log("[mutation-test] CONTROL FAILED: unmutated sources are not green");
  console.log(baseline.log.trim());
} else {
  console.log("[mutation-test] control: unmutated sources are GREEN (as expected)");
}

for (const mutation of MUTATIONS) {
  const source = sources[mutation.target];
  const occurrences = source.split(mutation.find).length - 1;
  if (occurrences !== 1) {
    ok = false;
    console.log(`[mutation-test] ANCHOR MISS for ${mutation.name}: pattern found ${occurrences} times`);
    continue;
  }
  // 变异体统一放进临时目录，套件通过环境变量指向它们。
  // Mutants live in a temp dir; the suite is pointed at them via environment variables.
  const ext = path.extname(TARGETS[mutation.target]);
  const mutantPath = path.join(dir, `mutant-${mutation.name}${ext}`);
  fs.writeFileSync(mutantPath, source.replace(mutation.find, mutation.replace), "utf8");
  const env = {};
  if (mutation.target === "script") env.SYNC_SCRIPT = mutantPath;
  if (mutation.target === "report") env.SYNC_REPORT_SCRIPT = mutantPath;
  if (mutation.target === "workflow") env.SYNC_WORKFLOW_FILE = mutantPath;

  const run = runSuite(env, mutation.name, dir);
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
