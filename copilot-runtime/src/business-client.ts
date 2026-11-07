import { readFileSync } from "node:fs";
import { Validator, type Schema } from "@cfworker/json-schema";
import { EventType, type BaseEvent, type RunAgentInput } from "@ag-ui/client";
import { EventSchemas } from "@ag-ui/core/schemas";
import type { RuntimeConfig } from "./config.js";
import { RuntimeFailure, type Accepted, type RequestContext } from "./context.js";

function loadValidators(version: 1 | 2) {
  const schema: Schema = JSON.parse(readFileSync(new URL(`../contracts/xiaohe-agui-v${version}.schema.json`, import.meta.url), "utf8"));
  const definitions = { ...schema };
  delete definitions.oneOf;
  delete definitions.anyOf;
  return Object.fromEntries(["Accepted", "Status", "Stopped", "StreamEvent", "ApiError"].map(name => [name, new Validator({ ...definitions, $ref: `#/$defs/${version === 2 && name === "StreamEvent" ? "StreamEventV2" : name}` }, "2020-12")]));
}
const versions = { 1: loadValidators(1), 2: loadValidators(2) };

function checked(name: string, value: unknown, version: 1 | 2): Record<string, unknown> {
  if (!versions[version][name]!.validate(value).valid || typeof value !== "object" || value === null || Array.isArray(value)) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
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
        headers: { Cookie: context.cookie, "X-Xiaohe-CSRF": context.csrf, Origin: context.origin, "X-Xiaohe-View-Version": String(config.viewVersion), "Content-Type": "application/json", Accept: path === "connect" ? "text/event-stream" : "application/json" },
        body: JSON.stringify(body), redirect: "error" });
    } catch { throw new RuntimeFailure("UPSTREAM_UNAVAILABLE"); }
    const echo = response.headers.get("X-Xiaohe-View-Version");
    if ((config.viewVersion === 2 && echo !== "2") || (config.viewVersion === 1 && echo !== null && echo !== "1")) {
      await response.body?.cancel();
      throw new RuntimeFailure("VIEW_VERSION_UNSUPPORTED", 406);
    }
    if (config.viewVersion === 2 && response.headers.get("Cache-Control") !== "private, no-store") {
      await response.body?.cancel();
      throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
    }
    if (!response.ok) {
      const value: unknown = await response.json().catch(() => null);
      if (versions[config.viewVersion].ApiError!.validate(value).valid && typeof value === "object" && value !== null && "error" in value && typeof value.error === "object" && value.error !== null && "code" in value.error && typeof value.error.code === "string") throw new RuntimeFailure(value.error.code, response.status);
      throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
    }
    return response;
  }
  async function json(path: string, body: unknown, name: string) {
    const response = await request(path, body);
    return checked(name, await response.json().catch(() => null), config.viewVersion);
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
      let historyEpoch: number | undefined, sequence = 0, ready = false;
      let windowIds = new Set<string>();
      let pendingIds: string[] | undefined;
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
            checked("StreamEvent", value, config.viewVersion);
            const event = EventSchemas.parse(value);
            if ((event.type === EventType.RUN_STARTED || event.type === EventType.RUN_FINISHED) && event.threadId !== threadId) throw new RuntimeFailure("FORBIDDEN", 403);
            if (event.type === EventType.STATE_SNAPSHOT) {
              const state = event.snapshot as { xiaohe: { conversationId: string; attemptId: string | null; businessRunId: string | null; history: { historyEpoch: number }; sync: { watermarkSequence: number }; messages: Array<{ id: string }> } };
              if (state.xiaohe.conversationId !== threadId || (attemptId !== undefined && state.xiaohe.attemptId !== attemptId) || (attemptId !== undefined && context.admission && state.xiaohe.businessRunId !== context.admission.businessRunId)) throw new RuntimeFailure("FORBIDDEN", 403);
              if (config.viewVersion === 2) {
                const epoch = state.xiaohe.history.historyEpoch, watermark = state.xiaohe.sync.watermarkSequence;
                if (!Number.isSafeInteger(epoch) || !Number.isSafeInteger(watermark)) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
                if (historyEpoch !== undefined && epoch !== historyEpoch) throw new RuntimeFailure("HISTORY_RESET_REQUIRED", 409);
                if (historyEpoch !== undefined && watermark !== sequence) throw new RuntimeFailure("EVENT_CURSOR_EXPIRED", 409);
                const ids = state.xiaohe.messages.map(message => message.id);
                if (new Set(ids).size !== ids.length || (pendingIds && JSON.stringify(ids) !== JSON.stringify(pendingIds))) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
                historyEpoch = epoch;
                sequence = watermark;
                windowIds = new Set(ids);
                pendingIds = undefined;
              }
            }
            if (event.type === EventType.CUSTOM && (event.value as { conversationId: string }).conversationId !== threadId) throw new RuntimeFailure("FORBIDDEN", 403);
            if (config.viewVersion === 2) {
              if (event.type === EventType.MESSAGES_SNAPSHOT) {
                pendingIds = event.messages.map(message => message.id);
                if (pendingIds.length > 10 || new Set(pendingIds).size !== pendingIds.length) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
              }
              if (event.type === EventType.CUSTOM) {
                const data = event.value as { historyEpoch: number; watermarkSequence?: number; sequence?: number };
                if (historyEpoch === undefined || data.historyEpoch !== historyEpoch) throw new RuntimeFailure("HISTORY_RESET_REQUIRED", 409);
                if (event.name === "xiaohe.v2.ready") {
                  if (data.watermarkSequence !== sequence) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
                  ready = true;
                } else if (event.name === "xiaohe.v2.event") {
                  if (!ready || !Number.isSafeInteger(data.sequence) || data.sequence !== sequence + 1) throw new RuntimeFailure("EVENT_CURSOR_EXPIRED", 409);
                  sequence = data.sequence;
                } else if (!Number.isSafeInteger(data.watermarkSequence) || data.watermarkSequence! < sequence) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
              }
              if ((event.type === EventType.TEXT_MESSAGE_START || event.type === EventType.TEXT_MESSAGE_CONTENT || event.type === EventType.TEXT_MESSAGE_END) && (!ready || !windowIds.has(event.messageId))) throw new RuntimeFailure("EVENT_CURSOR_EXPIRED", 409);
              if (event.type === EventType.RUN_FINISHED && !ready) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
            }
            if (event.type === EventType.RUN_ERROR) {
              const failure = new RuntimeFailure(typeof event.code === "string" ? event.code : "UPSTREAM_UNAVAILABLE");
              yield { type: EventType.RUN_ERROR, code: failure.code, message: failure.message };
            } else yield event;
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
