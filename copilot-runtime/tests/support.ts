import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { createRuntimeApp } from "../src/server.js";
import { loadConfig } from "../src/config.js";

export const THREAD_A = "00000000-0000-4000-8000-000000000001";
export const THREAD_B = "00000000-0000-4000-8000-000000000002";
export const RUN = "00000000-0000-4000-8000-000000000003";
export const config = () => loadConfig({ XIAOHE_BUSINESS_GATEWAY_URL: "https://portal.lvh.me/candidate/ai-api/v1", XIAOHE_PUBLIC_ORIGIN: "https://portal.lvh.me" });
export const cookie = (who = "A") => `__Host-XIAOHESESSION=${who.repeat(43)}`;
export const headers = (who = "A") => ({ Cookie: cookie(who), Origin: "https://portal.lvh.me", "X-Xiaohe-CSRF": `fake-csrf-${who}`, "Content-Type": "application/json" });
export const input = (threadId = THREAD_A, runId = "attempt-1") => ({ threadId, runId, messages: [{ id: "browser-only", role: "user", content: "了解服务" }], state: {}, tools: [], context: [], forwardedProps: {} });
const viewer = { kind: "guest", guestId: "guest-A", systemId: "portal", tenantId: 1, messageCount: 1, messageLimit: 100, retentionSeconds: 14400, expiresAt: "2026-11-06T00:00:00Z" };

export function gateway(options: { hold?: boolean; rejectRun?: boolean; malformed?: boolean; truncate?: boolean; foreignStream?: boolean; earlyError?: boolean } = {}) {
  const calls: Array<{ url: string; headers: Headers; body: Record<string, unknown> }> = [];
  const stats = { aborts: 0, businessActive: false };
  const transport: typeof fetch = async (url, init) => {
    const request = new Request(url, init);
    const body: Record<string, unknown> = await request.json();
    calls.push({ url: request.url, headers: request.headers, body });
    const expected = request.headers.get("cookie") === cookie() ? THREAD_A : THREAD_B;
    const fail = (code: string, status: number) => Response.json({ error: { code, message: "safe fixture failure", retryable: false }, requestId: "fixture-request" }, { status });
    if (body.threadId !== expected) return fail("FORBIDDEN", 403);
    if (request.url.endsWith("/run")) {
      if (options.rejectRun) return fail("RUN_ACTIVE", 409);
      if (options.malformed) return Response.json({ privateData: "must-not-forward" }, { status: 202 });
      stats.businessActive = true;
      return Response.json({ attemptId: body.runId, businessRunId: RUN, message: { id: "M1", role: "user", createdAt: "2026-11-05T00:00:00Z", parts: [{ type: "text", text: "了解服务" }] }, run: { id: RUN, status: "queued" }, viewer }, { status: 202 });
    }
    if (request.url.endsWith("/status")) return Response.json({ conversationId: body.threadId, activeRun: { id: RUN, status: "waiting_input" }, run: { id: RUN, status: "waiting_input" }, attemptId: "attempt-1", businessRunId: RUN, isRunning: false, cursor: null });
    if (request.url.endsWith("/stop")) {
      stats.businessActive = false;
      return Response.json({ stopped: true, run: { id: RUN, status: "cancelled" } });
    }
    assert.ok(request.url.endsWith("/connect"));
    const runId = typeof body.attemptId === "string" ? body.attemptId : "attempt-read";
    const status = options.hold ? "running" : "waiting_input";
    const events = [
      { type: "RUN_STARTED", threadId: body.threadId, runId },
      { type: "MESSAGES_SNAPSHOT", messages: options.foreignStream || options.earlyError ? [{ id: "M-foreign", role: "assistant", content: "must-not-forward-private" }] : [] },
      ...(options.earlyError ? [{ type: "RUN_ERROR", code: "AUTH_REQUIRED", message: "请重新登录。" }] : [{ type: "STATE_SNAPSHOT", snapshot: { xiaohe: { version: 1, conversationId: options.foreignStream ? THREAD_B : body.threadId, messages: [], viewer, activeRun: { id: RUN, status }, cursor: null, merge: null, attemptId: runId, businessRunId: RUN, businessRunStatus: status } } }]),
    ];
    const bytes = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""));
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes);
        if (!options.hold) {
          if (!options.truncate && !options.earlyError) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "RUN_FINISHED", threadId: body.threadId, runId })}\n\n`));
          controller.close();
        } else request.signal.addEventListener("abort", () => { stats.aborts += 1; try { controller.close(); } catch { /* 已取消的读取器无需重复关闭。 */ } }, { once: true });
      },
      cancel() { stats.aborts += 1; },
    });
    return new Response(stream, { headers: { "content-type": "text/event-stream" } });
  };
  return { transport, calls, stats };
}

export async function withServer(fake: ReturnType<typeof gateway>, use: (url: string) => Promise<void>) {
  const app = createRuntimeApp(config(), { fetch: fake.transport });
  const server = app.listen(0, "127.0.0.1");
  await new Promise<void>(resolve => server.once("listening", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  try { await use(`http://127.0.0.1:${address.port}/copilot-api`); }
  finally { server.closeAllConnections(); await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
}

export async function tick() { await delay(30); }
