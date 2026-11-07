import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { loadConfig } from "../src/config.js";
import { config, gateway, headers, input, RUN, THREAD_A, withServer } from "./support.js";

const fixtures = JSON.parse(readFileSync(new URL("../../tests/xiaohe/contracts/view_v2.json", import.meta.url), "utf8"));
type Options = { active?: boolean; ready?: boolean; terminal?: boolean; echo?: string | null; wireV1?: boolean; custom?: Record<string, unknown>; events?: Array<Record<string, unknown>>; baseline?: Record<string, unknown>; overflow?: boolean; oversized?: boolean; unsafeError?: boolean; unsafeStreamError?: boolean };

function v2Gateway(options: Options = {}) {
  const fake = gateway();
  const original = fake.transport;
  fake.transport = async (url, init) => {
    const response = await original(url, init);
    const values = new Headers(response.headers);
    if (options.echo !== null) values.set("X-Xiaohe-View-Version", options.echo ?? "2");
    values.set("Cache-Control", "private, no-store");
    if (options.unsafeError) return Response.json({ error: { code: "must-not-forward", message: "private error", retryable: false }, requestId: "fixture-request" }, { status: 409, headers: values });
    if (!url.toString().endsWith("/connect") || options.wireV1 || !response.ok) {
      return new Response(response.body, { status: response.status, headers: values });
    }
    await response.body?.cancel();
    const body = JSON.parse(init?.body as string);
    const active = options.active ?? false;
    const snapshot = { ...structuredClone(fixtures.public), conversationId: body.threadId,
      activeRun: active ? { id: RUN, status: "running" } : null,
      attemptId: active ? "attempt-1" : null, businessRunId: active ? RUN : null,
      businessRunStatus: active ? "running" : null };
    if (options.overflow) snapshot.history.historyEpoch = Number.MAX_SAFE_INTEGER + 1;
    const messages = snapshot.messages.map((message: { id: string; role: string }) => ({ id: message.id, role: message.role, content: "fixture" }));
    if (options.oversized) messages.push(...Array.from({ length: 10 }, (_, id) => ({ id: `extra-${id}`, role: "assistant", content: "must-not-forward" })));
    const events = [
      ...(active ? [{ type: "RUN_STARTED", threadId: body.threadId, runId: "attempt-1" }] : []),
      { type: "MESSAGES_SNAPSHOT", messages },
      { type: "STATE_SNAPSHOT", snapshot: { xiaohe: snapshot } },
      ...(options.baseline ? [{ type: "CUSTOM", name: "xiaohe.v2.message-baseline", value: options.baseline }] : []),
      ...(options.ready === false ? [] : [{ type: "CUSTOM", name: "xiaohe.v2.ready", value: { conversationId: body.threadId, historyEpoch: snapshot.history.historyEpoch, watermarkSequence: snapshot.sync.watermarkSequence } }]),
      ...(options.custom ? [options.custom] : []),
      ...(options.events ?? []),
      ...(options.unsafeStreamError ? [{ type: "RUN_ERROR", code: "UPSTREAM_UNAVAILABLE", message: "must-not-forward" }] : []),
      ...(options.terminal ? [{ type: "RUN_FINISHED", threadId: body.threadId, runId: "attempt-1" }] : []),
    ];
    const bytes = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""));
    return new Response(bytes, { headers: values });
  };
  return fake;
}

test("view-v2 fixed configuration defaults to 1 and rejects unsupported values", () => {
  const base = { XIAOHE_BUSINESS_GATEWAY_URL: "https://portal.lvh.me/ai-api/v1", XIAOHE_PUBLIC_ORIGIN: "https://portal.lvh.me" };
  assert.equal(loadConfig(base).viewVersion, 1);
  assert.equal(config("2").viewVersion, 2);
  for (const value of ["", "0", "3", " 2", "2.0"]) assert.throws(() => loadConfig({ ...base, XIAOHE_VIEW_VERSION: value }));
});

test("view-v2 info errors and SSE echo the configured version; browser cannot override upstream", async () => {
  const fake = v2Gateway();
  await withServer(fake, async url => {
    const info = await fetch(url + "/info", { headers: { "X-Xiaohe-View-Version": "1" } });
    assert.equal(info.headers.get("X-Xiaohe-View-Version"), "2");
    assert.equal(info.headers.get("Cache-Control"), "private, no-store");
    assert.ok((await info.json()).agents);
    assert.equal(fake.calls.length, 0);
    const denied = await fetch(url + "/agent/default/connect", { method: "POST", body: "{}" });
    assert.equal(denied.status, 403);
    assert.equal(denied.headers.get("X-Xiaohe-View-Version"), "2");
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: { ...headers(), "X-Xiaohe-View-Version": "1" }, body: JSON.stringify(input()) });
    const text = await response.text();
    assert.equal(response.headers.get("X-Xiaohe-View-Version"), "2");
    assert.equal(response.headers.get("Cache-Control"), "private, no-store");
    assert.ok(text.includes("xiaohe.v2.ready") && !text.includes("RUN_ERROR"));
    assert.ok(fake.calls.length >= 2 && fake.calls.every(call => call.headers.get("X-Xiaohe-View-Version") === "2"));
  }, "2");
});

test("view-v2 idle requires ready and active EOF remains an error after ready", async () => {
  for (const options of [{ ready: false }, { active: true }, { active: true, terminal: true }]) {
    const fake = v2Gateway(options);
    await withServer(fake, async url => {
      const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
      const text = await response.text();
      assert.equal(text.includes("RUN_ERROR"), !options.terminal);
      assert.equal(text.includes("RUN_FINISHED"), !!options.terminal);
    }, "2");
  }
});

test("view-v2 rejects missing echo, v1 projection, oversized window and unsafe integer", async () => {
  for (const options of [{ echo: null }, { echo: "1" }, { wireV1: true }, { oversized: true }, { overflow: true }]) {
    const fake = v2Gateway(options);
    await withServer(fake, async url => {
      const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
      const text = await response.text();
      assert.ok(response.status >= 400 || text.includes("RUN_ERROR"));
      assert.ok(!text.includes("must-not-forward") && !text.includes("RUN_FINISHED"));
    }, "2");
  }
});

test("view-v2 custom whitelist rejects unknown names, string sequence and mixed epoch", async () => {
  for (const custom of [
    { type: "CUSTOM", name: "private.unknown", value: { conversationId: THREAD_A, text: "must-not-forward" } },
    { type: "CUSTOM", name: "xiaohe.v1.event", value: fixtures.event },
    { type: "CUSTOM", name: "xiaohe.v2.event", value: { ...fixtures.event, conversationId: THREAD_A, sequence: "1" } },
    { type: "CUSTOM", name: "xiaohe.v2.event", value: { ...fixtures.event, conversationId: THREAD_A, historyEpoch: 1 } },
  ]) {
    const fake = v2Gateway({ custom });
    await withServer(fake, async url => {
      const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
      const text = await response.text();
      assert.ok(text.includes("RUN_ERROR") && !text.includes("must-not-forward"));
    }, "2");
  }
});

test("view-v2 run and stop send version 2 while legacy mode accepts no echo and rejects explicit 2", async () => {
  const fake = v2Gateway({ active: true, terminal: true });
  await withServer(fake, async url => {
    const run = await fetch(url + "/agent/default/run", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    assert.ok((await run.text()).includes("RUN_FINISHED"));
    const stop = await fetch(url + `/agent/default/stop/${THREAD_A}`, { method: "POST", headers: headers() });
    assert.equal(stop.status, 200);
    await stop.text();
    assert.ok(fake.calls.filter(call => /\/(run|stop)$/.test(call.url)).every(call => call.headers.get("X-Xiaohe-View-Version") === "2"));
  }, "2");
  const mixed = v2Gateway();
  await withServer(mixed, async url => {
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    assert.ok(response.status >= 400);
  });
  const legacy = gateway();
  await withServer(legacy, async url => {
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    assert.ok((await response.text()).includes("RUN_FINISHED"));
  });
});

test("view-v2 filters private upstream codes and stream error messages", async () => {
  for (const options of [{ unsafeError: true }, { unsafeStreamError: true }]) {
    const fake = v2Gateway(options);
    await withServer(fake, async url => {
      const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
      const text = await response.text();
      assert.ok(!text.includes("must-not-forward"));
      assert.ok(text.includes("UPSTREAM_UNAVAILABLE"));
    }, "2");
  }
});

test("view-v2 baseline does not advance the global sequence or create an eleventh SDK message", async () => {
  const baseline = { ...fixtures.baseline, conversationId: THREAD_A, watermarkSequence: 2, message: { ...fixtures.baseline.message, id: "hidden" } };
  const events = [
    { type: "CUSTOM", name: "xiaohe.v2.event", value: { eventId: "E1", conversationId: THREAD_A, runId: RUN, type: "message.delta", payload: { messageId: "hidden", delta: "covered" }, sequence: 1, historyEpoch: 0 } },
    { type: "CUSTOM", name: "xiaohe.v2.event", value: { ...fixtures.event, eventId: "E2", conversationId: THREAD_A, sequence: 2 } },
  ];
  const fake = v2Gateway({ active: true, terminal: true, baseline, events });
  await withServer(fake, async url => {
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    const text = await response.text();
    assert.ok(!text.includes("RUN_ERROR") && text.includes("RUN_FINISHED"));
    assert.ok(text.indexOf("xiaohe.v2.message-baseline") < text.indexOf("covered"));
    assert.ok(!text.includes("TEXT_MESSAGE_CONTENT"));
  }, "2");
});

test("view-v2 standard text cannot invent a message outside the SDK window", async () => {
  const fake = v2Gateway({ active: true, terminal: true, events: [{ type: "TEXT_MESSAGE_START", messageId: "hidden", role: "assistant" }] });
  await withServer(fake, async url => {
    const response = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers(), body: JSON.stringify(input()) });
    const text = await response.text();
    assert.ok(text.includes("RUN_ERROR") && !text.includes("TEXT_MESSAGE_START"));
  }, "2");
});
