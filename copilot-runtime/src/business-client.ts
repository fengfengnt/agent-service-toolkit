import { readFileSync } from "node:fs";
import { Validator, type Schema } from "@cfworker/json-schema";
import { EventType, type BaseEvent, type RunAgentInput } from "@ag-ui/client";
import { EventSchemas } from "@ag-ui/core/schemas";
import type { RuntimeConfig } from "./config.js";
import { RuntimeFailure, type Accepted, type RequestContext } from "./context.js";

const schema: Schema = JSON.parse(readFileSync(new URL("../contracts/xiaohe-agui-v1.schema.json", import.meta.url), "utf8"));
const definitions = { ...schema };
delete definitions.oneOf;
const validators = Object.fromEntries(["Accepted", "Status", "Stopped", "StreamEvent", "ApiError"].map(name => [name, new Validator({ ...definitions, $ref: `#/$defs/${name}` }, "2020-12")]));

function checked(name: string, value: unknown): Record<string, unknown> {
  if (!validators[name]!.validate(value).valid || typeof value !== "object" || value === null || Array.isArray(value)) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
  return value as Record<string, unknown>;
}

export interface XiaoheBusinessClient {
  run(input: RunAgentInput): Promise<Accepted>;
  isRunning(threadId: string): Promise<boolean>;
  stop(threadId: string, attemptId?: string): Promise<boolean>;
  connect(threadId: string, attemptId?: string): AsyncGenerator<BaseEvent>;
}

export function createBusinessClient(context: RequestContext, config: RuntimeConfig, transport: typeof fetch = fetch, signal: AbortSignal = context.signal): XiaoheBusinessClient {
  async function request(path: string, body: unknown): Promise<Response> {
    let response: Response;
    try {
      response = await transport(config.gatewayUrl + "/agui/" + path, { method: "POST", signal,
        headers: { Cookie: context.cookie, "X-Xiaohe-CSRF": context.csrf, Origin: context.origin, "Content-Type": "application/json", Accept: path === "connect" ? "text/event-stream" : "application/json" },
        body: JSON.stringify(body), redirect: "error" });
    } catch { throw new RuntimeFailure("UPSTREAM_UNAVAILABLE"); }
    if (!response.ok) {
      const value: unknown = await response.json().catch(() => null);
      if (validators.ApiError!.validate(value).valid && typeof value === "object" && value !== null && "error" in value && typeof value.error === "object" && value.error !== null && "code" in value.error && typeof value.error.code === "string") throw new RuntimeFailure(value.error.code, response.status);
      throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
    }
    return response;
  }
  async function json(path: string, body: unknown, name: string) {
    const response = await request(path, body);
    return checked(name, await response.json().catch(() => null));
  }
  return {
    async run(input) {
      const value = await json("run", input, "Accepted");
      if (typeof value.attemptId !== "string" || typeof value.businessRunId !== "string" || value.attemptId !== input.runId) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
      return { attemptId: value.attemptId, businessRunId: value.businessRunId };
    },
    async isRunning(threadId) {
      const value = await json("status", { threadId }, "Status");
      if (value.conversationId !== threadId) throw new RuntimeFailure("FORBIDDEN", 403);
      if (typeof value.isRunning !== "boolean") throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
      return value.isRunning;
    },
    async stop(threadId, attemptId) {
      const value = await json("stop", { threadId, ...(attemptId === undefined ? {} : { attemptId }) }, "Stopped");
      if (typeof value.stopped !== "boolean") throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
      return value.stopped;
    },
    async *connect(threadId, attemptId) {
      const response = await request("connect", { threadId, ...(attemptId === undefined ? {} : { attemptId }) });
      if (!response.headers.get("content-type")?.startsWith("text/event-stream") || !response.body) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        while (true) {
          const chunk = await reader.read();
          buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
          let match: RegExpExecArray | null;
          while ((match = /\r?\n\r?\n/.exec(buffer))) {
            const frame = buffer.slice(0, match.index);
            buffer = buffer.slice(match.index + match[0].length);
            const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).replace(/^ /, "")).join("\n");
            if (!data) continue;
            let value: unknown;
            try { value = JSON.parse(data); } catch { throw new RuntimeFailure("UPSTREAM_UNAVAILABLE"); }
            checked("StreamEvent", value);
            const event = EventSchemas.parse(value);
            if ((event.type === EventType.RUN_STARTED || event.type === EventType.RUN_FINISHED) && event.threadId !== threadId) throw new RuntimeFailure("FORBIDDEN", 403);
            if (event.type === EventType.STATE_SNAPSHOT) {
              const state = event.snapshot as { xiaohe: { conversationId: string; attemptId: string | null; businessRunId: string | null } };
              if (state.xiaohe.conversationId !== threadId || (attemptId !== undefined && state.xiaohe.attemptId !== attemptId) || (attemptId !== undefined && context.admission && state.xiaohe.businessRunId !== context.admission.businessRunId)) throw new RuntimeFailure("FORBIDDEN", 403);
            }
            if (event.type === EventType.CUSTOM && (event.value as { conversationId: string }).conversationId !== threadId) throw new RuntimeFailure("FORBIDDEN", 403);
            yield event;
          }
          if (chunk.done) {
            if (buffer.trim()) throw new RuntimeFailure("EVENT_CURSOR_EXPIRED", 409);
            break;
          }
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
    },
  };
}
