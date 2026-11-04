import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig } from "../src/config.js";
import { cookie, gateway, headers, input, THREAD_A, THREAD_B, withServer } from "./support.js";

test("configuration rejects non-TLS, credentials, and recursive Runtime gateways", () => {
  for (const url of ["http://portal.lvh.me/ai-api/v1", "https://user:password@portal.lvh.me/ai-api/v1", "https://portal.lvh.me/copilot-api"]) {
    assert.throws(() => loadConfig({ XIAOHE_BUSINESS_GATEWAY_URL: url, XIAOHE_PUBLIC_ORIGIN: "https://portal.lvh.me" }));
  }
});

test("public info permits browser GET without Origin and never authorizes private POST", async () => {
  const fake = gateway();
  await withServer(fake, async url => {
    const info = await fetch(url + "/info");
    assert.equal(info.status, 200);
    assert.ok((await info.json()).agents);
    const denied = await fetch(url + "/agent/default/connect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input()) });
    assert.equal(denied.status, 403);
    await denied.text();
    assert.equal(fake.calls.length, 0);
  });
});

test("unsupported history/state/clear and agent routes fail closed before business calls", async t => {
  const fake = gateway();
  await withServer(fake, async url => {
    const matrix = [
      ["GET", "/threads"], ["GET", "/threads/x/messages"], ["GET", "/threads/x/events"], ["GET", "/threads/x/state"],
      ["POST", "/threads/clear"], ["PATCH", "/threads/x"], ["DELETE", "/threads/x"], ["POST", "/threads/x/archive"],
      ["POST", "/threads/subscribe"], ["POST", "/transcribe"], ["GET", "/inspector-metadata"], ["GET", "/inspector-learning"],
      ["POST", "/agent/default/suggest"], ["POST", "/trajectory/x/connect"], ["GET", "/memories"], ["POST", "/memories/recall"],
      ["POST", "/memories/subscribe"], ["PATCH", "/memories/x"], ["DELETE", "/memories/x"], ["POST", "/annotate"], ["GET", "/cpk-debug-events"],
      ["POST", "/agent/other/run"], ["POST", "/prefix/agent/default/run"],
    ];
    for (const [method, path] of matrix) {
      await t.test(`${method} ${path} -> 404`, async () => {
        const response = await fetch(url + path, { method, headers: headers(), ...(method === "GET" ? {} : { body: "{}" }) });
        assert.equal(response.status, 404);
        await response.text();
      });
    }
    assert.equal(fake.calls.length, 0);
  });
});

test("Cookie/CSRF is filtered per concurrent request and cannot override the gateway", async () => {
  const fake = gateway();
  await withServer(fake, async url => {
    await Promise.all([["A", THREAD_A], ["B", THREAD_B]].map(async ([who, thread]) => {
      const response = await fetch(url + "/agent/default/run", { method: "POST", headers: { ...headers(who), Cookie: cookie(who) + "; __Host-XIAOHEAUTH=do-not-forward; portal=do-not-forward", Authorization: "Bearer do-not-forward", "X-Xiaohe-System-Id": "forged-system", "X-Forwarded-For": "forged-peer" }, body: JSON.stringify({ ...input(thread), state: { userId: "forged" }, forwardedProps: { url: "https://never-call.invalid" } }) });
      assert.equal(response.status, 200);
      await response.text();
    }));
    for (const call of fake.calls) {
      const expected = call.body.threadId === THREAD_A ? "A" : "B";
      assert.ok(call.url.startsWith(configForTest));
      assert.ok(call.headers.get("cookie") === cookie(expected));
      assert.ok(call.headers.get("x-xiaohe-csrf") === `fake-csrf-${expected}`);
      assert.ok(!call.headers.has("authorization") && !call.headers.has("x-xiaohe-system-id") && !call.headers.has("x-forwarded-for"));
    }
  });
});

const configForTest = "https://portal.lvh.me/candidate/ai-api/v1/agui/";

test("missing CSRF, wrong Origin, duplicate Cookie and foreign object are refused", async () => {
  const fake = gateway();
  await withServer(fake, async url => {
    for (const changed of [{ "X-Xiaohe-CSRF": "" }, { Origin: "https://foreign.invalid" }, { Cookie: cookie() + "; " + cookie("B") }]) {
      const response = await fetch(url + "/agent/default/run", { method: "POST", headers: { ...headers(), ...changed }, body: JSON.stringify(input()) });
      assert.ok(response.status === 401 || response.status === 403);
      await response.text();
    }
    assert.equal(fake.calls.length, 0);
    const foreign = await fetch(url + "/agent/default/connect", { method: "POST", headers: headers("B"), body: JSON.stringify(input()) });
    assert.equal(foreign.status, 403);
    await foreign.text();
  });
});
