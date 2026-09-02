// The event kinds are written twice, in two languages, so they are compared.
//
// `apps/worker/src/channel.mjs` refuses a kind it does not know, and
// `apps/web/app/_ui/events.ts` is what the write path checks against before it
// publishes. Two lists of strings that must agree is exactly the shape
// `LogoContractTests` exists for: one gets edited, the other does not, and the
// symptom is a write that commits and an event that never fans out.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { KINDS } from "../apps/worker/src/channel.mjs";

/** The TypeScript list, read as text: this file cannot import a .ts module. */
function webKinds() {
  const source = readFileSync(
    new URL("../apps/web/app/_ui/events.ts", import.meta.url), "utf8");
  const block = source.slice(source.indexOf("new Set(["),
                             source.indexOf("]);"));
  return new Set([...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]));
}

test("both sides know the same kinds", () => {
  const web = webKinds();
  assert.ok(web.size > 0, "no kinds parsed from events.ts; the check is vacuous");
  assert.deepEqual([...web].sort(), [...KINDS].sort());
});

test("neither side still knows the superseded name", () => {
  assert.ok(!KINDS.has("job_decision.changed"));
  assert.ok(!webKinds().has("job_decision.changed"),
    "job_decision.changed keyed the realtime layer on a projection that was " +
    "never finished");
});

test("a posting leaving Found is its own kind", () => {
  assert.ok(KINDS.has("posting.decided"),
    "Found is an absence and has no from, so entering the exclusion set " +
    "cannot be an application.changed");
  assert.ok(webKinds().has("posting.decided"));
});
