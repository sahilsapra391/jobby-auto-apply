// What a Profile row says about its last edit, run rather than read.
//
// **The rule has three ways to be wrong and none of them shows up in review.**
// A local hope has to outrank an older recorded outcome for the same answer,
// or editing the same row twice draws "Saved" over an edit that has not been.
// A recorded outcome has to outrank a local hope that has been answered, or a
// stale localStorage entry hides the device's own refusal. And a hope past its
// deadline has to stop being one, because a "sent" that stands for ever is
// indistinguishable from having worked -- the exact failure the board's
// `CLICK_MEMORY` learned when its drop list was immortal.
//
// So the resolution lives in `_ui/editState.ts` with no React in it, and this
// transpiles that file and calls it. Reading a state machine is not running
// one; this repository has a file full of checks that passed while measuring
// nothing, and every one of them was inspected rather than executed.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = join(REPO, 'apps/web/app/_ui/editState.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

const js = ts.transpileModule(readFileSync(SOURCE, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { resolveEditState, slotOf, SENT_TTL_MS } =
  await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));

const SLOT = slotOf('answerEdits', 'salary-expectation');
const NOW = 1_760_000_000_000;
const sent = (over = {}) =>
  ({ editId: 'v2-a', slot: SLOT, at: NOW - 1000, submitted: 'ninety', ...over });
const recorded = (over = {}) => ({
  editId: 'v2-a', queue: 'answerEdits', state: 'accepted',
  target: 'salary-expectation', submitted: null, held: null, reason: null,
  at: '2026-09-05T00:00:00Z', ...over,
});

test('a slot nobody has touched says nothing', () => {
  assert.deepEqual(resolveEditState(SLOT, [], [], NOW), { kind: 'none' });
});

test('an edit this browser sent and the device has not answered is sent', () => {
  const state = resolveEditState(SLOT, [sent()], [], NOW);
  assert.equal(state.kind, 'sent');
});

test('a sent edit past its deadline is lost, not hopeful', () => {
  const old = sent({ at: NOW - SENT_TTL_MS - 1 });
  assert.equal(resolveEditState(SLOT, [old], [], NOW).kind, 'lost');
  // And exactly at the deadline it is still believed, so the boundary is not
  // a place where the label flickers.
  const edge = sent({ at: NOW - SENT_TTL_MS });
  assert.equal(resolveEditState(SLOT, [edge], [], NOW).kind, 'sent');
});

test('the device answering the edit ends the local hope', () => {
  const state = resolveEditState(SLOT, [sent()], [recorded()], NOW);
  assert.equal(state.kind, 'accepted');
  assert.equal(state.at, '2026-09-05T00:00:00Z');
});

test('a second edit outranks the first one\'s recorded outcome', () => {
  // The reader edited the same answer again. The older "accepted" is true and
  // is not what this row is waiting on.
  const again = sent({ editId: 'v2-b', at: NOW - 5 });
  const state = resolveEditState(SLOT, [again], [recorded()], NOW);
  assert.equal(state.kind, 'sent');
});

test('an outcome for another slot is not this row\'s', () => {
  const elsewhere = recorded({ target: 'work-authorisation' });
  assert.deepEqual(resolveEditState(SLOT, [], [elsewhere], NOW), { kind: 'none' });
});

test('the resume wording has a slot even with no target', () => {
  const wording = recorded({ queue: 'resumeEdits', target: null, editId: 'v2-w' });
  const state = resolveEditState(slotOf('resumeEdits', null), [], [wording], NOW);
  assert.equal(state.kind, 'accepted');
});

test('a refusal carries all three facts and they do not share a field', () => {
  const state = resolveEditState(SLOT, [], [recorded({
    state: 'refused', reason: 'the entry moved to version 2',
    submitted: 'ninety', held: 'a hundred and ten',
  })], NOW);
  assert.equal(state.kind, 'refused');
  assert.equal(state.submitted, 'ninety');
  assert.equal(state.held, 'a hundred and ten');
  assert.equal(state.reason, 'the entry moved to version 2');
  assert.equal(state.heldRecorded, true);
});

test('a refusal with no held value says so rather than showing nothing', () => {
  // **The third state.** A version conflict records no held value at all, and
  // `held: null` also means "the store holds nothing". Drawing an empty cell
  // over the first would be "could not look" printed as "is not there", which
  // is the failure this repository has now made five times.
  const state = resolveEditState(SLOT, [], [recorded({
    state: 'refused', reason: 'conflict', submitted: 'ninety', held: null,
  })], NOW);
  assert.equal(state.heldRecorded, false);
});

test('the newest outcome for a slot is the one that counts', () => {
  // The route orders by `at desc`, so first match wins. A row that took the
  // last match would show a refusal the reader has since fixed.
  const outcomes = [
    recorded({ editId: 'v2-c', state: 'accepted', at: '2026-09-05T02:00:00Z' }),
    recorded({ editId: 'v2-a', state: 'refused', at: '2026-09-05T01:00:00Z' }),
  ];
  assert.equal(resolveEditState(SLOT, [], outcomes, NOW).kind, 'accepted');
});
