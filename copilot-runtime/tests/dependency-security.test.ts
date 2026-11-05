import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
const roots = Object.keys(lock.packages).filter(path => /(?:^|\/)node_modules\/zod$/.test("/" + path));

for (const root of roots) for (const mode of ["esm", "cjs"] as const) {
  test(`actual ${root}/v3 ${mode} rejects oversized bounded collections before element work`, async () => {
    const manifest = new URL("../" + root + "/package.json", import.meta.url);
    const fromPackage = createRequire(manifest);
    const api = mode === "cjs" ? fromPackage("./v3/index.cjs") : await import(pathToFileURL(fromPackage.resolve("./v3/index.js")).href);
    const { z } = api;
    if (lock.packages[root].version === "3.25.76") {
      const rootApi = mode === "cjs" ? fromPackage("./index.cjs") : await import(pathToFileURL(fromPackage.resolve("./index.js")).href);
      assert.equal(rootApi.z.ZodArray, z.ZodArray, "legacy root and /v3 must execute the same patched parser");
    }
    for (const kind of ["array-max", "array-length", "set-max", "set-size"]) {
      let visits = 0;
      const item = z.string().refine(() => { visits++; return true; });
      const schema = kind.startsWith("array") ? z.array(item)[kind === "array-max" ? "max" : "length"](2, "synthetic cap")
        : z.set(item)[kind === "set-max" ? "max" : "size"](2, "synthetic cap");
      const value = kind.startsWith("array") ? ["a", "b", "c"] : new Set(["a", "b", "c"]);
      const result = schema.safeParse(value);
      assert.equal(result.success, false);
      assert.equal(visits, 0, `${kind} must reject before visiting elements`);
      assert.equal(result.error.issues.length, 1);
      assert.equal(result.error.issues[0].code, "too_big");
      assert.equal(result.error.issues[0].message, "synthetic cap");
      assert.equal((await schema.safeParseAsync(value)).success, false);
      assert.equal(visits, 0);
      assert.equal(schema.safeParse(kind.startsWith("array") ? ["a", "b"] : new Set(["a", "b"])).success, true);
    }
    let visits = 0;
    const item = z.string().refine(() => { visits++; return true; });
    const short = z.array(item).length(2).safeParse(["a"]);
    assert.equal(short.success, false); assert.equal(short.error.issues[0].code, "too_small"); assert.equal(visits, 1);
    assert.deepEqual(z.array(z.string()).max(2).catch(["fallback"]).parse([1, 2, 3]), ["fallback"]);
    assert.equal(z.array(z.string()).max(2).transform(() => "unexpected").safeParse([1, 2, 3]).success, false);
    const nested = z.object({ items: z.array(z.string()).max(2) }).safeParse({ items: [1, 2, 3] });
    assert.deepEqual(nested.error.issues[0].path, ["items"]);
    let tupleVisits = 0;
    const tupleItem = z.string().refine(() => { tupleVisits++; return true; });
    const tuple = z.tuple([tupleItem, tupleItem]);
    assert.equal(tuple.safeParse(["a", "b", "c"]).success, false);
    assert.equal(tupleVisits, 0, "fixed tuples must reject their existing upper bound before spread/element work");
    assert.equal((await tuple.safeParseAsync(["a", "b", "c"])).success, false); assert.equal(tupleVisits, 0);
    assert.equal(tuple.rest(z.string()).safeParse(["a", "b", "c"]).success, true);
    assert.equal(z.array(z.string()).safeParse(["a", "b", "c"]).success, true, "unbounded schemas remain unchanged");
  });
}

for (const mode of ["esm", "cjs"] as const) test(`actual license verifier ${mode} uses the fixed external v4 without removing licensing`, async () => {
  const api = mode === "cjs" ? require("@copilotkit/license-verifier") : await import("@copilotkit/license-verifier");
  assert.deepEqual(api.organizationEntitlementSchema._zod.version, { major: 4, minor: 6, patch: 7 });
  assert.equal(typeof api.verifyLicense, "function"); assert.equal(typeof api.verifyKeyAttestation, "function");
  assert.equal(api.verifyLicense("synthetic.invalid.token").valid, false);
  const org = api.parseOrganizationEntitlement({ active: true, features: { "sdk.angular": true, "threads.max_count": 3 } });
  const base = api.createLicenseChecker();
  assert.equal(base.checkFeature("sdk.angular"), false);
  const overlay = base.withOrganizationEntitlement(org);
  assert.equal(overlay.checkFeature("sdk.angular"), true);
  assert.equal(overlay.getFeatureLimit("threads.max_count"), 3);
  assert.equal(overlay.checkFeature("unknown"), false);
  assert.equal(base.checkFeature("sdk.angular"), false);
  assert.throws(() => api.parseOrganizationEntitlement({ active: true, features: {}, unexpected: true }));
  assert.throws(() => api.parseOrganizationEntitlement({ active: true, features: { "threads.max_count": -1 } }));
});
