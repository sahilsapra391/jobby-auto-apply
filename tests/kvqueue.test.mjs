// Tests for deploy/kvqueue.mjs, the board's click queue on Cloudflare KV.
//
// The queue is coalesced into one key so that a poll costs one read instead of
// a list request, KV's free plan allowing 100,000 of the former and 1,000 of
// the latter. That trade brings back read-modify-write, so the cases worth
// pinning are dedup, single-item claims, and the distinction between "this key
// has never been written" and "the read failed".
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readQueue, writeQueue, enqueue, claimOne, claimAll, putConfig, takeConfig,
         configured, CONFIG_FIELD, QUEUES } from '../deploy/kvqueue.mjs';

const ENV = { CF_ACCOUNT_ID: 'acct', CF_KV_NAMESPACE_ID: 'ns', CF_API_TOKEN: 'tok' };

/** Stand in for Cloudflare, so no test ever touches the real namespace. */
function fakeKV({ status = 200, body = null, failWrite = false } = {}) {
  const values = new Map();
  if (body !== null) values.set('jobby:queue', body);
  const state = { values, writes: 0, reads: 0 };
  Object.defineProperty(state, 'value', {
    get: () => values.get('jobby:queue') ?? null,
    set: (value) => values.set('jobby:queue', value),
  });
  global.fetch = async (url, opts = {}) => {
    const parsed = new URL(url);
    const marker = '/values/';
    const key = decodeURIComponent(parsed.pathname.slice(
      parsed.pathname.indexOf(marker) + marker.length
    ));
    if ((opts.method || 'GET') === 'GET') {
      state.reads += 1;
      if (status !== 200) return { ok: false, status, text: async () => 'boom' };
      if (!values.has(key)) return { ok: false, status: 404, text: async () => 'not found' };
      return { ok: true, status: 200, text: async () => JSON.stringify(values.get(key)) };
    }
    state.writes += 1;
    if (failWrite) return { ok: false, status: 403, text: async () => 'forbidden' };
    values.set(key, JSON.parse(opts.body.get('value')));
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
  assert.deepEqual(await readQueue(ENV), {
    apply: [], scan: [], marked: [], drop: [], restore: [], focus: [],
    watch: [], pass: [], providerLocal: [], providerDocuments: [],
    searchConfig: null,
  });
});

test('a failed read raises instead of looking empty', async () => {
  // The whole outage came from a broken read being indistinguishable from a
  // quiet system. It must never resolve to an empty queue.
  fakeKV({ status: 500 });
  await assert.rejects(() => readQueue(ENV), /KV read failed for jobby:queue: 500/);
});

test('a failed write raises rather than silently dropping the click', async () => {
  fakeKV({ body: { apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] }, failWrite: true });
  await assert.rejects(() => writeQueue(ENV, { apply: [] }), /KV write failed for jobby:queue: 403/);
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


// --- the Found lane's additions -------------------------------------------
//
// watch, pass and searchConfig were added to the one key that already held six
// queues. Everything below is the same worry in different clothes: the `claim`
// bug destroyed fourteen untouched Apply clicks because one operation wrote
// over state it had not read carefully. A new field must not be able to do
// that to an old one, and an old one must not be able to do it to a new one.

const FULL = () => ({
  apply: [], scan: [], marked: [], drop: [], restore: [], focus: [],
  watch: [], pass: [], providerLocal: [], providerDocuments: [],
  searchConfig: null,
});

test('provider controls use separate local and document worker lanes', async () => {
  assert.ok(QUEUES.includes('providerLocal'));
  assert.ok(QUEUES.includes('providerDocuments'));
  fakeKV({ body: FULL() });
  await enqueue(ENV, 'providerLocal', 'connect-openai',
    { provider: 'openai', action: 'connect' });
  await enqueue(ENV, 'providerDocuments', 'connect-openai',
    { provider: 'openai', action: 'connect' });
  const local = await claimAll(ENV, 'providerLocal');
  const documents = await claimAll(ENV, 'providerDocuments');
  assert.equal(local[0].provider, 'openai');
  assert.equal(documents.length, 1,
    'the Mac must not consume the OCI worker action');
});

test('OCI provider claims cannot rewrite the application queue', async () => {
  const kv = fakeKV({ body: FULL() });
  await enqueue(ENV, 'apply', 'keep-this-click');
  await enqueue(ENV, 'providerDocuments', 'connect-gemini',
    { provider: 'gemini', action: 'connect' });
  await claimAll(ENV, 'providerDocuments');
  assert.equal(kv.values.has('jobby:queue:providerDocuments'), true);
  assert.equal((await readQueue(ENV)).apply[0].applicationId, 'keep-this-click');
});

test('the eye and pass ride the existing key with no new one', async () => {
  assert.ok(QUEUES.includes('watch'));
  assert.ok(QUEUES.includes('pass'));
  fakeKV({ body: FULL() });
  await enqueue(ENV, 'watch', 'a1b2c3d4e5f6a7b8');
  const q = await readQueue(ENV);
  assert.equal(q.watch.length, 1);
  assert.equal(q.watch[0].applicationId, 'a1b2c3d4e5f6a7b8');
});

test('the eye is idempotent per job id', async () => {
  // The board re-sends remembered clicks on every load. A second eye on one
  // job must not create a second Watching session.
  fakeKV({ body: FULL() });
  assert.equal((await enqueue(ENV, 'watch', 'job-1')).deduped, undefined);
  assert.equal((await enqueue(ENV, 'watch', 'job-1')).deduped, true);
  assert.equal((await readQueue(ENV)).watch.length, 1);
});

test('watching a job does not disturb any other queue', async () => {
  fakeKV({ body: FULL() });
  await enqueue(ENV, 'apply', 'still-running');
  await enqueue(ENV, 'drop', 'give-up');
  await enqueue(ENV, 'watch', 'job-1');
  await enqueue(ENV, 'pass', 'job-2');
  const after = await readQueue(ENV);
  assert.equal(after.apply.length, 1, 'the eye must not touch the apply queue');
  assert.equal(after.drop.length, 1);
  assert.equal(after.watch.length, 1);
  assert.equal(after.pass.length, 1);
});

test('claiming the watch queue leaves the apply queue alone', async () => {
  fakeKV({ body: FULL() });
  await enqueue(ENV, 'apply', 'untouched');
  await enqueue(ENV, 'watch', 'job-1');
  await enqueue(ENV, 'watch', 'job-2');
  assert.equal((await claimAll(ENV, 'watch')).length, 2);
  const after = await readQueue(ENV);
  assert.deepEqual(after.watch, []);
  assert.equal(after.apply.length, 1);
});

test('saving a search config does not clobber the queues', async () => {
  fakeKV({ body: FULL() });
  await enqueue(ENV, 'apply', 'in-flight');
  await enqueue(ENV, 'watch', 'job-1');
  await putConfig(ENV, { keywords: ['product manager'], version: 3 });
  const after = await readQueue(ENV);
  assert.equal(after.apply.length, 1, 'a config save must not eat a queued Apply');
  assert.equal(after.watch.length, 1);
  assert.deepEqual(after[CONFIG_FIELD].config.keywords, ['product manager']);
});

test('queueing a click does not clobber a saved search config', async () => {
  // The other direction, which is the one that is easy to forget.
  fakeKV({ body: FULL() });
  await putConfig(ENV, { keywords: ['business analyst'] });
  await enqueue(ENV, 'apply', 'acme-pm');
  await enqueue(ENV, 'pass', 'job-9');
  const after = await readQueue(ENV);
  assert.ok(after[CONFIG_FIELD], 'the config must survive a click landing after it');
  assert.deepEqual(after[CONFIG_FIELD].config.keywords, ['business analyst']);
});

test('claiming a queue does not clobber a saved search config', async () => {
  fakeKV({ body: FULL() });
  await putConfig(ENV, { keywords: ['pm'] });
  await enqueue(ENV, 'apply', 'a');
  await claimOne(ENV, 'apply');
  assert.ok((await readQueue(ENV))[CONFIG_FIELD]);
});

test('taking the config clears it and leaves everything else', async () => {
  fakeKV({ body: FULL() });
  await enqueue(ENV, 'apply', 'a');
  await putConfig(ENV, { keywords: ['pm'] });
  const taken = await takeConfig(ENV);
  assert.deepEqual(taken.config.keywords, ['pm']);
  const after = await readQueue(ENV);
  assert.equal(after[CONFIG_FIELD], null, 'a taken config must not be taken twice');
  assert.equal(after.apply.length, 1);
});

test('taking nothing is not a write', async () => {
  // The watcher polls every three seconds. If an empty take cost a write, that
  // would be 28,800 writes a day against a 1,000 ceiling.
  const kv = fakeKV({ body: FULL() });
  assert.equal(await takeConfig(ENV), null);
  assert.equal(kv.writes, 0);
});

test('a value written before these fields existed still reads correctly', async () => {
  // The live key predates watch, pass and searchConfig. readQueue merges over a
  // fresh shape, so the watcher does not crash on undefined the first time.
  fakeKV({ body: { apply: [{ applicationId: 'a' }], scan: [], marked: [] } });
  const q = await readQueue(ENV);
  assert.deepEqual(q.watch, []);
  assert.deepEqual(q.pass, []);
  assert.equal(q[CONFIG_FIELD], null);
  assert.equal(q.apply.length, 1);
});
