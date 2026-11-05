import assert from "node:assert/strict";
import { readFileSync, realpathSync } from "node:fs";
import { createRequire, registerHooks } from "node:module";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const manifest = createRequire(import.meta.url).resolve("zod/package.json");
const root = realpathSync(dirname(manifest));
const pkg = JSON.parse(readFileSync(manifest, "utf8")) as {
  version: string; exports: Record<string, { import: string; require: string }>;
};
assert.equal(pkg.version, "4.6.7", "Runtime requires the pinned Zod v4 parser");

// Nested Zod3 packages bundle old v4; redirect only explicit public v4 imports.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (!/^zod\/v4(?:\/|$)/.test(specifier)) return nextResolve(specifier, context);
    const key = "." + specifier.slice(3);
    const exported = Object.keys(pkg.exports).find(name => name === key ||
      (name.endsWith("*") && key.startsWith(name.slice(0, -1))));
    assert.ok(exported, "only official Zod v4 exports are allowed");
    const suffix = exported.endsWith("*") ? key.slice(exported.length - 1) : "";
    assert.ok(!suffix.includes("\\") && !suffix.split("/").some(part =>
      part === "." || part === ".." || part.toLowerCase() === "node_modules"));
    const branch = Array.from(context.conditions).includes("require") ? "require" : "import";
    const target = realpathSync(resolve(root, pkg.exports[exported]![branch].replace("*", suffix)));
    const inside = relative(root, target);
    assert.ok(!inside.startsWith("..") && !isAbsolute(inside), "Zod export must remain inside the pinned package");
    return { url: pathToFileURL(target).href, shortCircuit: true };
  },
});
