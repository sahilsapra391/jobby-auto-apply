// Every status and action a control sends is one a queue carries to the device.
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
//
// **There are three namespaces and this file read two of them.** A `<Act>`
// body can carry a `status`, an `action` or an `event`, and "Confirm
// submitted" sends `event: "completed"`. So the fourth stranded control was
// sitting inside the subject of the guard written to find stranded controls,
// invisible because the walk never asked about its key. Same shape as
// `GATE_PATHS` naming the caller and not the writer: the check was correct
// about what it looked at, and what it looked at was two thirds of the
// surface.

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

/**
 * Every `status:` or `action:` string literal inside a JSX `body={{...}}`
 * attribute.
 *
 * **Scoped to `body`, because `action` is two different namespaces.** The
 * Profile page's controls send `action: "save" | "delete" | "dismiss"` to
 * `/api/profile`, where the shared planner in `deploy/profile_edits.mjs`
 * validates them; the lane controls send theirs to `/api/applications` through
 * `<Act body={{...}}>`. An unscoped walk reported all five Profile actions as
 * stranded work orders, which is the region problem this repository has three
 * afternoons in: the same word means different things in different parts of
 * the tree, and a scanner that does not know which region it is in reports
 * confidently on the wrong one.
 */
function sentValues(file, key) {
  const tree = ts.createSourceFile(
    file, readFileSync(file, 'utf8'), ts.ScriptTarget.ES2022, true);
  const found = new Set();
  const collect = (node) => {
    if (ts.isPropertyAssignment(node)
        && node.name && node.name.getText() === key
        && ts.isStringLiteral(node.initializer)) {
      found.add(node.initializer.text);
    }
    node.forEachChild(collect);
  };
  const walk = (node) => {
    if (ts.isJsxAttribute(node) && node.name.getText() === 'body'
        && node.initializer) {
      collect(node.initializer);
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return found;
}

function queueMap(name) {
  const src = readFileSync(join(UI, 'queue.ts'), 'utf8');
  const tree = ts.createSourceFile('queue.ts', src, ts.ScriptTarget.ES2022, true);
  const out = {};
  const walk = (node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText() === name
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
const actions = new Set();
const events = new Set();
for (const name of readdirSync(UI)) {
  if (!name.endsWith('.tsx')) continue;
  for (const s of sentValues(join(UI, name), 'status')) sent.add(s);
  for (const a of sentValues(join(UI, name), 'action')) actions.add(a);
  for (const e of sentValues(join(UI, name), 'event')) events.add(e);
}
const carried = queueMap('STATUS_QUEUE');
const carriedActions = queueMap('ACTION_QUEUE');
const carriedEvents = queueMap('EVENT_QUEUE');

/**
 * The keys a `<Act body={{...}}>` can carry that the route acts on.
 *
 * Derived from the route rather than listed here, because a list beside the
 * thing it describes is how this file came to be reading two namespaces out of
 * three. A key the route reads and this file does not walk is the whole defect.
 */
function routeKeys() {
  const src = readFileSync(
    join(REPO, 'apps/web/app/api/applications/route.ts'), 'utf8');
  const tree = ts.createSourceFile('route.ts', src, ts.ScriptTarget.ES2022, true);
  const keys = new Set();
  const walk = (node) => {
    // `type Body = { applicationId?: string; action?: string; ... }`
    if (ts.isTypeAliasDeclaration(node) && node.name.getText() === 'Body'
        && ts.isTypeLiteralNode(node.type)) {
      for (const member of node.type.members) {
        if (ts.isPropertySignature(member) && member.name) {
          keys.add(member.name.getText());
        }
      }
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return keys;
}

test('the scan finds the statuses the controls send', () => {
  // The anti-vacuity floor. Every check below passes on an empty set, and this
  // file's whole subject is a status that was sent and not carried.
  assert.ok(sent.size >= 2,
    `only ${sent.size} statuses found in the lane controls, so the checks ` +
    `below are about almost nothing. The walker is probably not reaching the ` +
    `body props.`);
  assert.ok(Object.keys(carried).length >= 1, 'STATUS_QUEUE parsed as empty');
  assert.ok(actions.size >= 1,
    'no action props found in the lane controls, so the action check below ' +
    'is about nothing');
  assert.ok(Object.keys(carriedActions).length >= 1, 'ACTION_QUEUE parsed as empty');
  assert.ok(events.size >= 1,
    'no event props found in the lane controls, so the event check below is ' +
    'about nothing');
  assert.ok(Object.keys(carriedEvents).length >= 1, 'EVENT_QUEUE parsed as empty');
});

test('every key the route acts on is one this file walks', () => {
  // The guard on the guard. This file found three stranded controls and missed
  // a fourth because it walked `status` and `action` and the fourth sent an
  // `event`. Nothing about that omission was visible from inside the file: it
  // passed, in a suite that passed, about a surface it did not cover.
  //
  // `phase` is the exception and it is a real one: the daemon writes it, no
  // control sends it, and the route reads it so a daemon-originated write can
  // set it. It carries its reason here rather than being quietly skipped.
  const WALKED = new Set(['status', 'action', 'event']);
  const NOT_A_CONTROL_SURFACE = {
    applicationId: 'the subject, not a decision about it',
    postingId: 'the Found path, checked by DECISION_QUEUE below',
    decision: 'the Found path, checked by DECISION_QUEUE below',
    retry: 'a flag on the apply order a status already makes, not an order of ' +
           'its own; what it reaches is checked by ' +
           'tests/v2_retry_reaches_the_device_as_a_retry.test.mjs',
    phase: 'written by the daemon reporting its own progress; no control ' +
           'sends one, and a control that did would be ordering a state ' +
           'rather than work, which is the mistake this whole file is about',
  };
  const unwalked = [...routeKeys()].filter(
    (k) => !WALKED.has(k) && !(k in NOT_A_CONTROL_SURFACE));
  assert.deepEqual(unwalked, [],
    `the route acts on these body keys and nothing here walks them, so a ` +
    `control sending one could be stranded exactly as Confirm submitted was: ` +
    `${unwalked.join(', ')}`);
  assert.ok(routeKeys().size >= 4, 'the Body type parsed as almost nothing');
});

test('every event a control sends reaches the device', () => {
  // **An event is a claim about what happened, and it has to reach the store
  // that holds the claims.** "Confirm submitted" wrote an `application_events`
  // row and returned 200 while `history.jsonl` learned nothing, so the
  // duplicate guard would have offered the role again and the board would have
  // left the card in Needs You.
  const stranded = [...events].filter((e) => !(e in carriedEvents));
  assert.deepEqual(stranded, [],
    `these events are sent by a control and are in no queue, so the device ` +
    `never records them: ${stranded.join(', ')}`);
});

test('every action a control sends reaches the device', () => {
  // **An action is not a status and gets its own map.** "Open & finish" moves
  // no state: it asks the device to put a window on the screen. Carrying it as
  // a fourth status would mean inventing a state for an instruction, and then
  // writing that state into the Class B column the device owns.
  const stranded = [...actions].filter((a) => !(a in carriedActions));
  assert.deepEqual(stranded, [],
    `these actions are sent by a control and are in no queue, so pressing the ` +
    `control tells the device nothing: ${stranded.join(', ')}`);
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
