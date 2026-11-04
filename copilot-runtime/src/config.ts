export interface RuntimeConfig {
  readonly gatewayUrl: string;
  readonly publicOrigin: string;
  readonly port: number;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): RuntimeConfig {
  const gateway = new URL(env.XIAOHE_BUSINESS_GATEWAY_URL ?? "");
  const origin = new URL(env.XIAOHE_PUBLIC_ORIGIN ?? "");
  if (env.NODE_TLS_REJECT_UNAUTHORIZED === "0" || gateway.protocol !== "https:" || origin.protocol !== "https:" ||
      gateway.username || gateway.password || gateway.search || gateway.hash || origin.username || origin.password ||
      origin.pathname !== "/" || origin.search || origin.hash || gateway.origin !== origin.origin ||
      !gateway.pathname.endsWith("/ai-api/v1") || gateway.pathname.includes("/copilot-api")) {
    throw new Error("Runtime requires a fixed same-origin HTTPS business gateway with TLS verification");
  }
  const port = Number(env.PORT ?? 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("invalid Runtime port");
  return Object.freeze({ gatewayUrl: gateway.href.replace(/\/$/, ""), publicOrigin: origin.origin, port });
}
