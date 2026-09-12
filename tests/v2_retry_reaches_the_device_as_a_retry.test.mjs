// A Retry reaches the device as a retry, which is the only kind of order the
// device will start for an application parked in Needs you.
//
// **v2's Retry sent the apply queue without the flag.** The Python board's
// `/api/retry` adds `{ retry: true }`, and `watch.do_not_start` waives its
// parked refusal only for an entry carrying it. v2 sent `status: "inflight"`,
// the route ordered `apply` with nothing beside the id, and the device refused
// every Retry on a parked card and claimed it. Observed on 2026-09-12: Davies
// c9fcfeed85ae12c8, queued by 04:39:44Z, refused at 04:39:48Z with "it is
// parked in Needs you, waiting on the user", while v2 redrew the card in In
// flight as Queued with nothing behind it.
//
// **Four links, one per file, and each is checked where it lives.** The
// control has to send the flag, the helper has to turn it into the queue flag,
// the route has to pass the helper's answer to `order`, and the device has to
// read the same key. Each was a place the flag could be lost, and it was lost
// at the first three.

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
  // A recovering parser hands back a tree for a file the build refuses.
  assert.equal(tree.parseDiagnostics?.length ?? 0, 0,
    `${rel} does not parse: ` +
    `${(tree.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; ')}`);
  return tree;
}

/** Every `<Act>` whose className names `retry`, with the body literal it sends. */
function retryControls() {
  const tree = parse('apps/web/app/_ui/Rows.tsx');
  const found = [];
  const walk = (node) => {
    const opening = ts.isJsxSelfClosingElement(node) ? node
      : ts.isJsxElement(node) ? node.openingElement : null;
    if (opening && opening.tagName.getText() === 'Act') {
      const attrs = opening.attributes.properties.filter(ts.isJsxAttribute);
      const get = (name) => attrs.find((a) => a.name.getText() === name);
      const cls = get('className');
      const text = cls?.initializer && ts.isStringLiteral(cls.initializer)
        ? cls.initializer.text : '';
      if (/\bretry\b/.test(text)) {
        const body = get('body');
        const expr = body?.initializer && ts.isJsxExpression(body.initializer)
          ? body.initializer.expression : null;
        const props = {};
        if (expr && ts.isObjectLiteralExpression(expr)) {
          for (const p of expr.properties) {
            if (!ts.isPropertyAssignment(p)) continue;
            const init = p.initializer;
            props[p.name.getText()] = ts.isStringLiteral(init) ? init.text
              : init.kind === ts.SyntaxKind.TrueKeyword ? true
              : init.kind === ts.SyntaxKind.FalseKeyword ? false
              : init.getText();
          }
        }
        found.push({
          line: tree.getLineAndCharacterOfPosition(opening.getStart()).line + 1,
          props });
      }
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return found;
}

/** `STATUS_QUEUE` and `orderFlags`, compiled out of queue.ts and run. */
function flagsHelper() {
  const tree = parse('apps/web/app/_ui/queue.ts');
  const want = new Set(['STATUS_QUEUE', 'orderFlags']);
  const parts = [];
  const walk = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name && want.has(node.name.getText())) {
      parts.push(node.getText());
    } else if (ts.isVariableStatement(node)) {
      for (const d of node.declarationList.declarations) {
        if (want.has(d.name.getText())) parts.push(node.getText());
      }
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.equal(parts.length, want.size,
    `extracted ${parts.length} of ${want.size} declarations from queue.ts`);
  const js = ts.transpileModule(
    `${parts.join('\n').replace(/^export /gm, '')}\n` +
    'module.exports = { STATUS_QUEUE, orderFlags };',
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      fileName: 'queue.ts' }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  return mod.exports;
}

test('every Retry control sends the retry flag with its status', () => {
  const controls = retryControls();
  // The floor: Needs You and In flight each draw one, and a walker that stops
  // reaching the Act elements passes everything below on nothing.
  assert.ok(controls.length >= 2, `found ${controls.length} Retry controls in Rows.tsx`);
  for (const { line, props } of controls) {
    assert.equal(props.status, 'inflight', `Rows.tsx:${line} Retry sends status ${props.status}`);
    assert.strictEqual(props.retry, true,
      `Rows.tsx:${line} Retry sends no retry: true, so the device refuses it ` +
      `for any application parked in Needs you`);
  }
});

test("the helper turns the control's flag into the queue flag, and nothing else does", () => {
  const { STATUS_QUEUE, orderFlags } = flagsHelper();
  assert.equal(STATUS_QUEUE.inflight, 'apply');
  assert.deepEqual(orderFlags({ status: 'inflight', retry: true }), { retry: true });
  // A first Apply stays a first Apply.
  assert.deepEqual(orderFlags({ status: 'inflight' }), {});
  // A flag that waives a refusal is not switched on by anything but true.
  assert.deepEqual(orderFlags({ status: 'inflight', retry: 'true' }), {});
  assert.deepEqual(orderFlags({ status: 'inflight', retry: 1 }), {});
  // Only the apply queue reads it: a Stop or an Undrop carries nothing extra.
  assert.deepEqual(orderFlags({ status: 'dropped', retry: true }), {});
  assert.deepEqual(orderFlags({ status: 'watch', retry: true }), {});
  assert.deepEqual(orderFlags({ retry: true }), {});
});

test("the route passes the helper's answer with the status order", () => {
  // Read rather than called: the handler needs Postgres and KV. What is read is
  // the arguments of the one call that carries a status to its queue.
  const tree = parse('apps/web/app/api/applications/route.ts');
  const calls = [];
  const walk = (node) => {
    if (ts.isCallExpression(node) && node.expression.getText() === 'order'
        && node.arguments.length >= 2
        && node.arguments[0].getText() === 'STATUS_QUEUE[body.status]') {
      calls.push(node.arguments.map((a) => a.getText()));
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.equal(calls.length, 1, `found ${calls.length} status orders in route.ts`);
  assert.equal(calls[0][2], 'orderFlags(body)',
    `the status order passes ${calls[0][2] ?? 'nothing'} as its flags, so a ` +
    `Retry's flag stops at the route`);
});

test('the device waives the parked refusal on the same key the board writes', () => {
  // One contract in two languages. Code only: a docstring beside the call
  // quotes an older expression, and a match on prose passes through a rename.
  const py = readFileSync(join(REPO, 'scripts/watch.py'), 'utf8')
    .replace(/"""[\s\S]*?"""/g, '')
    .replace(/#.*$/gm, '');
  const reads = py.match(/retry=apply_head\.get\("[A-Za-z_]+"\) is True/g) ?? [];
  assert.ok(reads.length >= 1,
    'watch.py no longer passes retry=apply_head.get(...) is True to do_not_start');
  for (const r of reads) {
    assert.match(r, /get\("retry"\)/, `the device reads ${r}, and the board writes retry`);
  }
});
