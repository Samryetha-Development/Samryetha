#!/usr/bin/env node
// dev -> main 同步决策脚本的本地离线测试：用内置 stub 服务覆盖各条分支。
// Offline local tests for the dev -> main sync decision script, driven by a built-in stub API.
//
// 用法 / Usage:
//   node .github/scripts/dev-to-main-sync.test.mjs
//   SYNC_SCRIPT=<被改坏的副本> node .github/scripts/dev-to-main-sync.test.mjs   # 变异测试用
//
// 注意两点本机沙箱约束 / Two sandbox constraints worth knowing:
//  1. 管道 stdio 启动子进程会 EPERM，所以子进程输出走文件描述符。
//     Piped stdio to a child fails with EPERM, so child output uses a file descriptor.
//  2. 必须用异步 spawn，不能用 spawnSync：stub 服务跑在本进程事件循环里，
//     同步等待会把事件循环堵死，子进程的请求永远得不到响应（死锁）。
//     spawn must be async: the stub shares this process's event loop, and a blocking
//     spawnSync would freeze it so the child's request could never be answered.

import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = process.env.SYNC_SCRIPT || path.join(HERE, "dev-to-main-sync.mjs");
const TEST_REPO = "test-org/test-repo";
const NOW = Date.now();
const HOUR = 3600000;
const CHILD_TIMEOUT_MS = 30000;

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "d2m-test-"));
const CASE_DIR = fs.mkdtempSync(path.join(TMP, "cases-"));

// 构造一条 PR 记录，只填脚本真正读取的字段。
// Build one PR record with only the fields the script actually reads.
function mkPr(number, { base, head, mergedHoursAgo = null, closedHoursAgo = null, title = null }) {
  return {
    number,
    title: title || `PR ${number}`,
    base: { ref: base },
    head: { ref: head },
    merged_at: mergedHoursAgo === null ? null : new Date(NOW - mergedHoursAgo * HOUR).toISOString(),
    closed_at: closedHoursAgo === null ? null : new Date(NOW - closedHoursAgo * HOUR).toISOString(),
    html_url: `https://example.invalid/pull/${number}`,
    user: { login: "tester" },
  };
}

// 一个可编程的 GitHub API 桩：按 state/base 过滤返回 PR，并记录 POST。
// A programmable GitHub API stub: filters PRs by state/base and records POSTs.
function createStub() {
  const state = { pulls: [], aheadBy: 1, posts: [], lastBody: null, failCreate: null };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const send = (code, obj) => {
      const body = JSON.stringify(obj);
      res.writeHead(code, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
      res.end(body);
    };
    if (req.method === "POST" && url.pathname.endsWith("/pulls")) {
      if (state.failCreate) {
        req.resume();
        req.on("end", () => send(state.failCreate.status, state.failCreate.body));
        return;
      }
      let raw = "";
      req.on("data", (chunk) => { raw += chunk; });
      req.on("end", () => {
        state.posts.push(raw);
        state.lastBody = JSON.parse(raw);
        send(201, { number: 4242, html_url: "https://example.invalid/pull/4242" });
      });
      return;
    }
    if (url.pathname.includes("/compare/")) {
      return send(200, { ahead_by: state.aheadBy, behind_by: 0, total_commits: state.aheadBy });
    }
    if (url.pathname.endsWith("/pulls")) {
      const want = url.searchParams.get("state");
      const base = url.searchParams.get("base");
      let list = state.pulls;
      if (want === "open") list = list.filter((p) => p.merged_at === null && p.closed_at === null);
      if (want === "closed") list = list.filter((p) => p.merged_at !== null || p.closed_at !== null);
      if (base) list = list.filter((p) => p.base.ref === base);
      return send(200, list);
    }
    return send(404, { message: "not found" });
  });
  return { state, server };
}

// 异步跑一次被测脚本，返回退出码与结构化结果。
// Run the script once, asynchronously, and return its exit code plus the structured result.
function runScript(state, env, tag) {
  const logPath = path.join(CASE_DIR, `${tag}.log`);
  const resultPath = path.join(CASE_DIR, `${tag}.result.json`);
  return new Promise((resolve) => {
    const fd = fs.openSync(logPath, "w");
    const child = spawn(process.execPath, [SCRIPT], {
      env: {
        ...process.env,
        SYNC_REPO: TEST_REPO,
        GH_TOKEN: "stub-token",
        SYNC_API_BASE: state.baseUrl,
        SYNC_RESULT_FILE: resultPath,
        SYNC_MAX_PAGES: "3",
        HTTP_PROXY: "",
        HTTPS_PROXY: "",
        NO_PROXY: "127.0.0.1,localhost",
        ...env,
      },
      stdio: ["ignore", fd, fd],
    });
    const timer = setTimeout(() => child.kill("SIGKILL"), CHILD_TIMEOUT_MS);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      try { fs.closeSync(fd); } catch { /* already closed */ }
      const log = fs.readFileSync(logPath, "utf8");
      let result = null;
      if (fs.existsSync(resultPath)) result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
      resolve({
        status: code,
        signal,
        timedOut: signal === "SIGKILL",
        log,
        result,
        posts: state.posts.slice(),
      });
    });
  });
}

const failures = [];
let passed = 0;

// 断言辅助：失败时收集原因而不是立即崩掉，便于一次性看到全部问题。
// Assertion helper that collects failures instead of throwing, so one run shows every problem.
function check(tag, condition, detail) {
  if (condition) passed += 1;
  else failures.push(`${tag}: ${detail}`);
}

// 断言一个场景的结论、退出码，并核对是否真的建了 PR。
// Assert one scenario's decision, exit code, and whether a PR was actually created.
function assertScenario(tag, run, expectations) {
  check(tag, run.status === expectations.status,
    `exit code ${run.status}${run.timedOut ? " (TIMED OUT)" : ""} expected ${expectations.status}; log tail: ${run.log.trim().slice(-400)}`);
  check(tag, run.result !== null, "no result file written");
  if (!run.result) return;
  if (expectations.decision) {
    check(tag, run.result.decision === expectations.decision, `decision=${run.result.decision} expected=${expectations.decision}`);
  }
  if (expectations.reasonPrefix) {
    check(tag, String(run.result.reason).startsWith(expectations.reasonPrefix), `reason=${run.result.reason} expected prefix=${expectations.reasonPrefix}`);
  }
  if (expectations.count !== undefined) check(tag, run.result.count === expectations.count, `count=${run.result.count} expected=${expectations.count}`);
  if (expectations.windowKind) check(tag, run.result.windowKind === expectations.windowKind, `windowKind=${run.result.windowKind}`);
  if (expectations.posted !== undefined) {
    check(tag, (run.posts.length > 0) === expectations.posted, `posts=${run.posts.length} expected posted=${expectations.posted}`);
  }
}

async function main() {
  const { state, server } = createStub();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  state.baseUrl = `http://127.0.0.1:${server.address().port}`;

  const reset = (pulls, aheadBy) => {
    state.pulls = pulls;
    state.aheadBy = aheadBy;
    state.posts = [];
    state.lastBody = null;
    state.failCreate = null;
  };

  // 锚点 = 3 天前合并的 dev -> main 同步 PR（PR 500）。
  // Anchor = a dev -> main sync PR merged three days ago (PR 500).
  const anchor = mkPr(500, { base: "main", head: "dev", mergedHoursAgo: 72 });
  // 噪声：合入 main 但不是来自 dev 的 PR，不应被当成锚点。
  // Noise: a PR merged into main that did not come from dev must never become the anchor.
  const nonSyncAnchor = mkPr(501, { base: "main", head: "release/0.2", mergedHoursAgo: 60 });
  const mergeToDev = (n, hoursAgo) => mkPr(n, { base: "dev", head: `feat/x${n}`, mergedHoursAgo: hoursAgo });
  const run = (env, tag) => runScript(state, env, tag);

  // 场景 1：未达阈值（9 < 10）——不建 PR。
  // Scenario 1: below threshold (9 < 10), no PR is created.
  reset([anchor, nonSyncAnchor, ...Array.from({ length: 9 }, (_, i) => mergeToDev(600 + i, 40 - i))], 5);
  assertScenario("below-threshold", await run({ SYNC_THRESHOLD: "10" }, "s1"), {
    status: 0, decision: "skip", reasonPrefix: "below-threshold", count: 9, posted: false,
  });

  // 场景 2：刚好达到阈值（10 >= 10）——建 PR，且 PR 的 base/head 与正文正确。
  // Scenario 2: exactly at threshold (10 >= 10), a PR is created with the right base/head and body.
  reset([anchor, nonSyncAnchor, ...Array.from({ length: 10 }, (_, i) => mergeToDev(700 + i, 40 - i))], 5);
  {
    const r = await run({ SYNC_THRESHOLD: "10" }, "s2");
    assertScenario("at-threshold", r, { status: 0, decision: "created", reasonPrefix: "threshold-met", count: 10, posted: true });
    check("at-threshold", state.lastBody !== null && state.lastBody.base === "main" && state.lastBody.head === "dev",
      `payload base/head = ${state.lastBody && state.lastBody.base}/${state.lastBody && state.lastBody.head}`);
    check("at-threshold", state.lastBody !== null && /#700\b/.test(String(state.lastBody.body)),
      "PR body does not list the covered PRs");
    check("at-threshold", r.result.pull && r.result.pull.number === 4242, "created PR number not reported back");
  }

  // 场景 3：已达阈值但已有未合并的同步 PR——跳过，绝不建第二个。
  // Scenario 3: threshold met but a sync PR is already open; skip and never open a second one.
  reset([anchor, nonSyncAnchor, mkPr(900, { base: "main", head: "dev" }),
    ...Array.from({ length: 12 }, (_, i) => mergeToDev(800 + i, 40 - i))], 5);
  assertScenario("open-sync-pr-exists", await run({ SYNC_THRESHOLD: "10" }, "s3"), {
    status: 0, decision: "skip", reasonPrefix: "open-sync-pr-exists", count: 12, posted: false,
  });

  // 场景 4：已达阈值但两分支无差异——跳过（否则 PR API 只会返回 422）。
  // Scenario 4: threshold met but no diff between branches; skip (the PR API would 422).
  reset([anchor, nonSyncAnchor, ...Array.from({ length: 12 }, (_, i) => mergeToDev(810 + i, 40 - i))], 0);
  assertScenario("no-diff", await run({ SYNC_THRESHOLD: "10" }, "s4"), {
    status: 0, decision: "skip", reasonPrefix: "no-diff", posted: false,
  });

  // 场景 5：冷却期内（1 小时前刚有一个同步 PR 被关掉且未合并）——跳过。
  // Scenario 5: inside the cooldown window (a sync PR was closed unmerged an hour ago); skip.
  reset([anchor, nonSyncAnchor, mkPr(910, { base: "main", head: "dev", closedHoursAgo: 1 }),
    ...Array.from({ length: 11 }, (_, i) => mergeToDev(820 + i, 40 - i))], 5);
  assertScenario("cooldown", await run({ SYNC_THRESHOLD: "10", SYNC_COOLDOWN_HOURS: "24" }, "s5"), {
    status: 0, decision: "skip", reasonPrefix: "cooldown-after-rejected", posted: false,
  });

  // 场景 5b：同步 PR 是 3 天前被关掉的，已过冷却期——应当照常建。
  // Scenario 5b: the rejected sync PR is three days old, so the cooldown expired and we create.
  reset([anchor, nonSyncAnchor, mkPr(911, { base: "main", head: "dev", closedHoursAgo: 72 }),
    ...Array.from({ length: 11 }, (_, i) => mergeToDev(822 + i, 40 - i))], 5);
  assertScenario("cooldown-expired", await run({ SYNC_THRESHOLD: "10", SYNC_COOLDOWN_HOURS: "24" }, "s5b"), {
    status: 0, decision: "created", posted: true,
  });

  // 场景 6：dry_run 演练——给出 would-create 但绝不发出 POST。
  // Scenario 6: dry run reports would-create and never issues a POST.
  reset([anchor, nonSyncAnchor, ...Array.from({ length: 12 }, (_, i) => mergeToDev(830 + i, 40 - i))], 5);
  assertScenario("dry-run", await run({ SYNC_THRESHOLD: "10", SYNC_DRY_RUN: "true" }, "s6"), {
    status: 0, decision: "would-create", count: 12, posted: false,
  });

  // 场景 7：锚点前的 PR 不得被计入（9 条窗口内 + 6 条窗口前的诱饵，计数必须是 9）。
  // Scenario 7: PRs merged before the anchor must be excluded (9 in-window plus 6 decoys => 9).
  reset([anchor, nonSyncAnchor,
    ...Array.from({ length: 9 }, (_, i) => mergeToDev(840 + i, 40 - i)),
    ...Array.from({ length: 6 }, (_, i) => mergeToDev(950 + i, 120 + i))], 5);
  assertScenario("anchor-excludes-older", await run({ SYNC_THRESHOLD: "10" }, "s7"), {
    status: 0, decision: "skip", reasonPrefix: "below-threshold", count: 9, posted: false,
  });

  // 场景 8：force 演练用参数可越过阈值软闸门（硬闸门仍生效，见场景 3）。
  // Scenario 8: force bypasses the soft threshold gate, while hard gates still apply (see scenario 3).
  reset([anchor, nonSyncAnchor, ...Array.from({ length: 3 }, (_, i) => mergeToDev(860 + i, 40 - i))], 5);
  assertScenario("force-bypasses-threshold", await run({ SYNC_THRESHOLD: "10", SYNC_FORCE: "true" }, "s8"), {
    status: 0, decision: "created", count: 3, posted: true,
  });

  // 场景 9：无锚点时的回退窗口——按 lookbackDays 统计并如实标注 windowKind。
  // Scenario 9: no anchor -> fallback window bounded by lookbackDays, labelled as such.
  reset([...Array.from({ length: 4 }, (_, i) => mergeToDev(870 + i, 24 - i)), mergeToDev(880, 24 * 100)], 5);
  assertScenario("no-anchor-fallback", await run({ SYNC_THRESHOLD: "3", SYNC_LOOKBACK_DAYS: "90" }, "s9"), {
    status: 0, decision: "created", count: 4, windowKind: "fallback-lookback", posted: true,
  });

  // 场景 10：没有 token 时必须失败退出，而不是静默什么都不做。
  // Scenario 10: a missing token must fail loudly instead of doing nothing quietly.
  reset([anchor], 5);
  {
    const r = await run({ GH_TOKEN: "" }, "s10");
    assertScenario("missing-token", r, { status: 1, decision: "error" });
    check("missing-token", /SYNC_PR_TOKEN/.test(r.log), "log does not tell the operator which secret to set");
  }

  // 场景 11：Actions 被组织策略禁止建 PR 的 403 必须给出可执行的提示。
  // Scenario 11: the 403 "Actions may not create PRs" must produce actionable guidance.
  reset([anchor, nonSyncAnchor, ...Array.from({ length: 12 }, (_, i) => mergeToDev(890 + i, 40 - i))], 5);
  state.failCreate = { status: 403, body: { message: "GitHub Actions is not permitted to create or approve pull requests." } };
  {
    const r = await run({ SYNC_THRESHOLD: "10" }, "s11");
    assertScenario("actions-cannot-create-pr", r, { status: 1, decision: "error" });
    check("actions-cannot-create-pr", r.result && /SYNC_PR_TOKEN/.test(String(r.result.hint)), "hint does not mention SYNC_PR_TOKEN");
    check("actions-cannot-create-pr", /SYNC_PR_TOKEN/.test(r.log), "log does not mention SYNC_PR_TOKEN");
  }

  server.close();

  console.log(`[self-test] script=${path.relative(process.cwd(), SCRIPT)}`);
  console.log(`[self-test] passed=${passed} failed=${failures.length}`);
  if (failures.length > 0) {
    for (const failure of failures) console.log(`[self-test] FAIL ${failure}`);
    console.log(`[self-test] logs kept at ${CASE_DIR}`);
    process.exitCode = 1;
  } else {
    fs.rmSync(TMP, { recursive: true, force: true });
    console.log("[self-test] ALL SCENARIOS GREEN");
  }
}

main().catch((error) => {
  console.error("[self-test] harness error:", error);
  process.exitCode = 1;
});
