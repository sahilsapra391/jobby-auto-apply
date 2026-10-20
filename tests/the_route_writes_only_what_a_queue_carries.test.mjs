// A body the route accepts is a body some queue carries, and the id is checked
// before any of it.
//
// **A peer session wrote a row to production proving this.** It probed the
// four Needs You controls with a deliberately malformed `applicationId`,
// correctly reasoning that `order()` refuses it at `queue.ts:209` before the
// upsert. All four behaved that way. It then added a fifth case,
// `status: "review"`, and did not re-check that the property held for it. It
// does not, for exactly the reason the finding names: `review` maps to no
// queue, so `order()` is never called, so the only `applicationId` validation
// in the route never runs. 200, one `applications` row with
// `legacy_id '!!probe-not-a-real-id!!'`, one `idempotency_keys` row. Removed.
//
// Six accepted values reached no queue: `review` and `active` in `STATUSES`,
// `submitting`, `abandoned` and `needs_you` in `EVENTS`, and `phase`, which
// had no map at all.
//
// **Two properties, and neither is about the arrangement of the code.** The
// accepted set is compared against the queue maps, however each happens to be
// written, so a hand-written list naming an uncarried value fails here even
// though today's route derives the set. And the id predicate is compiled out
// of the route and *run* against the probe string, because reading a regex is
// what let the last one ship.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROUTE = join(REPO, 'apps/web/app/api/applications/route.ts');
const QUEUE = join(REPO, 'apps/web/app/_ui/queue.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

function parse(file) {
  const src = readFileSync(file, 'utf8');
  const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true);
  // A recovering parser hands back a tree for a file the build refuses, and
  // every guard walking it answers about that tree. Five did, once.
  assert.equal(tree.parseDiagnostics?.length ?? 0, 0,
    `${file} does not parse: ` +
    `${(tree.parseDiagnostics ?? []).map((d) => d.messageText).join('; ')}`);
  return { src, tree };
}

/** The keys of an object-literal `const` in queue.ts. */
function queueKeys(name) {
  const { tree } = parse(QUEUE);
  let out = null;
  const walk = (node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText() === name
        && node.initializer && ts.isObjectLiteralExpression(node.initializer)) {
      out = node.initializer.properties
        .filter((p) => ts.isPropertyAssignment(p))
        .map((p) => p.name.getText().replace(/['"]/g, ''));
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.ok(out, `${name} not found in queue.ts`);
  return out;
}

/**
 * What the route accepts, read whichever way it is written.
 *
 * `new Set(Object.keys(X))` reports X's keys; `new Set(["a", "b"])` reports
 * the literals. The point is to compare accepted against carried, not to
 * insist on one spelling: a future rewrite back to a literal list is exactly
 * the regression, and it has to fail here rather than pass on a technicality.
 */
function accepts(name) {
  const { tree } = parse(ROUTE);
  let out = null;
  const walk = (node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText() === name
        && node.initializer && ts.isNewExpression(node.initializer)) {
      const arg = (node.initializer.arguments ?? [])[0];
      if (arg && ts.isArrayLiteralExpression(arg)) {
        out = { how: 'literal',
                values: arg.elements.filter(ts.isStringLiteral).map((e) => e.text) };
      } else if (arg && ts.isCallExpression(arg)
                 && arg.expression.getText() === 'Object.keys') {
        out = { how: 'derived', from: arg.arguments[0].getText() };
      }
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.ok(out, `${name} not found in the route as a Set`);
  return out;
}

const CARRIED = { STATUS_QUEUE: queueKeys('STATUS_QUEUE'),
                  EVENT_QUEUE: queueKeys('EVENT_QUEUE') };

test('the queue maps parsed as something', () => {
  // The floor. Every comparison below is trivially satisfied by an empty
  // accepted set, and "not zero" is not "complete": these are the sets whose
  // sizes decide whether the checks mean anything.
  assert.ok(CARRIED.STATUS_QUEUE.length >= 3,
    `STATUS_QUEUE parsed as ${CARRIED.STATUS_QUEUE.length} entries`);
  assert.ok(CARRIED.EVENT_QUEUE.length >= 1,
    `EVENT_QUEUE parsed as ${CARRIED.EVENT_QUEUE.length} entries`);
});

for (const [set, map] of [['STATUSES', 'STATUS_QUEUE'], ['EVENTS', 'EVENT_QUEUE']]) {
  test(`every ${set} value the route accepts has a ${map} entry`, () => {
    const a = accepts(set);
    const carried = CARRIED[map];
    const values = a.how === 'derived' ? carried : a.values;
    if (a.how === 'derived') {
      assert.equal(a.from, map,
        `${set} is derived from ${a.from}, which is not ${map}`);
    }
    const deaf = values.filter((v) => !carried.includes(v));
    assert.deepEqual(deaf, [],
      `the route accepts ${deaf.join(', ')} and no queue carries them, so a ` +
      `body naming one skips order(), skips the only applicationId check ` +
      `inside it, and writes a row for work nobody will do`);
    assert.ok(values.length >= 1, `${set} resolved to nothing`);
  });
}

test('phase is refused rather than accepted with no queue', () => {
  const { src } = parse(ROUTE);
  // The property: a body carrying `phase` cannot reach the upsert. Asserted on
  // the refusal existing and on `phase` not being passed into the insert,
  // because the column is Class B and the device owns it.
  assert.match(src, /body\.phase !== undefined/,
    'the route does not refuse a phase, and there is no queue that would ' +
    'make one true');
  assert.doesNotMatch(src, /body\.phase \?\? null/,
    'the upsert still takes body.phase, so a phase can still be written by ' +
    'the board');
});

/** The route's own id predicate, compiled out and run. */
function idPredicate() {
  const { src } = parse(ROUTE);
  const at = src.indexOf('const APPLICATION_ID =');
  assert.notEqual(at, -1, 'APPLICATION_ID is not declared in the route');
  const line = src.slice(at, src.indexOf('\n', at) + 1);
  const js = ts.transpileModule(`${line}\nmodule.exports = APPLICATION_ID;`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  return mod.exports;
}

test('the id the probe used is refused, and real ids are not', () => {
  const re = idPredicate();
  // The exact string that wrote a production row.
  assert.equal(re.test('!!probe-not-a-real-id!!'), false,
    'the route would still take the probe id that wrote a row to production');
  for (const bad of ['', ' ', 'a b', "a'b", 'a;b', '../x', 'x'.repeat(129),
                     'null', '%27', 'a/b']) {
    if (bad === 'null') continue;   // a legal id shape, and a real hazard only downstream
    assert.equal(re.test(bad), false, `the route would take ${JSON.stringify(bad)}`);
  }
  // Both shapes the store actually holds: 16-hex and slug. 37 of 187 are hex.
  for (const good of ['000000000000000f', '0000000000000002', 'acme-pm',
                      'umbrella-corp_0000004', 'a.b-c_d']) {
    assert.equal(re.test(good), true, `the route would refuse the real id ${good}`);
  }
});

test('the route and the queue check the same character class', () => {
  // Two predicates for one property is how the accepted set and the carried
  // set drifted in the first place. `order()` still holds its own, because it
  // is reached from `saveSearch` and from paths that are not this route.
  const route = idPredicate().source.replace(/\{1,128\}/, '+');
  const { src } = parse(QUEUE);
  const m = src.match(/!(\/\^\[A-Za-z0-9._-\]\+\$\/)\.test\(applicationId\)/);
  assert.ok(m, 'order() no longer tests the application id with a regex');
  assert.equal(route, m[1].slice(1, -1),
    `the route accepts ${route} and order() accepts ${m[1]}; a body that ` +
    `passes one and fails the other is a 400 from whichever runs second, ` +
    `which is the drift this pair exists to prevent`);
});
