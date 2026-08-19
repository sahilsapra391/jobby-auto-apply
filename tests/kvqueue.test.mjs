// Tests for deploy/kvqueue.mjs, the board's click queue on Cloudflare KV.
//
// The queue is coalesced into one key so that a poll costs one read instead of
// a list request, KV's free plan allowing 100,000 of the former and 1,000 of
// the latter. That trade brings back read-modify-write, so the cases worth
// pinning are dedup, single-item claims, and the distinction between "this key
// has never been written" and "the read failed".
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readQueue, writeQueue, enqueue, claimOne, claimAll, configured } from '../deploy/kvqueue.mjs';

const ENV = { CF_ACCOUNT_ID: 'acct', CF_KV_NAMESPACE_ID: 'ns', CF_API_TOKEN: 'tok' };

/** Stand in for Cloudflare, so no test ever touches the real namespace. */
function fakeKV({ status = 200, body = null, failWrite = false } = {}) {
  const state = { value: body, writes: 0, reads: 0 };
  global.fetch = async (url, opts = {}) => {
    if ((opts.method || 'GET') === 'GET') {
      state.reads += 1;
      if (status !== 200) return { ok: false, status, text: async () => 'boom' };
      if (state.value === null) return { ok: false, status: 404, text: async () => 'not found' };
      return { ok: true, status: 200, text: async () => JSON.stringify(state.value) };
    }
    state.writes += 1;
    if (failWrite) return { ok: false, status: 403, text: async () => 'forbidden' };
    state.value = JSON.parse(opts.body.get('value'));
    return { ok: true, status: 200, text: async () => '{}' };
  };
  return state;
}

test('missing configuration is detected rather than guessed at', () => {
  assert.equal(configured({}), false);
  assert.equal(configured({ CF_ACCOUNT_ID: 'a', CF_KV_NAMESPACE_ID: 'b' }), false);
  assert.equal(configured(ENV), true);
});

test('a key that was never written is an empty queue, not an error', async () => {
  fakeKV({ body: null });
  assert.deepEqual(await readQueue(ENV), { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] });
});

test('a failed read raises instead of looking empty', async () => {
  // The whole outage came from a broken read being indistinguishable from a
  // quiet system. It must never resolve to an empty queue.
  fakeKV({ status: 500 });
  await assert.rejects(() => readQueue(ENV), /KV read failed: 500/);
});

test('a failed write raises rather than silently dropping the click', async () => {
  fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] }, failWrite: true });
  await assert.rejects(() => writeQueue(ENV, { apply: [] }), /KV write failed: 403/);
});

test('one poll costs exactly one read', async () => {
  const kv = fakeKV({ body: { apply: [{ applicationId: 'a' }], scan: [], marked: [] } });
  await readQueue(ENV);
  assert.equal(kv.reads, 1, 'a poll must not cost more than one read');
});

test('enqueue dedupes by application id', async () => {
  fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] } });
  assert.equal((await enqueue(ENV, 'apply', 'acme-pm')).deduped, undefined);
  const second = await enqueue(ENV, 'apply', 'acme-pm');
  assert.equal(second.deduped, true);
  assert.equal((await readQueue(ENV)).apply.length, 1);
});

test('claim-one takes the oldest and leaves the rest', async () => {
  // The bug this replaces: claim deleted the whole queue after running one
  // item, destroying fourteen clicks that had never been touched.
  fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] } });
  await enqueue(ENV, 'apply', 'first');
  await enqueue(ENV, 'apply', 'second');
  await enqueue(ENV, 'apply', 'third');
  const claimed = await claimOne(ENV, 'apply');
  assert.equal(claimed.length, 1);
  assert.equal(claimed[0].applicationId, 'first');
  const left = (await readQueue(ENV)).apply.map((x) => x.applicationId);
  assert.deepEqual(left, ['second', 'third']);
});

test('claim-one on an empty queue is a no-op, not a write', async () => {
  const kv = fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] } });
  assert.deepEqual(await claimOne(ENV, 'apply'), []);
  assert.equal(kv.writes, 0);
});

test('claim takes everything in that queue and nothing from the others', async () => {
  fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] } });
  await enqueue(ENV, 'marked', 'one');
  await enqueue(ENV, 'marked', 'two');
  await enqueue(ENV, 'apply', 'untouched');
  assert.equal((await claimAll(ENV, 'marked')).length, 2);
  const after = await readQueue(ENV);
  assert.deepEqual(after.marked, []);
  assert.equal(after.apply.length, 1);
});

test('a queue added later is present even for a value written before it existed', async () => {
  // readQueue merges over a fresh shape, so an old value that predates the drop
  // queue does not make drop undefined and crash the watcher.
  fakeKV({ body: { apply: [{ applicationId: 'a' }], scan: [], marked: [] } });
  const q = await readQueue(ENV);
  assert.deepEqual(q.drop, []);
  assert.equal(q.apply.length, 1);
});

test('dropping does not disturb the other queues', async () => {
  fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] } });
  await enqueue(ENV, 'apply', 'still-running');
  await enqueue(ENV, 'drop', 'give-up-on-this');
  const claimed = await claimAll(ENV, 'drop');
  assert.equal(claimed.length, 1);
  const after = await readQueue(ENV);
  assert.deepEqual(after.drop, []);
  assert.equal(after.apply.length, 1, 'a drop must not touch the apply queue');
});
