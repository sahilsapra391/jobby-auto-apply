// The four widening switches, read out of query strings the form produces.
//
// **Two of them were not "unwired", they were stuck on the narrow side**, which
// is worse than inert: the box rendered ticked and the predicate behaved as
// though it were not. `p.posted_at is not null` dropped 2,433 undated postings
// under every date window, and `p.workplace = any(...)` is NULL for a null
// workplace, so 17,084 postings survived a workplace selection only by being
// flagged remote. The board keeps both, by default, and I clicked it to check:
// unticking "unknown posted date" moves 3300 to 3211 and "unknown workplace"
// to 2191.
//
// **A ticked checkbox has no HTML shape that means "absent is on".** It
// submits when ticked and not when it is not, so either the default rides in
// every URL or absence means the opposite of the default. Each of these rides
// with a hidden `=0` companion of the same name and the *last* value wins.
// `params.get` returns the first, which is always the companion -- so a
// straightforward reading of these makes every box read as unticked on every
// load, and that is the failure this file exists to hold shut.
//
// Per the method: every case starts from a query string, and `filtersFrom` and
// `filterValues` are the real ones, compiled out of `db.ts`.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(REPO, 'apps/web/app/_ui/db.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

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
    `module.exports = { filtersFrom, filterValues, FILTER_KEYS };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', 'URLSearchParams', 'WINDOWS', js)(
    mod, mod.exports, URLSearchParams,
    { "6h": "6 hours", "24h": "24 hours", "7d": "7 days",
      "14d": "14 days", "30d": "30 days" });
  return mod.exports;
}

const { filtersFrom, filterValues, FILTER_KEYS } = loadFilterFunctions();
const values = (qs) => filterValues(filtersFrom(new URLSearchParams(qs)));

/** The four SQL parameters, and the URL key each is driven by. */
const SWITCHES = [
  ['unk_posted', 'unkposted'],
  ['unk_wp', 'unkwp'],
  ['unk_exp', 'unkexp'],
  ['inc_remote', 'incremote'],
];

test('all four are declared as on-by-default flags', () => {
  // The floor. Every case below is satisfiable by a `filterValues` that
  // returns four constants, and this is what says the keys are real.
  const kinds = Object.fromEntries(FILTER_KEYS.map((k) => [k.key, k.kind]));
  for (const [, key] of SWITCHES) {
    assert.equal(kinds[key], 'onflag',
      `${key} is ${kinds[key] ?? 'absent'}; a "flag" reads absent as off, ` +
      `which is the opposite of this control's default`);
  }
  assert.ok(FILTER_KEYS.length >= 14, `FILTER_KEYS parsed as ${FILTER_KEYS.length}`);
});

test('a bare lane URL gets the board defaults', () => {
  // The case that matters most: a reader who has touched none of these.
  const v = values('lane=found');
  for (const [param] of SWITCHES) {
    assert.equal(v[param], true,
      `${param} is ${v[param]} with nothing in the URL, so an untouched ` +
      `board narrows in a way the reader never asked for`);
  }
});

test('a ticked box survives the hidden companion in front of it', () => {
  // `get` returns the first value, which is always the companion's "0". A
  // reader that used it would report every box unticked on every load, which
  // looks exactly like the reader having unticked them.
  for (const [param, key] of SWITCHES) {
    const v = values(`lane=found&${key}=0&${key}=1`);
    assert.equal(v[param], true,
      `${key}=0&${key}=1 is a ticked box and read as off`);
  }
});

test('an unticked box is the companion alone, and it turns off', () => {
  for (const [param, key] of SWITCHES) {
    const v = values(`lane=found&${key}=0`);
    assert.equal(v[param], false, `${key}=0 did not turn ${param} off`);
  }
});

test('one box off leaves the other three alone', () => {
  // A shared reader that keyed on the wrong name would move all four together,
  // and every one of the cases above would still pass.
  for (const [, key] of SWITCHES) {
    const v = values(
      `lane=found&${SWITCHES.map(([, k]) => `${k}=0${k === key ? '' : `&${k}=1`}`).join('&')}`);
    for (const [param, other] of SWITCHES) {
      assert.equal(v[param], other !== key,
        `unticking ${key} also moved ${param}`);
    }
  }
});

test('the switches ride through a round trip of the whole filter block', () => {
  // The real shape: the form submits everything at once, so the companions sit
  // among nine other parameters rather than alone.
  const qs = 'lane=found&q=product+manager&within=7d&sort=score&not=senior'
    + '&scope=title&cos=hide&conames=OpenAI&wp=remote&wp=hybrid&exp=5'
    + '&unkposted=0&unkwp=0&unkwp=1&unkexp=0&incremote=0&incremote=1';
  const v = values(qs);
  assert.deepEqual(
    [v.unk_posted, v.unk_wp, v.unk_exp, v.inc_remote],
    [false, true, false, true]);
  // and nothing else moved
  assert.deepEqual(v.q, ['product manager']);
  assert.equal(v.exp, 5);
  assert.equal(v.scope, 'title');
  assert.deepEqual(v.wp, ['remote', 'hybrid']);
});
