// Nothing writes the saved search except a control somebody pressed.
//
// **The saved search is not a view preference.** `ingest.run` reads it to
// decide which jobs to fetch and keep, so narrowing it narrows sourcing, and a
// write nobody asked for has no undo.
//
// `SaveFilters` saved the filter form on every change from a `useEffect`, which
// turned any URL somebody navigated to into the account's saved search. On
// 2026-09-09 diagnostic navigation on the deployed board wrote
// `locations: [{country:"IN"}]`, `keywords: ["Technical","Program","Manager"]`
// and `postedWithin: "6h"` over a search that had been worldwide, keywordless
// and 30 days: version 23 to 27 in an afternoon. From the reader's side every
// filter looked broken, because every filter was operating inside 221 rows.
//
// The rule is about the trigger, not the endpoint. A POST from an `onClick` is
// somebody pressing a button. A POST from an effect is the page deciding.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(REPO, 'apps/web/app');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/** The routes that change the account's sourcing rather than one application. */
const SOURCING_WRITES = ['/api/search-config'];

function sources(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(full)) out.push(full);
  }
  return out;
}

/** Every mention of a sourcing route, with whether an effect encloses it. */
function sourcingCalls() {
  const found = [];
  for (const file of sources(APP)) {
    const src = readFileSync(file, 'utf8');
    if (!SOURCING_WRITES.some((r) => src.includes(r))) continue;
    const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);

    const walk = (node, insideEffect) => {
      let nowInside = insideEffect;
      if (ts.isCallExpression(node)) {
        const fn = node.expression.getText(tree);
        if (fn === 'useEffect' || fn === 'useLayoutEffect') nowInside = true;
      }
      if (ts.isStringLiteral(node) && SOURCING_WRITES.includes(node.text)) {
        const { line } = tree.getLineAndCharacterOfPosition(node.getStart());
        found.push({ file: relative(APP, file), line: line + 1, insideEffect: nowInside });
      }
      node.forEachChild((child) => walk(child, nowInside));
    };
    walk(tree, false);
  }
  return found;
}

test('the walk finds the sourcing writers, so this cannot pass vacuously', () => {
  const calls = sourcingCalls();
  assert.ok(calls.length >= 1,
    'no reference to /api/search-config anywhere under apps/web/app. Either ' +
    'the route was renamed and this rule now guards nothing, or the walk broke.');
});

test('no effect writes the saved search', () => {
  const automatic = sourcingCalls().filter((c) => c.insideEffect);
  assert.deepEqual(automatic, [],
    'these write the account\'s saved search from an effect rather than from a ' +
    'press, so navigating to a URL rewrites what the sweep fetches:\n' +
    automatic.map((c) => `  ${c.file}:${c.line}`).join('\n'));
});

test('SaveFilters is gone rather than merely unrendered', () => {
  // A component nobody renders is one edit from being rendered again, and the
  // edit looks like wiring something up rather than like re-introducing a bug.
  for (const file of sources(APP)) {
    const src = readFileSync(file, 'utf8');
    const declares = /export\s+function\s+SaveFilters\b/.test(src);
    assert.ok(!declares,
      `${relative(APP, file)} declares SaveFilters again. If the saved search ` +
      `should be written automatically, that needs a decision about sourcing, ` +
      `not a component.`);
  }
});
