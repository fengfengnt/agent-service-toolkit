import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (name: string) => readFileSync(new URL("../" + name, import.meta.url), "utf8");

test("compose_preserves_requested_identity_and_private_binding", () => {
  const compose = JSON.parse(read("deploy/compose.yaml"));
  assert.equal(compose.name, "yunhezhiyuan");
  assert.deepEqual(Object.keys(compose.services), ["aibackend-copilotruntime"]);
  const service = compose.services["aibackend-copilotruntime"];
  assert.equal(service.container_name, "yunhezhiyuan-aibackend-copilotruntime");
  assert.match(service.image, /^yunhezhiyuan-aibackend-copilotruntime:/);
  assert.deepEqual(service.ports, ["127.0.0.1:18110:3000"]);
  assert.deepEqual(service.volumes ?? [], []);
  assert.equal(service.read_only, true);
  assert.deepEqual(service.cap_drop, ["ALL"]);
  assert.equal(service.environment.NODE_EXTRA_CA_CERTS, "/run/configs/public-ca.pem");
  assert.ok(!JSON.stringify(service).includes("AUTH_SECRET"));
});

test("image contains only runtime artifacts and runs nonroot with verified TLS", () => {
  const dockerfile = read("Dockerfile");
  assert.equal((dockerfile.match(/^FROM /gm) ?? []).length, 2);
  assert.match(dockerfile, /npm ci --ignore-scripts/);
  assert.match(dockerfile, /USER node/);
  assert.match(dockerfile, /COPY --from=build/);
  assert.ok(!/^COPY\s+\.\s/gm.test(dockerfile));
  assert.ok(!/NODE_TLS_REJECT_UNAUTHORIZED|AUTH_SECRET|\.env|\.pem|\.key/.test(dockerfile));
  const ignore = read(".dockerignore");
  for (const name of ["node_modules", "dist", ".env*", "*.pem", "*.key"]) assert.ok(ignore.includes(name));
  const index = read("src/index.ts");
  assert.match(index, /SIGTERM/);
  assert.match(index, /closeAllConnections/);
  assert.ok(!/\.stop\(|cancel_run|\/stop/.test(index));
});

test("candidate HTTPS paths strip only candidate prefix and retain SSE headers", () => {
  const nginx = read("deploy/nginx-candidate.conf");
  assert.match(nginx, /location \^~ \/copilot-candidate\/copilot-api\//);
  assert.match(nginx, /proxy_pass http:\/\/127\.0\.0\.1:18110\/copilot-api\//);
  assert.match(nginx, /proxy_pass http:\/\/127\.0\.0\.1:18111\/ai-api\//);
  assert.equal((nginx.match(/proxy_buffering off/g) ?? []).length, 2);
  assert.equal((nginx.match(/proxy_cache off/g) ?? []).length, 2);
  assert.match(nginx, /proxy_set_header X-Xiaohe-System-Id portal;/);
});
