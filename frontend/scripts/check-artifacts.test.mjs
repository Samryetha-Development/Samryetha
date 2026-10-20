import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { checkArtifacts, checkDependencies } from "./check-artifacts.mjs";

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "samryetha-artifacts-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  async function put(name, content) {
    const file = path.join(root, name);
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(file, content);
  }
  await put("package.json", '{"type":"module"}');
  await put("dist/client/index.html", '<script type="module" src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css">');
  await put("dist/client/assets/app.js", 'import "./chunk.js"; export const message = "from @lako/ui";');
  await put("dist/client/assets/chunk.js", "export const ready = true;");
  await put("dist/client/assets/app.css", '@font-face{src:url("./font.woff2")}');
  await put("dist/client/assets/font.woff2", "fixture-font");
  await put("dist/client/.vite/manifest.json", JSON.stringify({
    "index.html": { file: "assets/app.js", isEntry: true, css: ["assets/app.css"], dynamicImports: ["chunk"] },
    "chunk": { file: "assets/chunk.js", assets: ["assets/font.woff2"] },
  }));
  await put("dist/server/entry-server.js", 'import "node:path"; export const render = () => "ok";');
  await put("node_modules/@lako/ui/package.json", JSON.stringify({ exports: { ".": { types: "./dist/index.d.ts", import: "./dist/index.js" } } }));
  await put("node_modules/@lako/ui/dist/index.js", "export const ready = true;");
  await put("node_modules/@lako/ui/dist/index.d.ts", "export declare const ready: true;");
  return { root, put };
}

test("complete artifacts and declared dependency exports pass", async (t) => {
  const { root } = await fixture(t);
  await checkDependencies(root);
  assert.equal(await checkArtifacts(root), 6);
});

test("missing Git package dist fails before bundling", async (t) => {
  const { root } = await fixture(t);
  await fs.rm(path.join(root, "node_modules/@lako/ui/dist/index.js"));
  await assert.rejects(checkDependencies(root), /Missing or empty artifact/);
});

for (const code of ['import { Login } from "@lako/ui";', 'import("@lako/ui");']) {
  test(`bare browser import fails: ${code}`, async (t) => {
    const { root, put } = await fixture(t);
    await put("dist/client/assets/app.js", code);
    await assert.rejects(checkArtifacts(root), /Unresolved browser module.*@lako\/ui/);
  });
}

for (const missing of ["assets/chunk.js", "assets/font.woff2", "assets/app.css"]) {
  test(`missing referenced artifact fails: ${missing}`, async (t) => {
    const { root } = await fixture(t);
    await fs.rm(path.join(root, "dist/client", missing));
    await assert.rejects(checkArtifacts(root), /Missing or empty artifact/);
  });
}

test("missing external SSR dependency fails", async (t) => {
  const { root, put } = await fixture(t);
  await put("dist/server/entry-server.js", 'import "samryetha-missing-ssr-package";');
  await assert.rejects(checkArtifacts(root), /Cannot find module/);
});

test("artifact traversal outside client directory fails", async (t) => {
  const { root, put } = await fixture(t);
  await put("dist/client/assets/app.js", 'import "../../../package.json";');
  await assert.rejects(checkArtifacts(root), /escapes output directory/);
});

test("broken manifest graph fails even when JavaScript references are absent", async (t) => {
  const { root, put } = await fixture(t);
  await put("dist/client/.vite/manifest.json", JSON.stringify({
    "index.html": { file: "assets/app.js", isEntry: true, dynamicImports: ["missing-chunk"] },
  }));
  await assert.rejects(checkArtifacts(root), /Missing manifest dependency/);
});
