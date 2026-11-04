import assert from "node:assert/strict";
import test from "node:test";
import { createRuntimeApp } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { gateway, headers, input, THREAD_A, tick, withServer } from "./support.js";

test("Runtime factory has a fixed HTTPS business gateway and no model configuration", () => {
  const config = loadConfig({ XIAOHE_BUSINESS_GATEWAY_URL: "https://portal.lvh.me/candidate/ai-api/v1", XIAOHE_PUBLIC_ORIGIN: "https://portal.lvh.me" });
  assert.equal(config.gatewayUrl, "https://portal.lvh.me/candidate/ai-api/v1");
  const app = createRuntimeApp(config);
  assert.equal(typeof app.listen, "function");
});

test("run is committed once before shared connect; waiting_input finishes transport only", async () => {
  const fake = gateway();
  await withServer(fake, async url => {
    const response = await fetch(url + "/agent/default/run", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    assert.equal(response.status, 200);
    const data = await response.text();
    assert.ok(data.includes("RUN_STARTED") && data.includes("RUN_FINISHED") && data.includes("waiting_input"));
    assert.equal(fake.calls.filter(call => call.url.endsWith("/run")).length, 1);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/connect")).length, 1);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/stop")).length, 0);
    assert.equal(fake.stats.businessActive, true);
  });
});

test("unsubscribe_only_detaches_and_explicit_stop_cancels", async () => {
  const fake = gateway({ hold: true });
  await withServer(fake, async url => {
    const abort = new AbortController();
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()), signal: abort.signal });
    assert.equal(response.status, 200);
    const reader = response.body!.getReader();
    await reader.read();
    abort.abort();
    await reader.cancel().catch(() => undefined);
    await tick();
    assert.ok(fake.stats.aborts > 0);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/stop")).length, 0);
    assert.equal(fake.calls.filter(call => call.url.endsWith("/run")).length, 0);
    const stop = await fetch(url + `/agent/default/stop/${THREAD_A}`, { method: "POST", headers: headers(), body: JSON.stringify({ runId: "attempt-1" }) });
    assert.equal(stop.status, 200);
    await stop.text();
    assert.equal(fake.calls.filter(call => call.url.endsWith("/stop")).length, 1);
  });
});

test("failed or malformed admission never emits RUN_STARTED", async () => {
  for (const options of [{ rejectRun: true }, { malformed: true }]) {
    const fake = gateway(options);
    await withServer(fake, async url => {
      const result = await fetch(url + "/agent/default/run", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
      assert.ok(result.status >= 400);
      const text = await result.text();
      assert.ok(!text.includes("RUN_STARTED") && !text.includes("must-not-forward"));
      assert.equal(fake.calls.filter(call => call.url.endsWith("/connect")).length, 0);
    });
  }
});

test("transport EOF without business terminal is a safe error", async () => {
  const fake = gateway({ truncate: true });
  await withServer(fake, async url => {
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    const text = await response.text();
    assert.ok(text.includes("RUN_ERROR") && !text.includes("RUN_FINISHED"));
    assert.equal(fake.calls.filter(call => call.url.endsWith("/run")).length, 0);
  });
});

test("invalid scope or revocation before snapshot never releases private pending frames", async () => {
  for (const options of [{ foreignStream: true }, { earlyError: true }]) {
    const fake = gateway(options);
    await withServer(fake, async url => {
      const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
      const text = await response.text();
      assert.ok(!text.includes("must-not-forward-private"));
      const events = text.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => JSON.parse(line.slice(5)));
      assert.equal(events.filter(event => event.type === "RUN_ERROR").length, 1);
      assert.ok(!events.some(event => event.type === "RUN_FINISHED"));
    });
  }
});

test("optional empty stop body cancels only once and invalid JSON is input error", async () => {
  const fake = gateway();
  await withServer(fake, async url => {
    const stop = await fetch(url + `/agent/default/stop/${THREAD_A}`, { method: "POST", headers: headers() });
    assert.equal(stop.status, 200);
    await stop.text();
    assert.equal(fake.calls.filter(call => call.url.endsWith("/stop")).length, 1);
    const invalid = await fetch(url + "/agent/default/run", { method: "POST", headers: headers(), body: "{" });
    assert.equal(invalid.status, 422);
    await invalid.text();
    assert.equal(fake.calls.filter(call => call.url.endsWith("/run")).length, 0);
  });
});
