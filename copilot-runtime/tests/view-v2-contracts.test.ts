import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";

const schema = JSON.parse(readFileSync(new URL("../contracts/xiaohe-agui-v2.schema.json", import.meta.url), "utf8"));
const fixtures = JSON.parse(readFileSync(new URL("../../tests/xiaohe/contracts/view_v2.json", import.meta.url), "utf8"));

test("V2 offline fixtures share the strict machine contract", () => {
  const validator = new Validator(schema, "2020-12", true);
  for (const [name, fixture] of Object.entries(fixtures)) {
    assert.equal(validator.validate(fixture).valid, true, name);
  }
  for (const value of [
    { ...fixtures.snapshot, version: 1 },
    { ...fixtures.snapshot, csrf: { ...fixtures.snapshot.csrf, private: true } },
    { ...fixtures.snapshot, messages: Array.from({ length: 11 }, (_, id) => ({ ...fixtures.baseline.message, id: `M${id}` })) },
    { ...fixtures.snapshot, sync: { eventCursor: null, watermarkSequence: 1 } },
    { ...fixtures.snapshot, history: { ...fixtures.snapshot.history, hasMore: true } },
    { ...fixtures.snapshot, history: { ...fixtures.snapshot.history, historyEpoch: Number.MAX_SAFE_INTEGER + 1 } },
    { ...fixtures.restore, expectedIdentity: null },
    { ...fixtures.restore, intent: "login" },
    { ...fixtures.bootstrap, expectedIdentity: null },
    { ...fixtures.event, sequence: 0 },
    { ...fixtures.event, sequence: "1" },
    { ...fixtures.event, historyEpoch: -1 },
    { ...fixtures.public, csrf: fixtures.snapshot.csrf },
    { ...fixtures.ready, value: { ...fixtures.ready.value, watermarkSequence: Number.MAX_SAFE_INTEGER + 1 } },
  ]) assert.equal(validator.validate(value).valid, false, JSON.stringify(value));
});
