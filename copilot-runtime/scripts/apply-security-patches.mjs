import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const hash = value => createHash('sha256').update(value).digest('hex');
const once = (source, before, after) => {
  assert.equal(source.split(before).length, 2, 'security patch anchor must match exactly once');
  return source.replace(before, after);
};

export function patchZod3(source) {
  for (const name of ['ZodArray', 'ZodSet', 'ZodTuple']) {
    const start = source.indexOf(`class ${name} extends`);
    assert.ok(start >= 0);
    const end = source.indexOf('\n}\n', start) + 3;
    assert.ok(end > start);
    let body = source.slice(start, end);
    const invalid = body.match(/return ([\w.]*INVALID);/)?.[1];
    assert.ok(invalid);
    const early = `return ${invalid}; // xiaohe-security: reject existing upper bound before element work`;
    if (name === 'ZodArray') {
      const exact = '                status.dirty();\n            }\n        }\n        if (def.minLength !== null)';
      body = once(body, exact, exact.replace('status.dirty();', `status.dirty();\n                if (tooBig) ${early}`));
      const max = '                status.dirty();\n            }\n        }\n        if (ctx.common.async)';
      body = once(body, max, max.replace('status.dirty();', `status.dirty();\n                ${early}`));
    } else if (name === 'ZodSet') {
      const max = '                status.dirty();\n            }\n        }\n        const valueType = this._def.valueType;';
      body = once(body, max, max.replace('status.dirty();', `status.dirty();\n                ${early}`));
    } else {
      const max = '            status.dirty();\n        }\n        const items = [...ctx.data]';
      body = once(body, max, max.replace('status.dirty();', `status.dirty();\n            ${early}`));
    }
    source = source.slice(0, start) + body + source.slice(end);
  }
  return source;
}

export function externalizeLicense(source, format) {
  const start = source.indexOf('//#region ../../node_modules/.pnpm/zod@4.3.6/');
  const end = source.indexOf('//#region ../../libs/license-catalog/src/index.ts');
  assert.ok(start >= 0 && end > start, 'only the pinned embedded Zod region may be replaced');
  const bindings = format === 'index.mjs'
    ? 'import { record, string, union, boolean, number, object, literal, enum as _enum } from "zod/v4";'
    : 'const { record, string, union, boolean, number, object, literal, enum: _enum } = require("zod/v4");';
  return source.slice(0, start) + bindings + '\n\n' + source.slice(end);
}

function main() {
  const root = new URL('../', import.meta.url);
  const profiles = JSON.parse(readFileSync(new URL('./security-patches.json', import.meta.url), 'utf8'));
  const lock = JSON.parse(readFileSync(new URL('package-lock.json', root), 'utf8'));
  assert.equal(lock.packages['node_modules/zod'].version, '4.6.7', 'externalized licensing requires the pinned safe root');
  assert.equal(lock.packages['node_modules/@copilotkit/license-verifier'].version, '0.5.0', 'locked license version drift');
  const zodRoots = Object.keys(lock.packages).filter(path => path === 'node_modules/zod' || path.endsWith('/node_modules/zod'));
  assert.equal(zodRoots.length, 5, 'review security coverage when the locked Zod tree changes');
  const targets = zodRoots.flatMap(path => ['types.js', 'types.cjs'].map(file => ({ path: `${path}/v3/${file}`,
    packageRoot: path, version: lock.packages[path].version, profile: `zod@${lock.packages[path].version}/${file}`, transform: patchZod3 })));
  for (const file of ['index.mjs', 'index.js']) targets.push({ path: `node_modules/@copilotkit/license-verifier/dist/${file}`,
    packageRoot: 'node_modules/@copilotkit/license-verifier', version: '0.5.0', profile: `license-verifier@0.5.0/${file}`,
    transform: source => externalizeLicense(source, file) });
  const pending = [];
  const checkOnly = process.argv.includes('--check');
  for (const target of targets) {
    const version = JSON.parse(readFileSync(new URL(`${target.packageRoot}/package.json`, root), 'utf8')).version;
    assert.equal(version, target.version, `security patch version drift: ${target.packageRoot}`);
    const profile = profiles[target.profile];
    assert.ok(profile, `unreviewed security patch profile: ${target.profile}`);
    const file = new URL(target.path, root);
    const source = readFileSync(file, 'utf8');
    const actual = hash(source);
    if (actual === profile.after) continue;
    assert.equal(actual, profile.before, `security patch content drift: ${target.path}`);
    assert.ok(!checkOnly, `security patch missing: ${target.path}; run npm run patch:security`);
    const patched = target.transform(source);
    assert.equal(hash(patched), profile.after, `security patch output mismatch: ${target.path}`);
    pending.push({ file, patched });
  }
  // An unknown file/version fails above before any dependency file is changed.
  for (const { file, patched } of pending) writeFileSync(file, patched);
  console.log(JSON.stringify({ patchId: 'xiaohe-dependencies-20261105', verified: targets.length, applied: pending.length, checkOnly }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main();
