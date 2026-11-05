import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const runtime = fileURLToPath(new URL("..", import.meta.url));
const lock = JSON.parse(readFileSync(join(runtime, "package-lock.json"), "utf8"));
const roots = Object.keys(lock.packages).filter(path => path === "node_modules/zod" || path.endsWith("/node_modules/zod"));
const types = roots.flatMap(root => [root + "/v3/types.js", root + "/v3/types.cjs"]);
const license = "node_modules/@copilotkit/license-verifier/dist/";
const prefix = join(tmpdir(), "xiaohe-security-install-");
const hash = (path: string) => createHash("sha256").update(readFileSync(path)).digest("hex");

function fixture(use: (root: string) => void) {
  const root = mkdtempSync(prefix);
  const files = ["package.json", "package-lock.json", "scripts/apply-security-patches.mjs", "scripts/security-patches.json",
    ...roots.map(path => path + "/package.json"), ...types,
    "node_modules/@copilotkit/license-verifier/package.json", license + "index.mjs", license + "index.js"];
  for (const file of files) { mkdirSync(dirname(join(root, file)), { recursive: true }); copyFileSync(join(runtime, file), join(root, file)); }
  try { use(root); }
  finally { assert.ok(resolve(root).startsWith(resolve(prefix))); rmSync(root, { recursive: true, force: true }); }
}

const run = (root: string, check = false) => spawnSync(process.execPath,
  [join(root, "scripts/apply-security-patches.mjs"), ...(check ? ["--check"] : [])], { encoding: "utf8", timeout: 10000 });
function original(root: string, file: string) {
  const path = join(root, file);
  const source = readFileSync(path, "utf8").replace(/^ +(?:if \(tooBig\) )?return [\w.]*INVALID; \/\/ xiaohe-security: reject existing upper bound before element work\n/gm, "");
  const profiles = JSON.parse(readFileSync(join(root, "scripts/security-patches.json"), "utf8"));
  const version = lock.packages[roots.find(value => file.startsWith(value + "/v3/"))!].version;
  assert.equal(createHash("sha256").update(source).digest("hex"), profiles[`zod@${version}/${file.split("/").at(-1)}`].before);
  writeFileSync(path, source);
}

test("install patch is repeatable and check mode never repairs an unpatched dependency", () => fixture(root => {
  for (const file of types) original(root, file);
  const before = types.map(file => hash(join(root, file)));
  assert.notEqual(run(root, true).status, 0);
  assert.deepEqual(types.map(file => hash(join(root, file))), before);
  const first = run(root); assert.equal(first.status, 0, first.stderr);
  assert.equal(JSON.parse(first.stdout).applied, 10);
  const applied = types.map(file => hash(join(root, file)));
  const second = run(root); assert.equal(second.status, 0, second.stderr);
  assert.equal(JSON.parse(second.stdout).applied, 0);
  assert.deepEqual(types.map(file => hash(join(root, file))), applied);
  assert.equal(run(root, true).status, 0);
}));

test("unknown content in a later target prevents every earlier patch write", () => fixture(root => {
  original(root, types[0]!);
  const path = join(root, types[0]!); const before = hash(path);
  const later = join(root, license + "index.js"); writeFileSync(later, readFileSync(later, "utf8") + "\n// drift\n");
  const result = run(root); assert.notEqual(result.status, 0); assert.match(result.stderr, /content drift/);
  assert.equal(hash(path), before);
}));

test("version drift is rejected without modifying other targets", () => fixture(root => {
  original(root, types[0]!);
  const path = join(root, types[0]!); const before = hash(path);
  const file = join(root, "node_modules/@copilotkit/license-verifier/package.json");
  const pkg = JSON.parse(readFileSync(file, "utf8")); pkg.version = "0.5.1"; writeFileSync(file, JSON.stringify(pkg));
  const result = run(root); assert.notEqual(result.status, 0); assert.match(result.stderr, /version drift/);
  assert.equal(hash(path), before);
}));

test("externalized licensing requires the pinned safe root and locked license version", () => {
  fixture(root => {
    const modified = structuredClone(lock); modified.packages["node_modules/zod"].version = "3.25.76";
    writeFileSync(join(root, "package-lock.json"), JSON.stringify(modified));
    const file = join(root, "node_modules/zod/package.json"); const pkg = JSON.parse(readFileSync(file, "utf8"));
    pkg.version = "3.25.76"; writeFileSync(file, JSON.stringify(pkg));
    const old = roots.find(path => path !== "node_modules/zod")!;
    for (const name of ["types.js", "types.cjs"]) copyFileSync(join(root, old, "v3", name), join(root, "node_modules/zod/v3", name));
    assert.notEqual(run(root).status, 0, "a valid v3 package must not replace the safe external v4 root");
  });
  fixture(root => {
    const modified = structuredClone(lock); modified.packages["node_modules/@copilotkit/license-verifier"].version = "0.5.1";
    writeFileSync(join(root, "package-lock.json"), JSON.stringify(modified));
    assert.notEqual(run(root).status, 0, "locked license version drift must fail even if old files remain installed");
  });
});

test("normal npm install and Docker ignore-scripts paths require the same verified patch", () => {
  const pkg = JSON.parse(readFileSync(join(runtime, "package.json"), "utf8"));
  assert.equal(pkg.scripts.postinstall, "node scripts/apply-security-patches.mjs");
  assert.match(pkg.scripts.test, /apply-security-patches\.mjs --check/);
  assert.match(pkg.scripts.build, /apply-security-patches\.mjs --check/);
  const docker = readFileSync(join(runtime, "Dockerfile"), "utf8");
  assert.ok(docker.indexOf("COPY scripts ./scripts") < docker.indexOf("RUN npm ci"));
  assert.match(docker, /npm ci --ignore-scripts[^\n]+node scripts\/apply-security-patches\.mjs/);
  assert.ok(readFileSync(join(runtime, ".dockerignore"), "utf8").includes("!scripts/**"));
});
