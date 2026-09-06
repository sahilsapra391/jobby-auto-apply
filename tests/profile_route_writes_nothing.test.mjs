// The Profile write route enqueues, and writes nothing else.
//
// **This is parity's premise, one route over.** An answer lives in
// `answers.json` on the device and reaches `answer_bank` through the Class B
// mirror; the resume registry reaches `profiles` the same way. The device is
// the only writer of both, and parity's whole question is whether the mirror
// lost or altered anything on the way across. A route that also wrote those
// tables would put a second writer inside the thing being compared -- and
// parity would then be unable to see it, because a divergence it caused itself
// looks exactly like the store and the mirror agreeing about something wrong.
//
// The same reasoning already sits in `edit_outcomes`: the device writes
// `pending` on claim rather than the board writing it on enqueue, because the
// alternative made that the one dual-written table and needed a permanent
// parity exemption on the table whose job is the truth about writes.
//
// **A narrow check, and it says so.** The real guarantee is parity's own seven
// green days; this is the fast local echo of it, and it can only see a write
// spelled out in this file. It parses rather than greps, for the reason three
// separate afternoons here were lost to: a `pool(` inside a comment explaining
// why there is no `pool(` would satisfy any pattern.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROUTE = join(REPO, 'apps/web/app/api/profile/route.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

const tree = ts.createSourceFile(
  ROUTE, readFileSync(ROUTE, 'utf8'), ts.ScriptTarget.ES2022, true);

/**
 * Every function this module calls, and every name it imports.
 *
 * **The callback returns nothing, deliberately.** `forEachChild` stops
 * traversing the moment its callback returns anything truthy -- it is written
 * for early-exit searches -- so a walker whose callback returns the
 * accumulator visits exactly one child and stops. The first version of this
 * file did that, and both of its checks passed against a route that could have
 * been full of `pool()`. A scanner that reads one node of the tree it claims to
 * read is this repository's oldest failure in a new language.
 */
function walk(node, found) {
  if (ts.isCallExpression(node)) {
    const target = node.expression;
    if (ts.isIdentifier(target)) found.calls.add(target.text);
    else if (ts.isPropertyAccessExpression(target)) found.calls.add(target.name.text);
  }
  if (ts.isImportDeclaration(node) && node.importClause?.namedBindings
      && ts.isNamedImports(node.importClause.namedBindings)) {
    for (const el of node.importClause.namedBindings.elements) {
      found.names.add(el.name.text);
    }
  }
  node.forEachChild((child) => { walk(child, found); });
  return found;
}

const { calls: called, names } = walk(tree, { calls: new Set(), names: new Set() });

test('the scan reaches the whole file, or it is measuring one node', () => {
  // The anti-vacuity floor, calibrated against the file rather than against
  // emptiness: "not zero" would have passed the broken walker too, which found
  // three names. Every one of these is a call the route genuinely makes, deep
  // inside the handler where a short-circuiting walk never reaches.
  for (const name of ['order', 'remember', 'replay', 'requireWritesEnabled']) {
    assert.ok(called.has(name),
      `the walker did not reach ${name}(), so nothing else it reports is ` +
      `evidence of anything`);
  }
});

test('the route holds no database handle of its own', () => {
  // `pool()` is how every other route reaches Postgres. Not importing it is
  // what makes "writes nothing" checkable from here rather than from the
  // database afterwards.
  assert.equal(names.has('pool'), false,
    'the Profile write route imports pool(), so it can write answer_bank or ' +
    'profiles directly. The device is the only writer of both; see the ' +
    'header of this file.');
  assert.equal(called.has('pool'), false, 'the route calls pool()');
  assert.equal(called.has('query'), false, 'the route runs its own SQL');
});

test('and it does reach the queue, which is the whole of the write', () => {
  // The half that fails in the other direction: a route that writes nothing
  // and enqueues nothing is not safe, it is broken, and every check above
  // would pass on an empty file.
  assert.ok(called.has('order'),
    'the route never calls order(), so nothing reaches the daemon');
  assert.ok(names.has('planAnswerEdit') && names.has('planResumeEdit'),
    'the route validates edits some other way than the shared planner, ' +
    'which is how the board and v2 come to disagree about what a valid ' +
    'edit is while both writing the same queue');
});

test('the enqueue precedes nothing it could half-do', () => {
  // `remember` stores the idempotency answer and is the only write here. It
  // has to come after the enqueue: a key remembered for work that never
  // reached the queue answers a retry with a success nobody earned.
  const src = readFileSync(ROUTE, 'utf8');
  const withoutComments = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '');
  assert.ok(withoutComments.indexOf('await order(')
            < withoutComments.indexOf('await remember('),
    'remember() runs before the enqueue, so a retry can be answered with a ' +
    'success for work the queue never took');
});
