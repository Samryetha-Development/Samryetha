import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { init, parse } from "es-module-lexer";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function requireFile(file) {
  const stat = await fs.stat(file).catch(() => null);
  if (!stat?.isFile() || stat.size === 0) {
    throw new Error(`Missing or empty artifact: ${file}`);
  }
}

function exportTargets(value) {
  if (typeof value === "string") return [value];
  if (!value || typeof value !== "object") return [];
  return Object.values(value).flatMap(exportTargets);
}

// Git packages can install successfully without the dist files their exports declare.
export async function checkDependencies(root = frontendRoot) {
  const packageRoot = path.join(root, "node_modules", "@lako", "ui");
  const metadata = JSON.parse(await fs.readFile(path.join(packageRoot, "package.json"), "utf8"));
  const targets = new Set([
    metadata.main,
    metadata.types,
    ...exportTargets(metadata.exports),
  ].filter(Boolean));
  if (targets.size === 0) throw new Error("@lako/ui has no declared entry points");
  for (const target of targets) {
    await requireFile(path.resolve(packageRoot, target));
  }
}

async function walk(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = await Promise.all(entries.map((entry) => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  }));
  return files.flat();
}

async function checkReference(root, source, reference) {
  if (/^(?:data:|https?:|\/\/|#)/i.test(reference)) return;
  const clean = decodeURIComponent(reference.split(/[?#]/, 1)[0]);
  const target = clean.startsWith("/")
    ? path.resolve(root, `.${clean}`)
    : path.resolve(path.dirname(source), clean);
  const relative = path.relative(root, target);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Artifact reference escapes output directory: ${source} -> ${reference}`);
  }
  await requireFile(target);
}

export async function checkArtifacts(root = frontendRoot) {
  const client = path.join(root, "dist", "client");
  const server = path.join(root, "dist", "server");
  const index = path.join(client, "index.html");
  await requireFile(index);
  await requireFile(path.join(server, "entry-server.js"));
  const manifestFile = path.join(client, ".vite", "manifest.json");
  await requireFile(manifestFile);
  const manifest = JSON.parse(await fs.readFile(manifestFile, "utf8"));
  if (!Object.values(manifest).some((entry) => entry.isEntry)) {
    throw new Error("Build manifest has no browser entry point");
  }
  for (const [key, entry] of Object.entries(manifest)) {
    if (typeof entry.file !== "string") throw new Error(`Manifest entry has no output file: ${key}`);
    for (const reference of [entry.file, ...(entry.css ?? []), ...(entry.assets ?? [])]) {
      await checkReference(client, index, reference);
    }
    for (const dependency of [...(entry.imports ?? []), ...(entry.dynamicImports ?? [])]) {
      if (!manifest[dependency]) throw new Error(`Missing manifest dependency: ${key} -> ${dependency}`);
    }
  }
  await init;
  const clientFiles = await walk(client);
  if (!clientFiles.some((file) => file.endsWith(".js"))) throw new Error("No browser JavaScript artifacts");
  for (const file of clientFiles) {
    const extension = path.extname(file);
    if (![".js", ".css", ".html"].includes(extension)) continue;
    await requireFile(file);
    const source = await fs.readFile(file, "utf8");
    if (extension === ".js") {
      const [imports] = parse(source);
      for (const item of imports) {
        // Non-literal dynamic imports are checked through their emitted chunks.
        if (item.n === undefined) continue;
        if (!/^(?:\.\.?\/|\/|https?:|data:)/i.test(item.n)) {
          throw new Error(`Unresolved browser module: ${file} -> ${item.n}`);
        }
        await checkReference(client, file, item.n);
      }
    } else if (extension === ".css") {
      for (const match of source.matchAll(/url\(\s*["']?([^\s"')]+)["']?\s*\)/g)) {
        await checkReference(client, file, match[1]);
      }
    } else {
      for (const match of source.matchAll(/<(?:script|link)\b[^>]*\b(?:src|href)=["']([^"']+)["']/gi)) {
        await checkReference(client, file, match[1]);
      }
    }
  }
  const require = createRequire(path.join(root, "package.json"));
  for (const file of await walk(server)) {
    if (!file.endsWith(".js")) continue;
    const [imports] = parse(await fs.readFile(file, "utf8"));
    for (const item of imports) {
      if (item.n === undefined) continue;
      if (item.n.startsWith(".")) {
        await checkReference(server, file, item.n);
      } else {
        // Node built-ins and deliberately externalized SSR packages must resolve.
        require.resolve(item.n);
      }
    }
  }
  return clientFiles.length;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    await checkDependencies();
    if (!process.argv.includes("--dependencies-only")) {
      const count = await checkArtifacts();
      console.log(`Artifact integrity passed (${count} client files; SSR imports resolved).`);
    } else {
      console.log("Dependency entry point integrity passed.");
    }
  } catch (error) {
    console.error(`Artifact integrity failed: ${error.message}`);
    process.exitCode = 1;
  }
}
