// Every status a control sends is one the queue carries to the device.
//
// **Three buttons sent `status: "inflight"` and none of them reached the
// daemon.** `STATUS_QUEUE` held `dropped` alone, under a comment explaining
// that `review`, `active` and `inflight` are stages the daemon moves an
// application through and a board observes rather than orders. True of the
// daemon's own transitions; false of a control labelled Apply.
//
// So `order` was never called, the route wrote `applications.status` anyway,
// and the board said it had started work on a real application while no
// machine had. Found by parity on one row: session file `watch`, Postgres
// `inflight`, apply queue empty, and a 200 recorded against the POST.
//
// **The check is that the two sides agree, not that a particular value is
// present.** Asserting `inflight` is in the map would pass the moment somebody
// adds a fourth control sending something else, which is the same failure one
// value along. This reads the statuses the controls actually send and requires
// each to be either carried by the queue or named as observational with a
// reason.
//
// It parses rather than greps, for the reason this repository has paid for
// three times: a `status: "inflight"` inside a comment explaining this rule is
// not a control sending one.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const UI = join(REPO, 'apps/web/app/_ui');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/**
 * Statuses a control may send without the queue carrying them, each with the
 * reason it is not a work order. An entry here is a claim that pressing it
 * asks the device for nothing, and that claim is checkable by reading what the
 * device does with the value.
 */
const OBSERVATIONAL = {
  // None today. `review` and `active` are daemon-owned and no control sends
  // them; if one ever does, it belongs here with its reason or in STATUS_QUEUE
  // with its queue, and deciding which is the whole point of this file.
};

/** Every `status: "..."` inside a JSX `body={{...}}` prop, from the tree. */
function statusesSent(file) {
  const tree = ts.createSourceFile(
    file, readFileSync(file, 'utf8'), ts.ScriptTarget.ES2022, true);
  const found = new Set();
  const walk = (node) => {
    if (ts.isPropertyAssignment(node)
        && node.name && node.name.getText() === 'status'
        && ts.isStringLiteral(node.initializer)) {
      found.add(node.initializer.text);
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return found;
}

function queueMap() {
  const src = readFileSync(join(UI, 'queue.ts'), 'utf8');
  const tree = ts.createSourceFile('queue.ts', src, ts.ScriptTarget.ES2022, true);
  const out = {};
  const walk = (node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText() === 'STATUS_QUEUE'
        && node.initializer && ts.isObjectLiteralExpression(node.initializer)) {
      for (const prop of node.initializer.properties) {
        if (ts.isPropertyAssignment(prop) && ts.isStringLiteral(prop.initializer)) {
          out[prop.name.getText().replace(/['"]/g, '')] = prop.initializer.text;
        }
      }
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return out;
}

const sent = new Set();
for (const name of readdirSync(UI)) {
  if (!name.endsWith('.tsx')) continue;
  for (const s of statusesSent(join(UI, name))) sent.add(s);
}
const carried = queueMap();

test('the scan finds the statuses the controls send', () => {
  // The anti-vacuity floor. Every check below passes on an empty set, and this
  // file's whole subject is a status that was sent and not carried.
  assert.ok(sent.size >= 2,
    `only ${sent.size} statuses found in the lane controls, so the checks ` +
    `below are about almost nothing. The walker is probably not reaching the ` +
    `body props.`);
  assert.ok(Object.keys(carried).length >= 1, 'STATUS_QUEUE parsed as empty');
});

test('every status a control sends reaches the device', () => {
  const stranded = [...sent].filter(
    (s) => !(s in carried) && !(s in OBSERVATIONAL));
  assert.deepEqual(stranded, [],
    `these statuses are sent by a control and are in neither STATUS_QUEUE nor ` +
    `OBSERVATIONAL, so pressing the control writes applications.status and ` +
    `tells the daemon nothing: ${stranded.join(', ')}`);
});

test('an observational status says why it asks the device for nothing', () => {
  for (const [status, why] of Object.entries(OBSERVATIONAL)) {
    assert.ok(why && why.length > 40,
      `${status} is exempt from reaching the daemon with no reason worth the ` +
      `name. "the daemon owns it" is the sentence that stranded Apply.`);
    assert.ok(!(status in carried),
      `${status} is both carried and declared observational`);
  }
});

test('a status is never both a work order and an omission', () => {
  const both = Object.keys(carried).filter((s) => s in OBSERVATIONAL);
  assert.deepEqual(both, [], `contradictory: ${both.join(', ')}`);
});
