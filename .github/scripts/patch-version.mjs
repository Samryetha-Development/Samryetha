#!/usr/bin/env node
// Trusted main-branch code only. Never execute code from the generated PR in a write-capable job.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const VERSION_FILES = [
  "frontend/package.json",
  "backend/src/samryetha/__init__.py",
  "backend/docs/openapi.json",
];
const BOT = "github-actions[bot]";
const BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com";
const BRANCH_PREFIX = "codex/version-bump-pr-";

export function nextPatch(version) {
  assert.equal(typeof version, "string", "Version must be a string");
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(version);
  assert(match && match[0] === version, `Expected a stable major.minor.patch version, got ${version}`);
  return `${match[1]}.${match[2]}.${BigInt(match[3]) + 1n}`;
}

export function planVersionFiles(contents) {
  const [packageText, backendText, openapiText] = VERSION_FILES.map((file) => contents[file]);
  const version = JSON.parse(packageText).version;
  const backendMatch = /^__version__ = "([^"]+)"$/m.exec(backendText);
  assert.equal(backendMatch?.[1], version, "Frontend and backend versions differ");
  assert.equal(JSON.parse(openapiText).info.version, version, "OpenAPI version differs");
  const next = nextPatch(version);
  // Replace only the version fields; preserve formatting and every other byte.
  const files = {
    [VERSION_FILES[0]]: packageText.replace(/("version"\s*:\s*")[^"]+("\s*,)/, `$1${next}$2`),
    [VERSION_FILES[1]]: backendText.replace(/^__version__ = "[^"]+"$/m, `__version__ = "${next}"`),
    [VERSION_FILES[2]]: openapiText.replace(/("info"\s*:\s*\{[\s\S]*?"version"\s*:\s*")[^"]+(")/, `$1${next}$2`),
  };
  assert.equal(JSON.parse(files[VERSION_FILES[0]]).version, next);
  assert.equal(JSON.parse(files[VERSION_FILES[2]]).info.version, next);
  return { version, next, files };
}

export function validateSource(pull, repo) {
  assert(pull.merged_at && pull.merge_commit_sha, "Source PR must already be merged");
  assert.equal(pull.base?.ref, "main", "Source PR must target main");
  assert.equal(pull.base?.repo?.full_name, repo);
  assert.equal(pull.head?.ref, "dev", "Source PR must promote dev");
  assert.equal(pull.head?.repo?.full_name, repo, "Fork promotions are not eligible");
}

function validateVersionPull(pull, repo, sourceNumber, branch, prAuthor) {
  assert.equal(pull.user?.login, prAuthor, `Version PR must belong to ${prAuthor}`);
  assert.equal(pull.base?.ref, "main");
  assert.equal(pull.base?.repo?.full_name, repo);
  assert.equal(pull.head?.ref, branch);
  assert.equal(pull.head?.repo?.full_name, repo);
  assert(pull.body?.includes(`<!-- samryetha-version-bump:${sourceNumber} -->`), "Version PR marker is missing");
}

export async function runAutomation({ mode, repo, sourceNumber, dryRun = false, runId, api, prApi = api, prAuthor = BOT, root = process.cwd(), git: gitOverride }) {
  assert(/^[\w.-]+\/[\w.-]+$/.test(repo), "Invalid repository");
  const git = gitOverride || ((args) => execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }));
  const basePath = `/repos/${repo}`;
  const readFiles = (ref) => Object.fromEntries(VERSION_FILES.map((file) => [file, git(["show", `${ref}:${file}`])]));
  const fetchBranch = (branch) => {
    git(["fetch", "--no-tags", "origin", branch]);
    return git(["rev-parse", "FETCH_HEAD"]).trim();
  };
  const requireAncestor = (ancestor, head) => git(["merge-base", "--is-ancestor", ancestor, head]);
  const validateCommit = (head, main) => {
    const parents = git(["rev-list", "--parents", "-n", "1", head]).trim().split(/\s+/);
    assert.equal(parents.length, 2, "Version branch must contain one ordinary version commit");
    const parent = parents[1];
    requireAncestor(parent, main);
    const changed = git(["diff", "--name-only", parent, head]).trim().split("\n").sort();
    assert.deepEqual(changed, [...VERSION_FILES].sort(), "Version commit changed unexpected files");
    assert.deepEqual(readFiles(head), planVersionFiles(readFiles(parent)).files, "Version commit contains unexpected edits");
    return parent;
  };

  let checkRun;
  if (mode === "merge") {
    assert(/^\d+$/.test(String(runId)), "Invalid check run ID");
    checkRun = await api(`${basePath}/actions/runs/${runId}`);
    assert.equal(checkRun.name, "PR checks");
    assert.equal(checkRun.path, ".github/workflows/pr-checks.yml");
    assert.equal(checkRun.event, "workflow_dispatch", "Only explicitly dispatched checks can merge");
    assert.equal(checkRun.status, "completed");
    assert.equal(checkRun.conclusion, "success", "Checks did not pass");
    assert.equal(checkRun.head_repository?.full_name, repo);
    const match = /^codex\/version-bump-pr-([1-9]\d*)$/.exec(checkRun.head_branch);
    assert(match, "Unexpected check branch");
    sourceNumber = match[1];
  } else {
    assert.equal(mode, "prepare");
  }
  assert(/^[1-9]\d*$/.test(String(sourceNumber)), "A source sync PR number is required");
  const source = await api(`${basePath}/pulls/${sourceNumber}`);
  validateSource(source, repo);
  const branch = `${BRANCH_PREFIX}${sourceNumber}`;
  const query = new URLSearchParams({ state: "all", base: "main", head: `${repo.split("/")[0]}:${branch}`, per_page: "100" });
  const pulls = await api(`${basePath}/pulls?${query}`);
  assert(pulls.length <= 1, "Multiple version PRs found for the same sync");
  let pull = pulls[0];
  if (pull) {
    validateVersionPull(pull, repo, sourceNumber, branch, prAuthor);
    if (pull.merged_at) return { decision: "already-merged", pr: pull.html_url };
    assert.equal(pull.state, "open", "Version PR was closed without merging; it will not be reopened automatically");
  }
  const main = fetchBranch("main");
  requireAncestor(source.merge_commit_sha, main);
  const plan = planVersionFiles(readFiles(main));

  if (mode === "merge") {
    assert(pull, "No generated version PR exists");
    const head = fetchBranch(branch);
    assert.equal(head, checkRun.head_sha, "Version branch moved after its checks");
    assert.equal(head, pull.head.sha, "Version PR moved after its checks");
    validateCommit(head, main);
    assert.deepEqual(readFiles(head), plan.files, "main changed during checks; rerun preparation to refresh the PR");
    // The merge API enforces branch rules and rejects a changed PR head (sha mismatch).
    const merged = await api(`${basePath}/pulls/${pull.number}/merge`, "PUT", {
      sha: head,
      merge_method: "squash",
      commit_title: `chore: bump version to ${plan.next} (#${pull.number})`,
    });
    assert.equal(merged.merged, true, `GitHub refused to merge: ${merged.message}`);
    return { decision: "merged", version: plan.next, pr: pull.html_url, sha: merged.sha };
  }

  if (dryRun) return { decision: "dry-run", source: sourceNumber, version: plan.next, branch, pr: pull?.html_url };
  const oldHead = git(["ls-remote", "--heads", "origin", `refs/heads/${branch}`]).trim().split(/\s+/)[0] || "";
  let head;
  if (oldHead) {
    assert.equal(fetchBranch(branch), oldHead, "Version branch moved during preparation");
    if (pull) assert.equal(pull.head.sha, oldHead, "Version PR moved during preparation");
    const parent = validateCommit(oldHead, main);
    assert.equal(git(["show", "-s", "--format=%ae", oldHead]).trim(), BOT_EMAIL, "Existing branch is not an automation commit");
    if (parent === main) head = oldHead;
  }
  if (!head) {
    git(["switch", "--detach", main]);
    for (const [file, content] of Object.entries(plan.files)) fs.writeFileSync(path.join(root, file), content);
    git(["add", "--", ...VERSION_FILES]);
    git(["-c", `user.name=${BOT}`, "-c", `user.email=${BOT_EMAIL}`, "commit", "-m", `chore: bump version to ${plan.next} after sync #${sourceNumber}`]);
    head = git(["rev-parse", "HEAD"]).trim();
    // An expected old SHA protects both initial creation and refreshes from concurrent edits.
    git(["push", `--force-with-lease=refs/heads/${branch}:${oldHead}`, "origin", `HEAD:refs/heads/${branch}`]);
  }
  const payload = {
    title: `chore: bump version to ${plan.next}`,
    body: [
      `<!-- samryetha-version-bump:${sourceNumber} -->`,
      `同步 PR #${sourceNumber} 已合入 main，版本号由 ${plan.version} 更新为 ${plan.next}。`,
      "",
      "同步更新前端、后端和 OpenAPI 版本号。显式启动完整 PR checks；全部通过后由 Action 自动合并。",
      "This PR is generated by GitHub Actions and merged only after the dispatched checks pass.",
    ].join("\n"),
  };
  if (!pull) {
    pull = await prApi(`${basePath}/pulls`, "POST", { ...payload, head: branch, base: "main" });
  } else {
    await api(`${basePath}/pulls/${pull.number}`, "PATCH", payload);
  }
  // GITHUB_TOKEN-created PR checks can require approval. Dispatch the checks on the actual head instead.
  await api(`${basePath}/actions/workflows/pr-checks.yml/dispatches`, "POST", { ref: branch });
  return { decision: "checks-dispatched", source: sourceNumber, version: plan.next, pr: pull.html_url, head };
}

async function main() {
  const token = process.env.GH_TOKEN;
  assert(token, "GH_TOKEN is required");
  const prToken = process.env.VERSION_PR_TOKEN;
  assert(prToken, "SYNC_PR_TOKEN is required to create version PRs under the current repository policy");
  const createApi = (accessToken) => async (route, method = "GET", body) => {
    const response = await fetch(`${process.env.GITHUB_API_URL || "https://api.github.com"}${route}`, {
      method,
      headers: {
        authorization: `Bearer ${accessToken}`, accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28", ...(body ? { "content-type": "application/json" } : {}),
      },
      signal: AbortSignal.timeout(30000),
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    if (!response.ok) throw new Error(`${method} ${route}: ${response.status} ${text}`);
    return text ? JSON.parse(text) : null;
  };
  const api = createApi(token);
  const prApi = createApi(prToken);
  // Resolve the configured token's identity rather than hard-coding its owner.
  // The PAT is used only for this lookup and PR creation; all other operations use GITHUB_TOKEN.
  const prAuthor = (await prApi("/user")).login;
  assert(typeof prAuthor === "string" && prAuthor.length > 0, "Cannot resolve SYNC_PR_TOKEN's owner");
  const result = await runAutomation({
    mode: process.argv[2], repo: process.env.GITHUB_REPOSITORY,
    sourceNumber: process.env.VERSION_SOURCE_PR, runId: process.env.VERSION_CHECK_RUN_ID,
    dryRun: process.env.VERSION_DRY_RUN === "true", api, prApi, prAuthor,
  });
  console.log(JSON.stringify(result, null, 2));
  if (process.env.GITHUB_STEP_SUMMARY) {
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `## Patch version\n\n${Object.entries(result).map(([key, value]) => `- ${key}: ${value}`).join("\n")}\n`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
