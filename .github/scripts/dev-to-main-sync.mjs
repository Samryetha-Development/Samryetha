#!/usr/bin/env node
// dev -> main 自动同步 PR 的决策脚本：统计 dev 上自上次同步以来合入的 PR 数，达阈值则开同步 PR。
// Decision script for the dev -> main auto sync PR: count PRs merged into dev since the
// last sync and open a promotion PR once the threshold is reached.
//
// 设计要点 / Design notes:
// - 无状态：不写任何计数器文件，每次触发都用 GitHub API 现算，避免冲突与漂移。
//   Stateless: no counter file is written; every run recomputes from the GitHub API.
// - 计数口径：base=<source> 且 merged=true，merged_at 晚于「最近一次已合并的 <source> -> <target> PR」。
//   Counting rule: base=<source> and merged=true, with merged_at later than the last merged sync PR.
// - 纯 Node 内置模块，零第三方依赖，CI 里无需 pnpm install。
//   Node builtins only, zero third-party dependencies, no pnpm install needed in CI.

import fs from "node:fs";
import process from "node:process";

// 把常见的真值写法统一解析成布尔。
// Normalize the common truthy spellings into a boolean.
function isTrue(value) {
  return ["1", "true", "yes", "on"].includes(String(value || "").trim().toLowerCase());
}

// 读取整数型环境变量，非法值直接报错而不是静默取默认。
// Read an integer environment variable; fail loudly instead of silently defaulting.
function readInt(name, fallback, min) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || Math.floor(value) !== value || value < min) {
    throw new Error(`invalid ${name}: ${JSON.stringify(raw)} (expected integer >= ${min})`);
  }
  return value;
}

const CONFIG = {
  // 允许指向本地 stub 服务，供离线测试使用；线上保持 api.github.com。
  // Overridable so tests can point at a local stub; production keeps api.github.com.
  apiBase: (process.env.SYNC_API_BASE || "https://api.github.com").replace(/\/+$/, ""),
  token: process.env.GH_TOKEN || process.env.SYNC_TOKEN || "",
  repo: process.env.GITHUB_REPOSITORY || process.env.SYNC_REPO || "Samryetha-Development/Samryetha",
  sourceBranch: process.env.SYNC_SOURCE_BRANCH || "dev",
  targetBranch: process.env.SYNC_TARGET_BRANCH || "main",
  threshold: readInt("SYNC_THRESHOLD", 10, 1),
  dryRun: isTrue(process.env.SYNC_DRY_RUN),
  // force 只用于 workflow_dispatch 的人工演练/人工强制同步，绕过阈值与冷却期两个软闸门。
  // force is only for manual workflow_dispatch runs; it bypasses the two soft gates only.
  force: isTrue(process.env.SYNC_FORCE),
  cooldownHours: readInt("SYNC_COOLDOWN_HOURS", 24, 0),
  lookbackDays: readInt("SYNC_LOOKBACK_DAYS", 90, 1),
  maxPages: readInt("SYNC_MAX_PAGES", 10, 1),
  resultFile: process.env.SYNC_RESULT_FILE || "",
  stepSummary: process.env.GITHUB_STEP_SUMMARY || "",
  outputFile: process.env.GITHUB_OUTPUT || "",
  serverUrl: process.env.GITHUB_SERVER_URL || "https://github.com",
  runUrl:
    process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
      ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
      : "",
};

// 单次 REST 调用；非 2xx 时抛出带状态码与响应体的错误，便于上层识别 403/422 等具体原因。
// One REST call; throws with status and body on non-2xx so callers can classify 403/422.
async function api(path, init = {}) {
  const url = path.startsWith("http") ? path : `${CONFIG.apiBase}${path}`;
  const response = await fetch(url, {
    ...init,
    headers: {
      accept: "application/vnd.github+json",
      "user-agent": "samryetha-dev-to-main-sync",
      "x-github-api-version": "2022-11-28",
      ...(CONFIG.token ? { authorization: `token ${CONFIG.token}` } : {}),
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    const error = new Error(`GET ${url} -> ${response.status} ${typeof body === "string" ? body : JSON.stringify(body)}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

// 分页拉取 PR 列表；以「本页不足 per_page」作为翻页终止条件。
// Paginate a PR list; stop when a page returns fewer than per_page items.
async function listPulls(params) {
  const pulls = [];
  for (let page = 1; page <= CONFIG.maxPages; page += 1) {
    const query = new URLSearchParams({ per_page: "100", page: String(page), ...params });
    const batch = await api(`/repos/${CONFIG.repo}/pulls?${query.toString()}`);
    if (!Array.isArray(batch)) throw new Error("unexpected pulls payload");
    pulls.push(...batch);
    if (batch.length < 100) break;
  }
  return pulls;
}

// 判断一个 PR 是否属于「<source> -> <target> 的同步 PR」。
// Decide whether a PR is a sync PR from <source> into <target>.
function isSyncPr(pull) {
  return pull.base?.ref === CONFIG.targetBranch && pull.head?.ref === CONFIG.sourceBranch;
}

// 锚点：最近一次已合并的同步 PR。计数窗口从它的 merged_at 之后开始。
// Anchor: the newest merged sync PR; the counting window starts right after its merged_at.
async function findAnchor() {
  const pulls = await listPulls({ state: "closed", base: CONFIG.targetBranch, sort: "updated", direction: "desc" });
  let anchor = null;
  for (const pull of pulls) {
    if (!isSyncPr(pull) || !pull.merged_at) continue;
    if (!anchor || pull.merged_at > anchor.mergedAt) {
      anchor = { mergedAt: pull.merged_at, number: pull.number };
    }
  }
  return anchor;
}

// 统计窗口内合入源分支的 PR；无锚点时退化为「最近 lookbackDays 天」，并如实标注。
// Count PRs merged into the source branch inside the window; without an anchor fall back to
// the last lookbackDays days and label that fallback explicitly.
async function countSourceMerges(anchor, nowMs) {
  const pulls = await listPulls({ state: "closed", base: CONFIG.sourceBranch, sort: "updated", direction: "desc" });
  const merged = pulls.filter((pull) => pull.merged_at);
  let windowStart;
  let windowKind;
  if (anchor) {
    windowStart = anchor.mergedAt;
    windowKind = "since-last-sync-pr";
  } else {
    windowStart = new Date(nowMs - CONFIG.lookbackDays * 86400000).toISOString();
    windowKind = "fallback-lookback";
  }
  const counted = merged
    .filter((pull) => pull.merged_at > windowStart)
    .sort((a, b) => (a.merged_at < b.merged_at ? -1 : 1))
    .map((pull) => ({ number: pull.number, title: pull.title, mergedAt: pull.merged_at }));
  return { counted, windowStart, windowKind };
}

// 已开的同步 PR（硬闸门：同一时间只允许存在一个，避免并发触发造成重复 PR）。
// Open sync PR (hard gate: at most one outstanding, so concurrent triggers cannot duplicate).
async function findOpenSyncPr() {
  const pulls = await listPulls({ state: "open", base: CONFIG.targetBranch, sort: "created", direction: "desc" });
  const open = pulls.filter(isSyncPr);
  return open.length > 0 ? { number: open[0].number, url: open[0].html_url } : null;
}

// 冷却期：最近被关闭但未合并的同步 PR，说明管理员刚拒绝过一次，短时间内不再重复打扰。
// Cooldown: a sync PR closed without merging means it was just rejected; stay quiet for a while.
async function findRecentRejectedSyncPr(nowMs) {
  if (CONFIG.cooldownHours <= 0) return null;
  const pulls = await listPulls({ state: "closed", base: CONFIG.targetBranch, sort: "updated", direction: "desc" });
  const cutoff = nowMs - CONFIG.cooldownHours * 3600000;
  const rejected = pulls
    .filter((pull) => isSyncPr(pull) && !pull.merged_at && pull.closed_at)
    .filter((pull) => new Date(pull.closed_at).getTime() > cutoff)
    .sort((a, b) => (a.closed_at < b.closed_at ? 1 : -1));
  return rejected.length > 0 ? { number: rejected[0].number, closedAt: rejected[0].closed_at } : null;
}

// 源分支相对目标分支的领先提交数；为 0 表示两边已无差异，此时开 PR 必然 422。
// Commits the source branch is ahead of the target; 0 means no diff and the PR API would 422.
async function compareBranches() {
  const data = await api(`/repos/${CONFIG.repo}/compare/${CONFIG.targetBranch}...${CONFIG.sourceBranch}`);
  return { aheadBy: data.ahead_by, behindBy: data.behind_by, totalCommits: data.total_commits };
}

// 渲染 PR 正文：列出本次同步覆盖的 PR、口径与触发来源，方便评审时核对。
// Render the PR body: covered PRs, counting rule and trigger source, for reviewer verification.
function renderBody(decision, facts) {
  const lines = [];
  lines.push(`本次由 GitHub Actions 自动发起，把 \`${CONFIG.sourceBranch}\` 上已积累的改动提升到 \`${CONFIG.targetBranch}\`。`);
  lines.push("");
  lines.push("This promotion PR is opened automatically by GitHub Actions.");
  lines.push("");
  lines.push("## 触发与口径 / Trigger and counting rule");
  lines.push("");
  lines.push(`- 阈值 threshold: **${CONFIG.threshold}**`);
  lines.push(
    `- 自上次同步（${facts.anchor ? `PR #${facts.anchor.number} @ ${facts.anchor.mergedAt}` : "无锚点，使用回退窗口"}）以来合入 \`${CONFIG.sourceBranch}\` 的 PR 数: **${facts.count}**`,
  );
  lines.push(`- 窗口窗口类型 window kind: \`${facts.windowKind}\``);
  lines.push(`- 当前 \`${CONFIG.sourceBranch}\` 领先 \`${CONFIG.targetBranch}\` 的提交数: **${facts.aheadBy}**`);
  if (decision.trigger) lines.push(`- 触发来源 trigger: ${decision.trigger}`);
  if (CONFIG.runUrl) lines.push(`- 运行记录 run: ${CONFIG.runUrl}`);
  lines.push("");
  lines.push(`## 覆盖的 PR / PRs included (${facts.counted.length})`);
  lines.push("");
  if (facts.counted.length === 0) {
    lines.push("- (无 / none)");
  } else {
    for (const pull of facts.counted) {
      lines.push(`- #${pull.number} ${pull.title} — merged ${pull.mergedAt}`);
    }
  }
  lines.push("");
  lines.push("## 说明 / Notes");
  lines.push("");
  lines.push("- 本 PR 由自动化流程创建，合并前请按仓库惯例完成评审与检查。");
  lines.push("- This PR is machine-generated; review and checks still apply before merging.");
  return lines.join("\n");
}

// 写机器可读结果，供本地测试与后续步骤读取。
// Write a machine-readable result for local tests and downstream steps.
function writeResult(result) {
  const json = `${JSON.stringify(result, null, 2)}\n`;
  if (CONFIG.resultFile) fs.writeFileSync(CONFIG.resultFile, json, "utf8");
  if (CONFIG.outputFile) {
    fs.appendFileSync(
      CONFIG.outputFile,
      `decision=${result.decision}\ncount=${result.count}\npr_number=${result.pull?.number || ""}\npr_url=${result.pull?.url || ""}\n`,
      "utf8",
    );
  }
}

// 把结论写进 GitHub Step Summary，让 workflow 页面直接可读。
// Write the conclusion into the GitHub step summary so the run page is self-explanatory.
function writeSummary(result) {
  if (!CONFIG.stepSummary) return;
  const lines = [
    "## dev -> main sync decision",
    "",
    `- decision: \`${result.decision}\``,
    `- reason: ${result.reason}`,
    `- threshold: ${result.threshold}`,
    `- merged PRs in window: ${result.count}`,
    `- dry run: ${result.dryRun}`,
  ];
  if (result.pull) lines.push(`- pull request: ${result.pull.url}`);
  if (result.error) lines.push(`- error: ${result.error}`);
  fs.appendFileSync(CONFIG.stepSummary, `${lines.join("\n")}\n`, "utf8");
}

// 创建同步 PR；并把「Actions 无权限建 PR」这一常见 403 翻译成可执行的提示。
// Create the sync PR; translate the common 403 "Actions may not create PRs" into actionable advice.
async function createSyncPr(facts, trigger) {
  const payload = {
    title: `chore: sync ${CONFIG.sourceBranch} to ${CONFIG.targetBranch} (auto, ${facts.count} merged PRs)`,
    head: CONFIG.sourceBranch,
    base: CONFIG.targetBranch,
    body: renderBody({ trigger }, facts),
  };
  const created = await api(`/repos/${CONFIG.repo}/pulls`, { method: "POST", body: JSON.stringify(payload) });
  return { number: created.number, url: created.html_url };
}

async function main() {
  const startedAt = new Date().toISOString();
  const result = {
    startedAt,
    repo: CONFIG.repo,
    threshold: CONFIG.threshold,
    dryRun: CONFIG.dryRun,
    force: CONFIG.force,
    decision: "error",
    reason: "",
    count: 0,
    counted: [],
    windowKind: null,
    windowStart: null,
    anchor: null,
    aheadBy: null,
    pull: null,
  };

  if (!CONFIG.token) {
    result.reason = "GH_TOKEN is empty; set secrets.SYNC_PR_TOKEN or rely on secrets.GITHUB_TOKEN";
    writeResult(result);
    writeSummary(result);
    console.error(`[dev-to-main-sync] FAILED: ${result.reason}`);
    process.exitCode = 1;
    return;
  }

  const nowMs = Date.now();
  const trigger = process.env.SYNC_TRIGGER || `event=${process.env.GITHUB_EVENT_NAME || "local"}`;

  const anchor = await findAnchor();
  const counted = await countSourceMerges(anchor, nowMs);
  const openSyncPr = await findOpenSyncPr();
  const compare = await compareBranches();

  result.anchor = anchor;
  result.count = counted.counted.length;
  result.counted = counted.counted;
  result.windowKind = counted.windowKind;
  result.windowStart = counted.windowStart;
  result.aheadBy = compare.aheadBy;

  const facts = {
    anchor,
    count: result.count,
    counted: counted.counted,
    windowKind: counted.windowKind,
    aheadBy: compare.aheadBy,
  };

  const finish = (decision, reason) => {
    result.decision = decision;
    result.reason = reason;
    writeResult(result);
    writeSummary(result);
    console.log(`[dev-to-main-sync] decision=${decision} reason=${reason} count=${result.count} threshold=${CONFIG.threshold}`);
    console.log(`[dev-to-main-sync] included=${JSON.stringify(counted.counted.map((p) => p.number))}`);
    if (result.pull) console.log(`[dev-to-main-sync] pull=${result.pull.url}`);
  };

  // 硬闸门一：已有未合并的同步 PR，直接跳过（并发触发与重复派发都靠它收敛）。
  // Hard gate 1: an open sync PR already exists, so skip; this is what makes reruns idempotent.
  if (openSyncPr) {
    result.pull = openSyncPr;
    finish("skip", `open-sync-pr-exists:#${openSyncPr.number}`);
    return;
  }
  // 软闸门一：未达阈值。
  // Soft gate 1: below threshold.
  if (result.count < CONFIG.threshold && !CONFIG.force) {
    finish("skip", `below-threshold:${result.count}/${CONFIG.threshold}`);
    return;
  }
  // 硬闸门二：两边无差异，此时开 PR 只会拿到 422。
  // Hard gate 2: no diff between branches; opening a PR would only yield 422.
  if (compare.aheadBy === 0) {
    finish("skip", "no-diff-between-branches");
    return;
  }
  // 软闸门二：冷却期，避免管理员刚拒绝就被立刻再次打扰。
  // Soft gate 2: cooldown, so a just-rejected sync is not immediately re-proposed.
  const rejected = await findRecentRejectedSyncPr(nowMs);
  if (rejected && !CONFIG.force) {
    finish("skip", `cooldown-after-rejected:#${rejected.number}@${rejected.closedAt}`);
    return;
  }

  if (CONFIG.dryRun) {
    finish("would-create", `threshold-met:${result.count}/${CONFIG.threshold}${CONFIG.force ? " (force)" : ""}`);
    return;
  }

  result.pull = await createSyncPr(facts, trigger);
  finish("created", `threshold-met:${result.count}/${CONFIG.threshold}${CONFIG.force ? " (force)" : ""}`);
}

main().catch((error) => {
  // 让失败可诊断：既打印错误，也把结构化结果落盘。
  // Keep failures diagnosable: print the error and persist the structured result.
  const message = error && error.message ? error.message : String(error);
  const hint =
    error && error.status === 403 && /not permitted to create or approve pull requests/i.test(message)
      ? "org/repo policy blocks GITHUB_TOKEN from creating PRs; set repository secret SYNC_PR_TOKEN (PAT with repo scope / pull_requests:write)"
      : "";
  console.error(`[dev-to-main-sync] ERROR: ${message}`);
  if (hint) console.error(`[dev-to-main-sync] HINT: ${hint}`);
  const result = {
    startedAt: new Date().toISOString(),
    repo: CONFIG.repo,
    threshold: CONFIG.threshold,
    dryRun: CONFIG.dryRun,
    decision: "error",
    reason: message,
    hint,
    error: true,
  };
  writeResult(result);
  writeSummary(result);
  process.exitCode = 1;
});
