import { AgentRunner, type AgentRunnerRunRequest, type AgentRunnerConnectRequest, type AgentRunnerIsRunningRequest, type AgentRunnerStopRequest } from "@copilotkit/runtime/v2";
import { EventType, type BaseEvent } from "@ag-ui/client";
import { Observable } from "rxjs";
import { createBusinessClient } from "./business-client.js";
import type { RuntimeConfig } from "./config.js";
import { currentContext, RuntimeFailure, safeFailure } from "./context.js";

export function createXiaoheRunner(config: RuntimeConfig, transport: typeof fetch = fetch): AgentRunner {
  function stream(threadId: string, attemptId?: string): Observable<BaseEvent> {
    const context = currentContext();
    return new Observable(subscriber => {
      const abort = new AbortController();
      const client = createBusinessClient(context, config, transport, AbortSignal.any([context.signal, abort.signal]));
      void (async () => {
        let started = false, terminal = false, snapshot = false;
        const pending: BaseEvent[] = [];
        for await (const event of client.connect(threadId, attemptId)) {
          if (subscriber.closed) return;
          if (event.type === EventType.RUN_ERROR) {
            terminal = true;
            subscriber.next(event);
            break;
          }
          if (event.type === EventType.RUN_STARTED) started = true;
          if (event.type === EventType.STATE_SNAPSHOT) snapshot = true;
          if (!snapshot) {
            if ((event.type !== EventType.RUN_STARTED && event.type !== EventType.MESSAGES_SNAPSHOT) || pending.length >= 2) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
            pending.push(event);
            continue;
          }
          for (const initial of pending) subscriber.next(initial);
          pending.length = 0;
          subscriber.next(event);
          if (event.type === EventType.RUN_FINISHED) terminal = true;
          if (terminal) break;
        }
        if (!subscriber.closed && !terminal && (!snapshot || started)) throw new RuntimeFailure("EVENT_CURSOR_EXPIRED", 409);
        subscriber.complete();
      })().catch(error => {
        if (subscriber.closed || context.signal.aborted || abort.signal.aborted) return;
        const failure = safeFailure(error);
        subscriber.next({ type: EventType.RUN_ERROR, code: failure.code, message: failure.message });
        subscriber.complete();
      });
      return () => { abort.abort(); };
    });
  }
  return new class extends AgentRunner {
    override run(request: AgentRunnerRunRequest): Observable<BaseEvent> {
      const accepted = currentContext().admission;
      if (!accepted || accepted.attemptId !== request.input.runId) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
      return stream(request.threadId, accepted.attemptId);
    }
    override connect(request: AgentRunnerConnectRequest): Observable<BaseEvent> { return stream(request.threadId); }
    override isRunning(request: AgentRunnerIsRunningRequest): Promise<boolean> { return createBusinessClient(currentContext(), config, transport).isRunning(request.threadId); }
    override async stop(_request: AgentRunnerStopRequest): Promise<boolean> {
      const stopped = currentContext().stopResult;
      if (stopped === undefined) throw new RuntimeFailure("UPSTREAM_UNAVAILABLE");
      return stopped;
    }
  }();
}
