// A live reload replaces the rows and the cursor together, or the lane lies.
//
// **`reload` did `setRows(() => body.rows)` and stopped there.** The cursor it
// invalidated lives in `LaneList`, two files away. Load more twice, then press
// "3 new rows": rows 51-150 vanish while the cursor still points at 150, so
// the next Load more appends 151-200 onto a fifty-row list. The reader is
// looking at a lane with a hundred rows missing from the middle, no error
// anywhere, and no way to tell.
//
// Which rows are shown, where the next page starts, and whether there is one
// are one fact in three pieces of state. Setting one of them is how they come
// apart.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const UI = join(REPO, 'apps/web/app/_ui');
const read = (f) => readFileSync(join(UI, f), 'utf8');

/** The body of one function, from its declaration to the closing brace. */
function body(source, declaration) {
  const at = source.indexOf(declaration);
  if (at === -1) return null;
  const rest = source.slice(at);
  const end = rest.indexOf('\n  }, [');
  return end === -1 ? rest.slice(0, 1500) : rest.slice(0, end);
}

test('reload does not set the rows behind the cursor\'s back', () => {
  const live = read('useLive.ts');
  const reload = body(live, 'const reload = useCallback');
  assert.ok(reload, 'reload was not found, so this test has no subject');
  assert.ok(!/setRows\s*\(/.test(reload),
    'reload calls setRows directly. That replaces the rows and leaves the ' +
    'cursor pointing into the list it just discarded.');
  assert.ok(/replaceAll\s*\(/.test(reload),
    'reload no longer goes through replaceAll, which is the one call that ' +
    'moves the rows and the paging state together.');
});

test('replaceAll sets all three, from the response it was handed', () => {
  const lane = read('LaneList.tsx');
  const fn = body(lane, 'const replaceAll = useCallback');
  assert.ok(fn, 'replaceAll was not found');
  for (const setter of ['setRows', 'setCursor', 'setDone']) {
    assert.ok(fn.includes(setter),
      `replaceAll does not call ${setter}, so that piece of the paging state ` +
      `survives a reload that invalidated it`);
  }
  // **From the response, not from the server-rendered page.** A reload happens
  // because the rows changed, so its page one is not the page one the server
  // sent and `initialCursor` no longer describes it.
  assert.ok(!/initialCursor|initiallyDone/.test(fn),
    'replaceAll resets to the server-rendered cursor. The reload returned its ' +
    'own `next`, and that is the only cursor belonging to the rows now shown.');
});

test('Load more and reload agree about where the cursor comes from', () => {
  // Both read the same two fields off the same route. If they ever disagree,
  // one of them is describing a page the lane is not on.
  const lane = read('LaneList.tsx');
  const live = read('useLive.ts');
  const more = body(lane, 'const more = useCallback');
  assert.ok(/body\.next/.test(more) && /body\.done/.test(more),
    'Load more no longer reads next/done from the response');
  const reload = body(live, 'const reload = useCallback');
  assert.ok(/body\.next/.test(reload) && /body\.done/.test(reload),
    'reload does not read next/done from its own response, so it cannot pass ' +
    'them on and the cursor is a guess');
});

test('Cursor is declared once', () => {
  // It was declared twice, identically, one edit away from drifting.
  const local = read('LaneList.tsx').match(/^type Cursor\s*=/m);
  assert.equal(local, null,
    'LaneList declares its own Cursor again. The route, the page and the list ' +
    'have to agree about the shape, so it comes from db.ts.');
});
