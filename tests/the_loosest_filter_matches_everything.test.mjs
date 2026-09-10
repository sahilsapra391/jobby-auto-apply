// The loosest URL the form can produce must not narrow anything.
//
// **"0 jobs match, of 22,735 in the pool", under every box ticked.** The
// keyword box submits alongside the hidden chips, so an empty box put `q=` in
// the URL; `f.q.map(...).filter(Boolean) || null` then produced `[]` rather
// than null, because **an empty array is truthy in JavaScript**; and
// `unnest('{}')` yields no rows, so the `exists` was false for every posting.
//
// Every "measured against the database" before this one was a query I wrote.
// This one starts from a query string, which is the only input the page ever
// gets, and it walks the real `filtersFrom` and `filterValues`.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(REPO, 'apps/web/app/_ui/db.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/**
 * `filtersFrom` and `filterValues`, compiled out of `db.ts` and run.
 *
 * Reading the source and asserting its shape is what let this ship: the
 * expression was right about the strings and wrong about the container, and
 * only running it says so.
 */
function loadFilterFunctions() {
  const src = readFileSync(DB, 'utf8');
  const grab = (name) => {
    const at = src.indexOf(`function ${name}(`);
    if (at === -1) throw new Error(`${name} not found in db.ts`);
    let i = src.indexOf('{', at), depth = 0;
    for (let j = i; j < src.length; j++) {
      if (src[j] === '{') depth++;
      else if (src[j] === '}' && --depth === 0) return src.slice(at, j + 1);
    }
    throw new Error(`${name} never closes`);
  };
  const keys = src.slice(src.indexOf('const FILTER_KEYS'), src.indexOf('] as const;') + 11);
  const js = ts.transpileModule(
    `${keys}\n${grab('filtersFrom')}\n${grab('filterValues')}\n` +
    `module.exports = { filtersFrom, filterValues };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  // `WINDOWS` is supplied rather than imported: `filterValues` reads it to map
  // a chip to an interval, and this only needs the keys to exist.
  new Function('module', 'exports', 'URLSearchParams', 'WINDOWS', js)(
    mod, mod.exports, URLSearchParams,
    { "6h": "6 hours", "24h": "24 hours", "7d": "7 days",
      "14d": "14 days", "30d": "30 days" });
  return mod.exports;
}

const { filtersFrom, filterValues } = loadFilterFunctions();

/** The exact string the board produced when it read 0 of 22,735. */
const LOOSEST = 'lane=found&q=&within=24h&sort=score&not=&scope=title%2Bdescription'
              + '&cos=all&conames=&wp=remote&wp=hybrid&wp=onsite&exp=';

test('an empty keyword box is no keyword, not an empty list', () => {
  const values = filterValues(filtersFrom(new URLSearchParams(LOOSEST)));
  assert.equal(values.q, null,
    `the empty box produced ${JSON.stringify(values.q)}. An empty array is ` +
    `truthy in JavaScript, and the predicate reads it as "match nothing": ` +
    `unnest('{}') yields no rows so the exists is false for every posting.`);
});

test('no filter in the loosest URL narrows anything', () => {
  // Every value the page would send. Anything that is not null here is a
  // predicate that will run, and the loosest filter must run none of them.
  const values = filterValues(filtersFrom(new URLSearchParams(LOOSEST)));
  const narrowing = Object.entries(values).filter(([k, v]) => {
    if (v === null || v === false) return false;
    // `within` is a real choice in this URL and legitimately narrows.
    if (k === 'within') return false;
    return true;
  });
  assert.deepEqual(narrowing, [],
    'these are set from a URL where the reader chose nothing, so each one ' +
    'silently narrows the lane: ' + JSON.stringify(narrowing));
});

test('a real keyword still arrives, so this is not vacuous', () => {
  const values = filterValues(filtersFrom(
    new URLSearchParams('q=product+manager&q=&lane=found')));
  assert.deepEqual(values.q, ['product manager'],
    'the empty entry should be dropped and the real one kept');
});

test('keywords are deduped, like the board', () => {
  const values = filterValues(filtersFrom(
    new URLSearchParams('q=product+manager&q=product+manager&lane=found')));
  assert.deepEqual(values.q, ['product manager']);
});
