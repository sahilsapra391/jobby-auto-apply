// The bridge from a v2 click to the daemon's work queue.
//
// **The two halves were different systems and nothing joined them.** Measured
// 2026-09-04: `watch.poll_queues` reads Cloudflare KV; the v2 write route
// wrote Postgres and contained no enqueue; `scripts/jobs_store.py` has zero
// SELECTs and nothing under `scripts/` reads Postgres back. So a Watch pressed
// on a v2 card wrote a row, v2's own lane view read that row and moved the
// card, and the watcher never learned anything had happened.
//
// What is worth testing here is the half a live click cannot check cheaply:
// that the queue names agree with the board's, that the generated copy really
// is the board's queue, and that a failing enqueue refuses loudly rather than
// falling through to a commit.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(REPO, p), 'utf8');

/** The board's own endpoint-to-queue map, parsed rather than restated. */
function boardQueues() {
  const src = read('deploy/api/board.js');
  const block = src.slice(src.indexOf('const QUEUES = {'));
  const body = block.slice(block.indexOf('{'), block.indexOf('};') + 1);
  const out = {};
  for (const m of body.matchAll(/'(\/api\/[a-z]+)':\s*'([a-z]+)'/g)) {
    out[m[1].replace('/api/', '')] = m[2];
  }
  return out;
}

/** v2's maps, parsed out of the TypeScript for the same reason. */
function v2Queues(name) {
  const src = read('apps/web/app/_ui/queue.ts');
  const at = src.indexOf(`export const ${name}: Record<string, string> = {`);
  assert.notEqual(at, -1, `${name} is not exported from queue.ts`);
  const body = src.slice(at, src.indexOf('};', at));
  const out = {};
  for (const m of body.matchAll(/^\s*(\w+):\s*"([a-z]+)",/gm)) out[m[1]] = m[2];
  return out;
}

test('every queue v2 sends to is one the board already sends to', () => {
  const board = new Set(Object.values(boardQueues()));
  assert.ok(board.size >= 8, `parsed only ${board.size} board queues; the ` +
                             `matcher has stopped matching`);
  for (const [decision, queue] of Object.entries(v2Queues('DECISION_QUEUE'))) {
    assert.ok(board.has(queue),
              `v2 sends ${decision} to a queue the board never uses: ${queue}. ` +
              `The watcher has no case for it and the click would sit there.`);
  }
  for (const [status, queue] of Object.entries(v2Queues('STATUS_QUEUE'))) {
    assert.ok(board.has(queue), `v2 sends status ${status} to ${queue}`);
  }
});

test('the three decisions the board offers on a Found card are all bridged', () => {
  const v2 = v2Queues('DECISION_QUEUE');
  // Named, because the omission that matters is silent: a decision with no
  // queue records an opinion and orders no work, which is the state this
  // whole module exists to end.
  assert.deepEqual(Object.keys(v2).sort(), ['apply', 'pass', 'watch']);
  assert.equal(v2.watch, 'watch');
  assert.equal(v2.apply, 'apply');
  assert.equal(v2.pass, 'pass');
});

test('a status the daemon owns is deliberately not enqueued', () => {
  const v2 = v2Queues('STATUS_QUEUE');
  assert.deepEqual(Object.keys(v2), ['dropped']);
  for (const owned of ['review', 'active', 'inflight', 'watch']) {
    assert.equal(v2[owned], undefined,
                 `${owned} is a stage the daemon moves an application through. ` +
                 `Enqueueing it would be v2 inventing a work order the board ` +
                 `has no button for.`);
  }
});

test('the generated queue is the board queue, but for its kv import', () => {
  const source = read('deploy/kvqueue.mjs');
  const copy = read('apps/web/app/_ui/kvqueue.generated.mjs');
  const body = copy.slice(copy.indexOf('// The board\'s click queue'));
  assert.equal(
    body.replace("./kv.generated.mjs", "./kv.mjs"), source,
    'the generated queue has drifted from deploy/kvqueue.mjs beyond the one ' +
    'import line the generator is allowed to rewrite');
});

test('the generated kv client is verbatim', () => {
  const copy = read('apps/web/app/_ui/kv.generated.mjs');
  const body = copy.slice(copy.indexOf('// Low-level Cloudflare Workers KV'));
  assert.equal(body, read('deploy/kv.mjs'));
});

test('an unconfigured queue is refused by name, never skipped', async () => {
  const { configured } = await import('../apps/web/app/_ui/kv.generated.mjs');
  assert.equal(configured({}), false);
  assert.equal(configured({ CF_ACCOUNT_ID: 'a', CF_KV_NAMESPACE_ID: 'n' }), false,
               'two of three is not configured; a partial credential that read ' +
               'as configured would fail at the API with a worse message');
  assert.equal(
    configured({ CF_ACCOUNT_ID: 'a', CF_KV_NAMESPACE_ID: 'n', CF_API_TOKEN: 't' }),
    true);
});

test('enqueue is idempotent per application, so a re-sent click is one click', async () => {
  const { enqueue } = await import('../apps/web/app/_ui/kvqueue.generated.mjs');
  const env = { CF_ACCOUNT_ID: 'a', CF_KV_NAMESPACE_ID: 'n', CF_API_TOKEN: 't' };
  // A key-aware store rather than one slot: `enqueue` reads and writes by key,
  // and a stub that ignores keys would pass a client that wrote to the wrong
  // one. Testing against a fixture looser than the real thing is how a
  // configuration nobody ships gets verified.
  const kv = new Map();
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const key = String(url);
    if (init?.method === 'PUT') {
      // `kvPut` sends multipart FormData with the payload under `value`, not a
      // string body. A stub that stringified it stored "[object FormData]" and
      // the read back never parsed, so the dedupe looked broken when the
      // client was fine. Read it the way Cloudflare does.
      kv.set(key, init.body.get('value'));
      return { ok: true, status: 200, text: async () => '' };
    }
    if (!kv.has(key)) return { ok: false, status: 404, text: async () => '' };
    return { ok: true, status: 200, text: async () => kv.get(key) };
  };
  try {
    const first = await enqueue(env, 'watch', 'job-1');
    assert.equal(first.queued, 'job-1');
    assert.ok(!first.deduped, 'the first click is not a duplicate of anything');
    const again = await enqueue(env, 'watch', 'job-1');
    assert.equal(again.deduped, true,
                 'a click the board re-sends from its memory must not queue twice');
    const other = await enqueue(env, 'watch', 'job-2');
    assert.ok(!other.deduped, 'a different job is a different click');
  } finally {
    globalThis.fetch = real;
  }
});

test('a KV write that fails throws, so the caller can refuse to commit', async () => {
  const { enqueue } = await import('../apps/web/app/_ui/kvqueue.generated.mjs');
  const env = { CF_ACCOUNT_ID: 'a', CF_KV_NAMESPACE_ID: 'n', CF_API_TOKEN: 't' };
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) =>
    init?.method === 'PUT'
      ? { ok: false, status: 500, text: async () => 'kv exploded' }
      : { ok: true, status: 200, text: async () => '{}' };
  try {
    await assert.rejects(() => enqueue(env, 'watch', 'job-2'),
                         'a silent failure here writes a row for work nobody ' +
                         'will do, which is the exact state being fixed');
  } finally {
    globalThis.fetch = real;
  }
});
