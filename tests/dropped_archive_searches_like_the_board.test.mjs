// The Dropped dialog searches what the board's archive searched.
//
// The board's `dropped_row` haystack is company, role, the lane a role was
// dropped from, the stage and the reason. On the board "posting closed" found
// 2 of 58 and "watch" found 49; v2's dialog found 0 for both, because its
// search navigated to Found and searched postings instead.
//
// This runs `droppedLanes.ts` rather than reading it, against rows shaped the
// way the lane query returns them.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

function load() {
  const src = readFileSync(join(REPO, 'apps/web/app/_ui/droppedLanes.ts'), 'utf8');
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  return mod.exports;
}

const { droppedFromLabel, droppedHaystack } = load();

const ROWS = [
  { id: '1', company: 'Alpha Labs', role: 'Designer', dropped_from: 'Watching',
    dropped_stage: 'not started',
    dropped_reason: 'posting closed by the employer, noticed on 2026-10-02' },
  { id: '2', company: 'Beta Corp', role: 'Engineer', dropped_from: 'watch',
    dropped_stage: 'not started', dropped_reason: null },
  { id: '3', company: 'Gamma Inc', role: 'Analyst', dropped_from: 'In flight',
    dropped_stage: 'Queued (starting the local form run)', dropped_reason: null },
  { id: '4', company: 'Delta', role: 'Writer', dropped_from: null,
    dropped_stage: null, dropped_reason: null },
];

const search = (term) => ROWS
  .filter((r) => droppedHaystack(r).includes(term.toLowerCase()))
  .map((r) => r.id);

test('the reason, the stage and the lane are all searched', () => {
  assert.deepEqual(search('posting closed'), ['1']);
  assert.deepEqual(search('queued'), ['3']);
  // Both spellings the store has written answer the lane's own word.
  assert.deepEqual(search('watch'), ['1', '2']);
  assert.deepEqual(search('Watching'), ['1', '2'],
    'a role recorded as "watch" is labelled Watching, so a search for the ' +
    'label it is shown under has to find it');
});

test('company and role are still searched, so this is not only the new fields', () => {
  assert.deepEqual(search('beta'), ['2']);
  assert.deepEqual(search('analyst'), ['3']);
  assert.deepEqual(search('nothing like this'), []);
});

test('the two spellings of one lane are one entry', () => {
  assert.equal(droppedFromLabel('watch'), 'Watching');
  assert.equal(droppedFromLabel('Watching'), 'Watching');
  assert.equal(droppedFromLabel('inflight'), 'In flight');
  assert.equal(droppedFromLabel('review'), 'Needs you');
  assert.equal(droppedFromLabel(null), 'unknown');
  // A spelling nobody mapped is shown as written, never dropped.
  assert.equal(droppedFromLabel('Somewhere new'), 'Somewhere new');
  const lanes = new Set(ROWS.map((r) => droppedFromLabel(r.dropped_from)));
  assert.deepEqual([...lanes].sort(), ['In flight', 'Watching', 'unknown']);
});
