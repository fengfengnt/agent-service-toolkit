import { randomUUID } from "node:crypto";
import express, { type Express } from "express";
import { HttpAgent } from "@ag-ui/client";
import { RunAgentInputSchema } from "@ag-ui/core/schemas";
import { CopilotRuntime } from "@copilotkit/runtime/v2";
import { createCopilotExpressHandler } from "@copilotkit/runtime/v2/express";
import { createBusinessClient } from "./business-client.js";
import type { RuntimeConfig } from "./config.js";
import { currentContext, requests, RuntimeFailure, safeFailure, sessionCookie } from "./context.js";
import { createXiaoheRunner } from "./runner.js";

function failureResponse(error: unknown): Response {
  const value = safeFailure(error);
  return Response.json({ error: { code: value.code, message: value.message, retryable: value.status >= 500 }, requestId: randomUUID() }, { status: value.status, headers: { "Cache-Control": "private, no-store" } });
}

async function boundedRequest(request: Request): Promise<Request> {
  if (!request.body) return request;
  if (Number(request.headers.get("content-length")) > 65536) throw failureResponse(new RuntimeFailure("INPUT_INVALID", 422));
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.length;
      if (size > 65536) throw failureResponse(new RuntimeFailure("INPUT_INVALID", 422));
      chunks.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let position = 0;
  for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.length; }
  return new Request(request.url, { method: request.method, headers: request.headers, body: bytes.buffer, signal: request.signal });
}

export function createRuntimeApp(config: RuntimeConfig, dependencies: { fetch?: typeof fetch } = {}): Express {
  const transport = dependencies.fetch ?? fetch;
  const runtime = new CopilotRuntime({ agents: { default: new HttpAgent({ url: config.gatewayUrl + "/agui/run" }) }, runner: createXiaoheRunner(config, transport), forwardHeaders: { allow: ["cookie", "x-xiaohe-csrf", "origin"] } });
  const app = express();
  app.get("/healthz", (_request, response) => { response.json({ status: "live" }); });
  app.use((request, response, next) => {
    const info = request.method === "GET" && request.path === "/copilot-api/info";
    const allowed = request.method === "POST" && /^\/copilot-api\/agent\/default\/(run|connect|stop\/[^/]+)$/.test(request.path);
    response.setHeader("Cache-Control", "private, no-store");
    if (!info && !allowed) { response.status(404).json({ error: { code: "NOT_FOUND" } }); return; }
    if (info) { next(); return; }
    try {
      if (request.get("origin") !== config.publicOrigin) throw new RuntimeFailure("FORBIDDEN", 403);
      const cookie = sessionCookie(request.headers.cookie);
      const csrf = request.get("x-xiaohe-csrf");
      if (!csrf) throw new RuntimeFailure("CSRF_INVALID", 403);
      request.headers.cookie = cookie;
      const abort = new AbortController();
      response.once("close", () => abort.abort());
      requests.run({ cookie, csrf, origin: config.publicOrigin, signal: abort.signal }, next);
    } catch (error) {
      const failure = safeFailure(error);
      response.status(failure.status).json({ error: { code: failure.code, message: failure.message, retryable: false }, requestId: randomUUID() });
    }
  });
  app.use(createCopilotExpressHandler({ runtime, basePath: "/copilot-api", cors: false, activateChannels: false,
    hooks: {
      onRequest: ({ request }) => boundedRequest(request),
      async onBeforeHandler({ request, route }) {
        if (route.method === "info") return;
        const context = currentContext();
        const client = createBusinessClient(context, config, transport);
        try {
          if (route.method === "agent/run" || route.method === "agent/connect") {
            const parsed = RunAgentInputSchema.safeParse(await request.clone().json());
            if (!parsed.success) throw new RuntimeFailure("INPUT_INVALID", 422);
            if (route.method === "agent/run") context.admission = await client.run(parsed.data);
            else await client.isRunning(parsed.data.threadId);
          } else if (route.method === "agent/stop") {
            const raw = await request.clone().text();
            const value: unknown = raw.trim() ? JSON.parse(raw) : {};
            if (typeof value !== "object" || value === null || Array.isArray(value) || Object.keys(value).some(key => key !== "runId") || ("runId" in value && (typeof value.runId !== "string" || !value.runId.trim()))) throw new RuntimeFailure("INPUT_INVALID", 422);
            context.stopResult = await client.stop(route.threadId, "runId" in value && typeof value.runId === "string" ? value.runId : undefined);
          } else throw new RuntimeFailure("NOT_FOUND", 404);
        } catch (error) { throw failureResponse(error instanceof SyntaxError ? new RuntimeFailure("INPUT_INVALID", 422) : error); }
      },
    },
  }));
  return app;
}
