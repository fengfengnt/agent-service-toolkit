import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);

for (const mode of ["esm", "cjs"] as const) test(`license ${mode} preserves real signature, expiry, trust and feature checks`, async () => {
  const installed = mode === "cjs" ? require("@copilotkit/license-verifier") : await import("@copilotkit/license-verifier");
  const master = generateKeyPairSync("ed25519");
  const signing = generateKeyPairSync("ed25519");
  const masterPublic = master.publicKey.export({ type: "spki", format: "der" }).toString("base64");
  const signingPublic = signing.publicKey.export({ type: "spki", format: "der" }).toString("base64");
  const suffix = mode === "esm" ? "mjs" : "js";
  const source = readFileSync(new URL(`../node_modules/@copilotkit/license-verifier/dist/index.${suffix}`, import.meta.url), "utf8");
  const anchor = `const baked = ${JSON.stringify(installed.getMasterPublicKey())};`;
  assert.equal(source.split(anchor).length, 2, "test fixture replaces exactly one public trust anchor");
  // Only this disposable test copy trusts the generated key; installed code and keys stay untouched.
  const fixtureSource = source.replace(anchor, `const baked = ${JSON.stringify(masterPublic)};`)
    .replace('from "zod/v4"', `from ${JSON.stringify(import.meta.resolve("zod/v4"))}`)
    .replace('require("zod/v4")', `require(${JSON.stringify(require.resolve("zod/v4"))})`);
  const prefix = join(tmpdir(), "xiaohe-license-");
  const folder = mkdtempSync(prefix);
  const file = join(folder, mode === "esm" ? "fixture.mjs" : "fixture.cjs");
  writeFileSync(file, fixtureSource);
  try {
    const api = mode === "cjs" ? require(file) : await import(pathToFileURL(file).href);
    assert.deepEqual(Object.keys(api).sort(), Object.keys(installed).sort());
    const keyId = "synthetic-test-key";
    const attestation = { keyId, publicKey: signingPublic,
      attestationSig: sign(null, Buffer.from(`${keyId}:${signingPublic}`), master.privateKey).toString("base64") };
    assert.equal(api.verifyKeyAttestation(attestation), true);
    assert.equal(api.addRuntimeKeyAttestation({ ...attestation, attestationSig: "AAAA" }), false);
    assert.equal(api.addRuntimeKeyAttestation(attestation), true);
    const payload = { version: 1, license_id: "synthetic", key_id: keyId, telemetry_id: "synthetic",
      owner: { org_id: "synthetic", org_name: "fixture", contact_email: "fixture@example.invalid" },
      issued_at: new Date().toISOString(), expires_at: new Date(Date.now() + 86400000).toISOString(), tier: "pro",
      seat_limit: 1, features: { "sdk.angular": true, "threads.max_count": 2, unknown: true }, remove_branding: false };
    const token = (body = payload, kid = keyId) => {
      const input = Buffer.from(JSON.stringify({ alg: "EdDSA", typ: "LIC", kid })).toString("base64url") + "." +
        Buffer.from(JSON.stringify(body)).toString("base64url");
      return input + "." + sign(null, Buffer.from(input), signing.privateKey).toString("base64url");
    };
    const validToken = token();
    const valid = api.verifyLicense(validToken);
    assert.equal(valid.valid, true); assert.equal(valid.error, null);
    assert.equal(api.isFeatureEnabled(valid.license, "sdk.angular"), true);
    assert.equal(api.isFeatureEnabled(valid.license, "unknown"), false);
    assert.equal(api.getFeatureLimit(valid.license, "threads.max_count"), 2);
    const checker = api.createLicenseChecker(validToken);
    const overlay = checker.withOrganizationEntitlement(api.parseOrganizationEntitlement({ active: true, features: { "threads.max_count": 0 } }));
    assert.equal(overlay.getFeatureLimit("threads.max_count"), 0);
    assert.equal(api.getFeatureLimit(checker.getStatus().license, "threads.max_count"), 2);
    const parts = validToken.split("."); const corrupt = Buffer.from(parts[2]!, "base64url"); corrupt[0] ^= 1;
    assert.equal(api.verifyLicense(parts.slice(0, 2).join(".") + "." + corrupt.toString("base64url")).error, "invalid_signature");
    assert.equal(api.verifyLicense(token({ ...payload, key_id: "unknown" }, "unknown")).error, "unknown_key");
    assert.equal(api.verifyLicense(token(payload, "different")).error, "key_mismatch");
    assert.equal(api.verifyLicense(token({ ...payload, expires_at: new Date(Date.now() - 10 * 86400000).toISOString() })).error, "expired");
    assert.equal(api.verifyLicense(token({ ...payload, features: { "sdk.angular": true, "threads.max_count": -1, unknown: true } })).error, "parse_error");
    api.clearRuntimeKeys(); assert.equal(api.verifyLicense(validToken).error, "unknown_key");
    assert.equal(readFileSync(new URL(`../node_modules/@copilotkit/license-verifier/dist/index.${suffix}`, import.meta.url), "utf8"), source);
  } finally {
    assert.ok(resolve(folder).startsWith(resolve(prefix)));
    rmSync(folder, { recursive: true, force: true });
  }
});
