// Every filter that narrows page one narrows page two.
//
// **`filtersFrom` read ten keys and `laneQuery` wrote three.** The route was
// right and the writer was the leak, so page one was filtered and every page
// after it was not. Measured on production with `exp=2`: page one 50 rows and
// 0 violations; page two 50 rows and 25 violations carrying `years_min` up to
// 12. Same declared-twice shape as `GATE_PATHS`, `verify_parity.FIELDS`, the
// migrations list and the generated-file list -- and the fifth instance was
// *this pair*, caught the same way, by checking rows rather than counts.
//
// It survived because the advanced controls needed a Filter button nobody
// found. Making them apply on change turns it into the common case.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(REPO, 'apps/web/app/_ui/db.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/** The `Filters` type's own field names, from the AST rather than a list. */
function filterFields() {
  const src = readFileSync(DB, 'utf8');
  const tree = ts.createSourceFile(DB, src, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
  let names = null;
  const walk = (node) => {
    if (ts.isTypeAliasDeclaration(node) && node.name.getText(tree) === 'Filters'
        && ts.isTypeLiteralNode(node.type)) {
      names = node.type.members.filter(ts.isPropertySignature)
        .map((m) => m.name.getText(tree));
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return names;
}

/** The keys `FILTER_KEYS` declares, which now drives both directions. */
function declaredKeys() {
  const src = readFileSync(DB, 'utf8');
  const block = src.slice(src.indexOf('const FILTER_KEYS'), src.indexOf('] as const;'));
  return [...block.matchAll(/key:\s*"([^"]+)"/g)].map((m) => m[1]);
}

test('every field of Filters is a key both directions know', () => {
  const fields = filterFields();
  assert.ok(fields && fields.length >= 8,
    'the Filters type was not found, so this test has no subject');
  const declared = new Set(declaredKeys());
  const dropped = fields.filter((f) => !declared.has(f));
  assert.deepEqual(dropped, [],
    'these filters are read from the URL and never written back into it, so ' +
    'they narrow page one and every later page is unfiltered:\n  ' +
    dropped.join('\n  '));
});

test('the key list is not a restatement of three', () => {
  // Anti-vacuity with the number that matters: the bug was a list of three
  // beside a reader of ten, so a floor at three would have passed it.
  assert.ok(declaredKeys().length >= 10,
    `FILTER_KEYS declares ${declaredKeys().length} keys; the reader takes ten`);
});

test('laneQuery is generated from the list, not hand-written', () => {
  // The property that stops it drifting again. A `laneQuery` that names any
  // filter literally is one somebody has to remember to extend.
  const src = readFileSync(DB, 'utf8');
  const body = src.slice(src.indexOf('export function laneQuery'),
                         src.indexOf('\n}', src.indexOf('export function laneQuery')));
  for (const key of declaredKeys()) {
    assert.ok(!body.includes(`"${key}"`),
      `laneQuery names "${key}" literally. It has to walk FILTER_KEYS, or the ` +
      `next filter added is the next one silently dropped from page two.`);
  }
});

// `SCOPE_OPTIONS` may carry a type annotation between the name and the `=`,
// which a literal match misses. It did.
const DECL = /SCOPE_OPTIONS\s*(?::[^=]*)?=/;

test('the scope select emits a value filterValues actually tests for', () => {
  // **"Title and description" was a no-op and this was the whole reason.**
  // `filterValues` compared against "all", which nothing on this axis has ever
  // emitted: the vocabulary is `title` | `title+description`. Both choices
  // collapsed to "title", so the title-only predicate stayed on either way,
  // and the stored preference on production was the one being discarded.
  const src = readFileSync(DB, 'utf8');
  // **From `SCOPE_OPTIONS`, not from every `<option>` on the page.** The first
  // version of this scanned all of them and picked up the companies select's
  // `all | only | hide`, which is a different axis that legitimately uses
  // "all" -- and so reported the fixed code as broken. A checker that cannot
  // tell one select from another is confidently wrong in whichever direction
  // its default points.
  // It lives in `search.ts`, generated from
  // `found_lane.SCOPE_OPTIONS`, so the vocabulary is already single-sourced
  // across the two languages. The defect was never drift in the list: it was
  // `db.ts` comparing against a value that has never been in it.
  const vocabFile = [join(REPO, 'apps/web/app/search.ts'),
                     join(REPO, 'apps/web/app/_ui/FoundSurface.tsx')]
    .find((f) => { try { return DECL.test(readFileSync(f, 'utf8')); }
                   catch { return false; } });
  assert.ok(vocabFile, 'SCOPE_OPTIONS is not declared anywhere this can read');
  const vocab = readFileSync(vocabFile, 'utf8');
  const start = vocab.search(DECL);
  const block = vocab.slice(start, vocab.indexOf(';', start));
  const emitted = new Set([...block.matchAll(/"([^"]+)"/g)]
    .map((m) => m[1]).filter((v) => !/\s/.test(v)));
  const line = src.split('\n').find((l) => /^\s*scope:/.test(l));
  assert.ok(line, 'filterValues no longer decides a scope');
  const compared = [...line.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  const orphan = compared.filter((v) => !emitted.has(v));
  assert.deepEqual(orphan, [],
    `filterValues compares scope against ${JSON.stringify(orphan)}, which no ` +
    `control emits. The select offers ${JSON.stringify([...emitted])}, so ` +
    `both of its choices take the same branch and the option does nothing.`);
});

// **A stored location is an object, and stringifying one gives
// `"[object Object]"`.** `search_config.validate` enforces
// `[{country:"US"}, {regionPack:"EU"}]`, and `savedSearch` read it with
// `.map(String)`: the chip would have read that literal, the picker would have
// shown nothing ticked over a real saved search, and a `regionPack` would have
// been lost rather than expanded into its countries.
//
// `test_region_packs_are_one_contract.py` compares the two copies of the pack
// table and is blind to this: reverting the expansion to `.map(String)` leaves
// it green, which is a rule stated in one place not being a rule the codebase
// holds. This is the half that watches the call site.
test('the saved locations are expanded, never stringified', () => {
  const src = readFileSync(DB, 'utf8');
  const fn = src.slice(src.indexOf('export async function savedSearch'));
  const body = fn.slice(0, fn.indexOf('\n}\n'));

  assert.ok(/expandLocations\s*\(/.test(body),
    'savedSearch no longer expands the stored locations. They are objects; ' +
    'anything else turns every one of them into "[object Object]".');

  const stringified = body.match(/locations[^\n]*\.map\(String\)/);
  assert.equal(stringified, null,
    `savedSearch stringifies the locations: ${stringified?.[0]}. ` +
    'That is the defect this replaced.');
});

test('the expansion handles both entry shapes and refuses neither', () => {
  // The vocabulary is two keys, and dropping either is silent: a lost
  // `regionPack` narrows a search to nothing and a lost `country` widens it.
  const src = readFileSync(DB, 'utf8');
  const fn = src.slice(src.indexOf('function expandLocations'));
  const body = fn.slice(0, fn.indexOf('\n}\n'));
  for (const key of ['regionPack', 'country']) {
    assert.ok(body.includes(key),
      `expandLocations never reads ${key}, so entries carrying it expand to ` +
      `nothing and the saved search silently changes meaning`);
  }
});
