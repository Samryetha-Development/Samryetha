#!/usr/bin/env node
// 只报告、不决策的两种事件：PR 被关闭但未合并，以及来自 fork 的 PR。
// Report-only handling for two events: a PR closed without merging, and a fork PR.
//
// 为什么要把这几行 shell 从 workflow 里搬到脚本文件：
// Why this logic lives in a script file instead of inline shell in the workflow:
//   workflow 的 `run:` 若是未加引号的 YAML 纯量，值里的 " #" 会被 YAML 当成注释起点而被截断。
//   When a `run:` value is an unquoted YAML plain scalar, a " #" inside it starts a YAML comment
//   and silently truncates the value. 2026-10-10 的首次真实运行就是这样把
//   `echo "PR #103 came from a fork; ..."` 截断成 `echo "PR`，导致 bash 报未闭合引号并以 exit 2 失败。
//   That truncated the command to `echo "PR`, so bash aborted with an unmatched quote and exit 2.
//   把负载放进 .mjs 后，workflow 里的 `run:` 只剩一条无特殊字符的命令，该类截断不可能再发生。
//   Moving the payload into this file leaves only a plain command in `run:`, which cannot be truncated.
//
// 幂等语义：本脚本只打印与写摘要，绝不创建 PR、绝不改仓库状态。
// Idempotency: this script only prints and writes a step summary; it never creates a PR.

import fs from "node:fs";
import process from "node:process";

// 事件上下文由 workflow 通过环境变量传入。
// Event context is passed in by the workflow through environment variables.
const prNumber = process.env.SYNC_PR_NUMBER || "?";
const merged = String(process.env.SYNC_PR_MERGED || "").trim() === "true";
const headRepo = process.env.SYNC_PR_HEAD_REPO || "";
const baseRepo = process.env.SYNC_REPO || "";
const sourceBranch = process.env.SYNC_SOURCE_BRANCH || "dev";
const targetBranch = process.env.SYNC_TARGET_BRANCH || "main";
const stepSummary = process.env.GITHUB_STEP_SUMMARY || "";

// 三种情况的措辞：未合并关闭 / fork PR 委派 / 不该走到这里。
// Three cases: closed without merge, fork PR delegated, or an unexpected arrival.
let title;
let lines;
if (!merged) {
  title = "No promotion needed: PR closed without merging";
  lines = [
    `PR #${prNumber} 被关闭但**没有合并**，因此没有新的改动进入 \`${sourceBranch}\`，无需任何决策。`,
    "",
    `PR #${prNumber} was closed without merging, so nothing new landed on \`${sourceBranch}\`.`,
  ];
} else if (headRepo && headRepo !== baseRepo) {
  title = "Fork PR: decision delegated to the push run";
  lines = [
    `本次事件来自 fork \`${headRepo}\` 的 PR #${prNumber}。`,
    "",
    "GitHub 不会把仓库 secret 交给从 fork 触发的工作流，因此这条 `pull_request` 运行拿不到有写权限的凭据；",
    `真正的评估已交由同一次合并产生的 \`push\` 运行完成（那样才会真正判断是否发起 \`${sourceBranch}\` -> \`${targetBranch}\` 的 PR）。`,
    "",
    "本步骤**只报告、不决策**，不会创建任何 PR。",
    "",
    `This event came from fork \`${headRepo}\` (PR #${prNumber}). Secrets are not passed to workflows`,
    "triggered from a fork, so this run has no write-capable token; the evaluation is performed by the",
    "`push` run created by the same merge. This step is report-only and creates no pull request.",
  ];
} else {
  title = "Report-only step reached with an unexpected event";
  lines = [
    `PR #${prNumber} 看起来是同仓库的已合并 PR，本应由决策步骤处理。`,
    "",
    "This report-only step received an event that the decision step should have handled;",
    "check the step conditions in the workflow.",
  ];
}

// 用 GitHub 的 notice 注解输出，既不失败也不污染错误统计。
// Emit a GitHub notice annotation: visible in the UI without failing the job.
console.log(`::notice::${title} - ${lines[0]}`);

// 同时写进 Step Summary，便于在 Actions 页面直接读到结论。
// Also append to the step summary so the run page shows the conclusion.
if (stepSummary) {
  fs.appendFileSync(stepSummary, `### ${title}\n\n${lines.join("\n")}\n\n`, "utf8");
}

// 明确写出退出码：本步骤必须成功退出，否则每次从 fork 合并进 dev 都会留下误导性的红叉。
// State the exit code explicitly: this step must succeed, otherwise every fork merge leaves a red X.
process.exitCode = 0;
