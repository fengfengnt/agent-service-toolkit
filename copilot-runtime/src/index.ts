import { loadConfig } from "./config.js";

process.env.COPILOTKIT_TELEMETRY_DISABLED = "true";
process.env.DO_NOT_TRACK = "1";
const { createRuntimeApp } = await import("./server.js");
const config = loadConfig();
const server = createRuntimeApp(config).listen(config.port, "0.0.0.0");
const shutdown = () => {
  server.close();
  server.closeAllConnections();
};
process.once("SIGTERM", shutdown);
process.once("SIGINT", shutdown);
