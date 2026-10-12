import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { nextPatch, planVersionFiles, runAutomation, validateSource, VERSION_FILES } from "./patch-version.mjs";

const REPO = "test-org/forum";
const BRANCH = "codex/version-bump-pr-105";
const original = {
  [VERSION_FILES[0]]: '{\n  "name": "forum",\n  "version": "0.2.1",\n  "dependencies": { "example": "1.0.0" }\n}\n',
  [VERSION_FILES[1]]: '"""Backend."""\n\n__version__ = "0.2.1"\n',
  [VERSION_FILES[2]]: '{\n  "openapi": "3.1.0",\n  "info": {\n    "title": "Forum",\n    "version": "0.2.1"\n  },\n  "paths": {}\n}\n',
};

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "patch-version-test-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const remote = path.join(dir, "remote.git");
  const root = path.join(dir, "checkout");
  fs.mkdirSync(root);
  const exec = (cwd, args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  const git = (args) => exec(root, args);
  exec(dir, ["init", "--bare", remote]);
  git(["init", "-b", "main"]);
  git(["config", "user.name", "Tester"]);
  git(["config", "user.email", "test@example.invalid"]);
  const write = (files) => {
    for (const [file, content] of Object.entries(files)) {
      fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
      fs.writeFileSync(path.join(root, file), content);
    }
  };
  write({ ...original, "README.md": "Forum\n" });
  git(["add", "."]);
  git(["commit", "-m", "Sync dev to main"]);
  const main = git(["rev-parse", "HEAD"]).trim();
  git(["remote", "add", "origin", remote]);
  git(["push", "origin", "main"]);
  const state = {
    calls: [], pulls: [], failDispatch: false, failCreate: false, mergeResult: { merged: true, sha: "merged-sha" },
    source: {
      number: 105, merged_at: "2026-10-11T08:58:11Z", merge_commit_sha: main,
      base: { ref: "main", repo: { full_name: REPO } },
      head: { ref: "dev", repo: { full_name: REPO } },
    },
  };
  const api = async (route, method = "GET", body) => {
    state.calls.push({ route, method, body });
    if (route.endsWith("/actions/runs/42")) return state.run;
    if (route.endsWith("/pulls/105")) return state.source;
    if (route.includes("/pulls?")) {
      for (const pull of state.pulls) {
        pull.head.sha = exec(dir, ["--git-dir", remote, "rev-parse", `refs/heads/${BRANCH}`]).trim();
      }
      return structuredClone(state.pulls);
    }
    if (method === "POST" && route.endsWith("/pulls")) {
      if (state.failCreate) throw new Error("PR creation unavailable");
      const pull = {
        ...body, number: 200, state: "open", merged_at: null, html_url: "https://example.invalid/pull/200",
        user: { login: "github-actions[bot]" },
        base: { ref: "main", repo: { full_name: REPO } },
        head: { ref: BRANCH, sha: git(["rev-parse", "HEAD"]).trim(), repo: { full_name: REPO } },
      };
      state.pulls.push(pull);
      return structuredClone(pull);
    }
    if (method === "PATCH" && route.endsWith("/pulls/200")) {
      Object.assign(state.pulls[0], body);
      return state.pulls[0];
    }
    if (method === "POST" && route.endsWith("/dispatches")) {
      if (state.failDispatch) throw new Error("Dispatch unavailable");
      return null;
    }
    if (method === "PUT" && route.endsWith("/pulls/200/merge")) return state.mergeResult;
    throw new Error(`Unexpected API call: ${method} ${route}`);
  };
  const options = { repo: REPO, sourceNumber: "105", api, root, git };
  const prepare = (extra = {}) => runAutomation({ ...options, mode: "prepare", ...extra });
  const check = () => {
    state.run = {
      name: "PR checks", path: ".github/workflows/pr-checks.yml", event: "workflow_dispatch",
      status: "completed", conclusion: "success", head_repository: { full_name: REPO },
      head_branch: BRANCH, head_sha: git(["rev-parse", "HEAD"]).trim(),
    };
  };
  const merge = () => runAutomation({ ...options, mode: "merge", runId: "42" });
  return { state, git, write, prepare, check, merge, root, main };
}

test("increment only patch, including rollover and large values", () => {
  assert.equal(nextPatch("0.2.1"), "0.2.2");
  assert.equal(nextPatch("1.9.9"), "1.9.10");
  assert.equal(nextPatch("1.2.9007199254740993"), "1.2.9007199254740994");
  for (const invalid of ["1.2", "01.2.3", "1.2.3-rc.1", "v1.2.3", "1.2.3\n"]) {
    assert.throws(() => nextPatch(invalid));
  }
});

test("change exactly the three version fields and reject drift", () => {
  const plan = planVersionFiles(original);
  assert.equal(plan.next, "0.2.2");
  for (const file of VERSION_FILES) assert.equal(plan.files[file], original[file].replace("0.2.1", "0.2.2"));
  assert.throws(() => planVersionFiles({ ...original, [VERSION_FILES[1]]: '__version__ = "0.2.0"\n' }));
  assert.throws(() => planVersionFiles({ ...original, [VERSION_FILES[2]]: original[VERSION_FILES[2]].replace("0.2.1", "0.2.0") }));
});

test("reject unmerged, unrelated and fork source PRs", () => {
  const valid = { merged_at: "now", merge_commit_sha: "sha", base: { ref: "main", repo: { full_name: REPO } }, head: { ref: "dev", repo: { full_name: REPO } } };
  validateSource(valid, REPO);
  for (const change of [
    { merged_at: null }, { base: { ...valid.base, ref: "dev" } },
    { head: { ...valid.head, ref: "feature" } },
    { head: { ...valid.head, repo: { full_name: "fork/forum" } } },
  ]) assert.throws(() => validateSource({ ...valid, ...change }, REPO));
});

test("dry run makes no branch, commit, PR, or dispatch", async (t) => {
  const f = fixture(t);
  const result = await f.prepare({ dryRun: true });
  assert.equal(result.decision, "dry-run");
  assert.equal(result.version, "0.2.2");
  assert.equal(f.git(["rev-parse", "HEAD"]).trim(), f.main);
  assert.equal(f.git(["ls-remote", "--heads", "origin", BRANCH]), "");
  assert(f.state.calls.every((call) => call.method === "GET"));
});

test("prepare opens one bot PR and explicitly dispatches checks on its branch", async (t) => {
  const f = fixture(t);
  const result = await f.prepare();
  assert.equal(result.decision, "checks-dispatched");
  assert.equal(f.state.pulls.length, 1);
  assert.equal(f.state.pulls[0].base.ref, "main");
  assert.equal(f.state.pulls[0].head.ref, BRANCH);
  assert.match(f.state.pulls[0].body, /samryetha-version-bump:105/);
  assert.deepEqual(f.state.calls.at(-1).body, { ref: BRANCH });
  assert.equal(f.git(["show", "-s", "--format=%ae", "HEAD"]).trim(), "41898282+github-actions[bot]@users.noreply.github.com");
});

test("rerun reuses the same PR and exact commit", async (t) => {
  const f = fixture(t);
  const first = await f.prepare();
  const second = await f.prepare();
  assert.equal(first.head, second.head);
  assert.equal(f.state.calls.filter((call) => call.method === "POST" && call.route.endsWith("/pulls")).length, 1);
});

test("retry resumes after dispatch failure without another increment", async (t) => {
  const f = fixture(t);
  f.state.failDispatch = true;
  await assert.rejects(f.prepare(), /Dispatch unavailable/);
  const head = f.git(["rev-parse", "HEAD"]).trim();
  f.state.failDispatch = false;
  assert.equal((await f.prepare()).head, head);
  assert.equal(f.state.pulls.length, 1);
});

test("retry resumes after branch push but failed PR creation", async (t) => {
  const f = fixture(t);
  f.state.failCreate = true;
  await assert.rejects(f.prepare(), /PR creation unavailable/);
  const head = f.git(["rev-parse", "HEAD"]).trim();
  f.state.failCreate = false;
  assert.equal((await f.prepare()).head, head);
  assert.equal(f.state.pulls.length, 1);
});

test("merged source is processed once and closed version PRs stay closed", async (t) => {
  const f = fixture(t);
  await f.prepare();
  f.state.pulls[0].merged_at = "now";
  assert.equal((await f.prepare()).decision, "already-merged");
  f.state.pulls[0].merged_at = null;
  f.state.pulls[0].state = "closed";
  await assert.rejects(f.prepare(), /will not be reopened/);
});

test("successful checks merge only the verified PR head", async (t) => {
  const f = fixture(t);
  const prepared = await f.prepare();
  f.check();
  const result = await f.merge();
  assert.equal(result.decision, "merged");
  assert.equal(result.version, "0.2.2");
  assert.equal(f.state.calls.at(-1).body.sha, prepared.head);
  assert.equal(f.state.calls.at(-1).method, "PUT");
});

for (const [label, changedRun] of [
  ["failed", { conclusion: "failure" }], ["pending", { status: "in_progress" }],
  ["ordinary PR", { event: "pull_request" }], ["wrong workflow", { path: ".github/workflows/other.yml" }],
  ["wrong repository", { head_repository: { full_name: "fork/forum" } }],
  ["unrelated branch", { head_branch: "dev" }], ["stale SHA", { head_sha: "0".repeat(40) }],
]) {
  test(`${label} checks cannot merge`, async (t) => {
    const f = fixture(t);
    await f.prepare();
    f.check();
    Object.assign(f.state.run, changedRun);
    await assert.rejects(f.merge());
    assert(!f.state.calls.some((call) => call.method === "PUT"));
  });
}

test("unexpected content or branch commits cannot merge", async (t) => {
  const f = fixture(t);
  await f.prepare();
  f.write({ "README.md": "Injected change\n" });
  f.git(["add", "."]);
  f.git(["commit", "--amend", "--no-edit"]);
  f.git(["push", "--force", "origin", `HEAD:${BRANCH}`]);
  f.check();
  await assert.rejects(f.merge(), /unexpected files/);
  assert(!f.state.calls.some((call) => call.method === "PUT"));
});

test("non-version edits inside an allowed file cannot merge", async (t) => {
  const f = fixture(t);
  await f.prepare();
  const packageFile = VERSION_FILES[0];
  f.write({ [packageFile]: fs.readFileSync(path.join(f.root, packageFile), "utf8").replace('"example": "1.0.0"', '"example": "2.0.0"') });
  f.git(["add", "."]);
  f.git(["commit", "--amend", "--no-edit"]);
  f.git(["push", "--force", "origin", `HEAD:${BRANCH}`]);
  f.check();
  await assert.rejects(f.merge(), /unexpected edits/);
  assert(!f.state.calls.some((call) => call.method === "PUT"));
});

test("a changed main version blocks merge; preparation refreshes with a lease", async (t) => {
  const f = fixture(t);
  await f.prepare();
  f.check();
  const checkedHead = f.state.run.head_sha;
  f.git(["switch", "main"]);
  f.write(planVersionFiles(original).files);
  f.git(["add", "."]);
  f.git(["commit", "-m", "Another version update"]);
  f.git(["push", "origin", "main"]);
  await assert.rejects(f.merge(), /main changed during checks/);
  const refreshed = await f.prepare();
  assert.equal(refreshed.version, "0.2.3");
  assert.notEqual(refreshed.head, checkedHead);
  await assert.rejects(f.merge(), /moved after its checks/);
  f.check();
  assert.equal((await f.merge()).version, "0.2.3");
});

test("human PRs and missing markers cannot be auto merged", async (t) => {
  const f = fixture(t);
  await f.prepare();
  f.check();
  f.state.pulls[0].user.login = "someone";
  await assert.rejects(f.merge(), /github-actions/);
  f.state.pulls[0].user.login = "github-actions[bot]";
  f.state.pulls[0].body = "No marker";
  await assert.rejects(f.merge(), /marker/);
});

test("branch protection refusal remains a failure", async (t) => {
  const f = fixture(t);
  await f.prepare();
  f.check();
  f.state.mergeResult = { merged: false, message: "Required review missing" };
  await assert.rejects(f.merge(), /Required review missing/);
});
