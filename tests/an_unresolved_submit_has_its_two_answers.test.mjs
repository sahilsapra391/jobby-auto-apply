// A submit nobody has answered is answered from the card, and never retried.
//
// **A real application, 2026-09-12.** The run clicked Submit, no
// confirmation appeared, and the latest event stayed `submitting`. The card sat
// in In flight offering Retry, which the device refuses in exactly that state
// (`watch.do_not_start`), and Stop, which is a drop: it writes no event and the
// submit stays unresolved for ever. Confirm submitted lived only on a Needs You
// card the application never reached. Seven applications in the real store
// had a latest `submitting` with nothing after it.
//
// So three properties, each read off the code that holds it rather than listed:
//
//   the answers exist     an `event: "abandoned"` control sits beside Confirm
//                         submitted, and the event reaches the device through a
//                         queue the device claims
//   Retry yields          every Retry, and Watching's Apply, is on the false
//                         side of a conditional on `unresolved_submit`
//   the route checks      an `abandoned` is refused before anything is queued
//                         unless the latest event is `submitting`
//
// It parses rather than greps, and refuses a file the parser had to recover.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

function parse(rel) {
  const file = join(REPO, rel);
  const tree = ts.createSourceFile(file, readFileSync(file, 'utf8'),
    ts.ScriptTarget.ES2022, true);
  assert.equal(tree.parseDiagnostics?.length ?? 0, 0,
    `${rel} does not parse: ` +
    `${(tree.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; ')}`);
  return tree;
}

/** Every `<Act>` in Rows.tsx: its body literal, and the node itself. */
function acts() {
  const tree = parse('apps/web/app/_ui/Rows.tsx');
  const out = [];
  const walk = (node) => {
    const opening = ts.isJsxSelfClosingElement(node) ? node
      : ts.isJsxElement(node) ? node.openingElement : null;
    if (opening && opening.tagName.getText() === 'Act') {
      const attrs = opening.attributes.properties.filter(ts.isJsxAttribute);
      const get = (name) => attrs.find((a) => a.name.getText() === name);
      const cls = get('className');
      const body = get('body');
      const expr = body?.initializer && ts.isJsxExpression(body.initializer)
        ? body.initializer.expression : null;
      const props = {};
      if (expr && ts.isObjectLiteralExpression(expr)) {
        for (const p of expr.properties) {
          if (!ts.isPropertyAssignment(p)) continue;
          const init = p.initializer;
          props[p.name.getText()] = ts.isStringLiteral(init) ? init.text
            : init.kind === ts.SyntaxKind.TrueKeyword ? true : init.getText();
        }
      }
      out.push({
        node,
        className: cls?.initializer && ts.isStringLiteral(cls.initializer)
          ? cls.initializer.text : '',
        props,
        line: tree.getLineAndCharacterOfPosition(opening.getStart()).line + 1,
      });
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return out;
}

/**
 * Whether `node` sits in the false branch of a conditional whose condition
 * reads `unresolved_submit`. The walk goes up through every ancestor, so a
 * fragment or a wrapper between the two does not hide it.
 */
function yieldsToAnUnresolvedSubmit(node) {
  let child = node;
  for (let at = node.parent; at; child = at, at = at.parent) {
    if (ts.isConditionalExpression(at)
        && /\bunresolved_submit\b/.test(at.condition.getText())
        && at.whenFalse === child) {
      return true;
    }
  }
  return false;
}

const ACTS = acts();

test('the walk reaches the lane controls', () => {
  // The floor. Every check below passes on an empty list.
  assert.ok(ACTS.length >= 10, `found ${ACTS.length} Act controls in Rows.tsx`);
});

test('"It did not go through" exists beside Confirm submitted', () => {
  const unsent = ACTS.filter((a) => a.props.event === 'abandoned');
  assert.ok(unsent.length >= 1, 'no control sends event: "abandoned"');
  for (const a of unsent) {
    // Not styled or named as a Retry: the Retry guard reads that class, and
    // this control orders no run.
    assert.doesNotMatch(a.className, /\bretry\b/,
      `Rows.tsx:${a.line} is classed as a Retry and orders no run`);
    assert.equal(a.props.status, undefined,
      `Rows.tsx:${a.line} also sends a status, so it orders work beside the answer`);
    // A sibling in the same component: whoever is offered one answer is
    // offered the other.
    let scope = a.node.parent;
    while (scope && !ts.isJsxFragment(scope) && !ts.isJsxElement(scope)) scope = scope.parent;
    const siblings = ACTS.filter((b) => b.node.parent === scope || b.node.parent === a.node.parent);
    assert.ok(siblings.some((b) => b.props.event === 'completed'),
      `Rows.tsx:${a.line} offers "It did not go through" without Confirm submitted`);
  }
});

test('the event reaches a queue the device claims', () => {
  const queueTs = readFileSync(join(REPO, 'apps/web/app/_ui/queue.ts'), 'utf8');
  const block = queueTs.slice(queueTs.indexOf('export const EVENT_QUEUE'));
  const m = block.match(/abandoned:\s*"([^"]+)"/);
  assert.ok(m, 'EVENT_QUEUE carries no abandoned entry');
  const queue = m[1];

  const kv = readFileSync(join(REPO, 'deploy/kvqueue.mjs'), 'utf8');
  const list = kv.slice(kv.indexOf('export const QUEUES'), kv.indexOf('];', kv.indexOf('export const QUEUES')));
  assert.match(list, new RegExp(`'${queue}'`),
    `deploy/kvqueue.mjs does not list ${queue}, so the enqueue is refused`);

  const watch = readFileSync(join(REPO, 'scripts/watch.py'), 'utf8');
  assert.match(watch, new RegExp(`q\\.get\\("${queue}"\\)`),
    `the watcher never looks at the ${queue} queue`);
  assert.match(watch, /apply_marks\.py"\),\s*\n\s*"--unsent"/,
    'the watcher does not hand the queue to apply_marks.py --unsent');

  const marks = readFileSync(join(REPO, 'scripts/apply_marks.py'), 'utf8');
  assert.match(marks, new RegExp(`queue\\("claim", "${queue}/"\\)`),
    `apply_marks.py does not claim ${queue}/`);
  assert.match(marks, /"event": "abandoned"/,
    'apply_marks.py does not write an abandoned event');
});

test('no Retry is offered on an unresolved submit', () => {
  const retries = ACTS.filter((a) => a.props.retry === true);
  assert.ok(retries.length >= 2, `found ${retries.length} Retry controls`);
  for (const a of retries) {
    assert.ok(yieldsToAnUnresolvedSubmit(a.node),
      `Rows.tsx:${a.line} Retry is drawn whatever the history says, and the ` +
      `device refuses it while the latest event is submitting`);
  }
});

test("Watching's Apply yields as well, for the same refusal", () => {
  const applies = ACTS.filter((a) => a.props.status === 'inflight' && a.props.retry !== true);
  assert.ok(applies.length >= 1, 'found no Apply control');
  for (const a of applies) {
    assert.ok(yieldsToAnUnresolvedSubmit(a.node),
      `Rows.tsx:${a.line} Apply is drawn on an unresolved submit`);
  }
});

test('the lanes select the column the cards decide on', () => {
  const lanes = readFileSync(join(REPO, 'apps/web/app/lanes.generated.ts'), 'utf8');
  const count = (lanes.match(/as unresolved_submit/g) || []).length;
  // Every application lane's page, row and sort queries carry it; a handful
  // means the generated file is stale against scripts/v2_lanes.py.
  assert.ok(count >= 6, `unresolved_submit is selected ${count} times`);
  assert.match(lanes, /order by h\.at desc nulls last limit 1\) = 'submitting'/,
    'the column no longer asks whether the latest event is submitting');
});

test('the route refuses an abandoned that answers no submit, before queueing', () => {
  const tree = parse('apps/web/app/api/applications/route.ts');
  let guard = null;
  let firstOrder = null;
  const walk = (node) => {
    if (!guard && ts.isIfStatement(node)
        && /body\.event\s*===\s*"abandoned"/.test(node.expression.getText())) {
      guard = node;
    }
    if (!firstOrder && ts.isCallExpression(node)
        && node.expression.getText() === 'order'
        && node.arguments[0]?.getText().includes('EVENT_QUEUE')) {
      firstOrder = node;
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.ok(guard, 'the route has no guard on event: "abandoned"');
  assert.ok(firstOrder, 'the route no longer orders events through EVENT_QUEUE');
  assert.ok(guard.getStart() < firstOrder.getStart(),
    'the abandoned guard runs after the event is queued');
  const text = guard.getText();
  assert.match(text, /"submitting"/, 'the guard does not ask for a latest submitting');
  assert.match(text, /409/, 'the guard does not refuse');
});
