import { AsyncLocalStorage } from "node:async_hooks";

export class RuntimeFailure extends Error {
  constructor(readonly code: string, readonly status = 503) {
    super(code === "AUTH_REQUIRED" ? "请重新登录。" : code === "CSRF_INVALID" ? "请求验证失效，请重新获取会话。" : code === "VISITOR_EXPIRED" ? "访客会话已到期。" : code === "FORBIDDEN" ? "无权访问此内容。" : "请求未能完成，请稍后重试。");
  }
}

export interface Accepted { readonly attemptId: string; readonly businessRunId: string }
export interface RequestContext {
  readonly cookie: string;
  readonly csrf: string;
  readonly origin: string;
  readonly signal: AbortSignal;
  admission?: Accepted;
  stopResult?: boolean;
}
export const requests = new AsyncLocalStorage<RequestContext>();
export function currentContext(): RequestContext {
  const value = requests.getStore();
  if (!value) throw new RuntimeFailure("AUTH_REQUIRED", 401);
  return value;
}

export function sessionCookie(raw: string | undefined): string {
  const name = "__Host-XIAOHESESSION";
  const matches = (raw ?? "").split(";").map(part => part.trim()).filter(part => (part.includes("=") ? part.slice(0, part.indexOf("=")) : part) === name);
  if (matches.length !== 1) throw new RuntimeFailure("AUTH_REQUIRED", 401);
  const value = matches[0]!.slice(name.length + 1);
  if (!/^[A-Za-z0-9_-]{43}$/.test(value)) throw new RuntimeFailure("AUTH_REQUIRED", 401);
  return name + "=" + value;
}

export function safeFailure(error: unknown): RuntimeFailure {
  return error instanceof RuntimeFailure ? error : new RuntimeFailure("UPSTREAM_UNAVAILABLE");
}
