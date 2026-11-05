import assert from "node:assert/strict";
import { fork } from "node:child_process";
import { once } from "node:events";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { RunAgentInputSchema } from "@ag-ui/core/schemas";
import { config, gateway, headers, input } from "./support.js";

const require = createRequire(import.meta.url);
const client = createRequire(require.resolve("@ag-ui/client"));
const fixedVersion = { major: 4, minor: 6, patch: 7 };
const compiled = process.env.XIAOHE_TEST_COMPILED === "1";

test("actual client CJS and core ESM use fixed v4 with bounded collections rejected early", () => {
  const { z } = client("zod/v4");
  assert.deepEqual(z.core.version, fixedVersion);
  assert.deepEqual(RunAgentInputSchema._zod.version, fixedVersion);
  const result = z.array(z.string()).max(2).safeParse([1, 2, 3]);
  assert.equal(result.success, false);
  assert.equal(result.error.issues.length, 1);
  assert.equal(result.error.issues[0].code, "too_big");
  let visits = 0;
  z.array(z.string().refine(() => { visits++; return true; })).max(2).safeParse(["a", "b", "c"]);
  assert.equal(visits, 0);
});

test("v4 public subpaths align while root/v3 keep their original resolution", async () => {
  for (const path of ["zod/v4", "zod/v4/core", "zod/v4/mini", "zod/v4/locales", "zod/v4/locales/en.js"]) {
    assert.equal(client.resolve(path), require.resolve(path));
    await import(path);
  }
  assert.equal(client("zod/package.json").version, "3.25.76");
  assert.equal(createRequire(require.resolve("@copilotkit/runtime"))("zod/package.json").version, "3.25.76");
  const { z } = client("zod/v3");
  assert.equal(z.array(z.string()).max(2).safeParse([1, 2, 3]).error.issues.length, 4);
  for (const path of ["zod/v4/private", "zod/v4/locales/../../v3/index.js", "zod/v4/locales/../index.js",
    "zod/v4/locales/..\\classic\\external.cjs", "zod/v4/locales/..\\..\\v3\\index.cjs"]) {
    assert.throws(() => client(path));
    await assert.rejects(import(path));
  }
});

test("formal entry installs the resolver before SDK imports without inheriting a test preload", { timeout: 20000 }, async () => {
  const socket = createServer().listen(0, "127.0.0.1");
  await once(socket, "listening");
  const address = socket.address();
  assert.ok(address && typeof address !== "string");
  const port = address.port;
  await new Promise<void>((resolve, reject) => socket.close(error => error ? reject(error) : resolve()));
  const child = fork(new URL(compiled ? "../dist/index.js" : "../src/index.ts", import.meta.url), [], {
    // Explicit arguments omit the parent harness's resolver; only index may install it.
    execArgv: [...(compiled ? [] : ["--import", "tsx"]), "--import", "./tests/zod-observer.mjs"],
    cwd: new URL("..", import.meta.url), silent: true,
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_OPTIONS: "", PORT: String(port),
      XIAOHE_PUBLIC_ORIGIN: "https://portal.lvh.me", XIAOHE_BUSINESS_GATEWAY_URL: "https://portal.lvh.me/candidate/ai-api/v1" },
  });
  let stderr = "";
  child.stderr!.on("data", chunk => { stderr = (stderr + chunk.toString()).slice(-2000); });
  child.stdout!.resume();
  try {
    let ready = false;
    for (let attempt = 0; attempt < 150; attempt++) {
      assert.equal(child.exitCode, null, stderr);
      ready = await fetch(`http://127.0.0.1:${port}/healthz`, { signal: AbortSignal.timeout(500) })
        .then(async response => { await response.text(); return response.status === 200; }).catch(() => false);
      if (ready) break;
      await delay(50);
    }
    assert.ok(ready, stderr);
    const info = await fetch(`http://127.0.0.1:${port}/copilot-api/info`, { signal: AbortSignal.timeout(3000) });
    assert.equal(info.status, 200);
    await info.text();
    const answer = once(child, "message", { signal: AbortSignal.timeout(3000) });
    child.send("probe");
    const [value] = await answer;
    assert.deepEqual(value.versions, ["4.6.7"]);
    assert.deepEqual(value.cjsVersion, fixedVersion);
    assert.equal(value.visits, 0);
    assert.equal(value.issues, 1);
    assert.equal(value.legacyVersion, "3.25.76");
  } finally {
    if (child.exitCode === null) { const exited = once(child, "exit"); child.kill(); await exited; }
  }
});

test("Runtime factory retains health, info, admission, stream and route guards", async () => {
  const modulePath = compiled ? "../dist/server.js" : "../src/server.js";
  const { createRuntimeApp }: typeof import("../src/server.js") = await import(modulePath);
  const fake = gateway();
  const server = createRuntimeApp(config(), { fetch: fake.transport }).listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const base = `http://127.0.0.1:${address.port}`;
  try {
    for (const path of ["/healthz", "/copilot-api/info"]) {
      const response = await fetch(base + path); assert.equal(response.status, 200); await response.text();
    }
    const run = await fetch(base + "/copilot-api/agent/default/run", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    assert.equal(run.status, 200);
    const events = (await run.text()).split(/\r?\n/).filter(line => line.startsWith("data:"))
      .map(line => JSON.parse(line.slice(5)).type);
    assert.deepEqual(events, ["RUN_STARTED", "MESSAGES_SNAPSHOT", "STATE_SNAPSHOT", "RUN_FINISHED"]);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/run")).length, 1);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/connect")).length, 1);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/stop")).length, 0);
    const denied = await fetch(base + "/copilot-api/threads");
    assert.equal(denied.status, 404); await denied.text();
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
