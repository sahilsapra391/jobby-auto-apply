// The Worker shell around `Channel`: routing, auth, and the one derivation.
//
// `channel.mjs` is tested separately and thoroughly. What is worth testing
// here is the shell's own failure modes, and the one that matters is not a
// crash: if the socket path and the publish path name different Durable
// Objects, sockets connect, `/publish` answers 200, and nothing is ever
// delivered. That looks exactly like a working channel with nothing to say.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import worker, { accountFromPath, channelFor } from '../apps/worker/src/index.mjs';

const ACCOUNT = 'a56bbd42-97be-4198-ba7f-63141323a4c7';

/** A binding that records which name each side asked for. */
function stubEnv({ token = 'secret' } = {}) {
  const asked = [];
  return {
    asked,
    JOBBY_CHANNEL_TOKEN: token,
    TENANT_CHANNEL: {
      idFromName(name) { asked.push(name); return { name }; },
      get(id) {
        return { fetch: async () => new Response(JSON.stringify({ ok: true, id: id.name }),
                                                 { headers: { 'content-type': 'application/json' } }) };
      },
    },
  };
}

const publish = (env, body, headers = {}) => worker.fetch(
  new Request('https://channel.example/publish', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  }), env);

test('the account rides the path, and nothing else is accepted', () => {
  assert.equal(accountFromPath(`/a/${ACCOUNT}`), ACCOUNT);
  assert.equal(accountFromPath(`/a/${ACCOUNT}/`), ACCOUNT);
  // Refused rather than defaulted. A channel that picks an account when the
  // URL does not name one delivers somebody else's applications.
  assert.equal(accountFromPath('/'), null);
  assert.equal(accountFromPath('/a'), null);
  assert.equal(accountFromPath('/a/'), null);
  assert.equal(accountFromPath('/a/../b'), null);
  assert.equal(accountFromPath(''), null);
});

test('both sides derive the same Durable Object name', () => {
  // **The failure this exists for is silent.** Two derivations mean the
  // publisher writes to one object and the subscriber listens to another;
  // every status code is a success and no event ever arrives.
  const env = stubEnv();
  channelFor(env, ACCOUNT);
  const fromSocket = env.asked.pop();
  channelFor(env, ACCOUNT);
  const fromPublish = env.asked.pop();
  assert.equal(fromSocket, fromPublish);
  assert.equal(fromSocket, `account:${ACCOUNT}`);
});

test('a socket with no account in the path is refused, not defaulted', async () => {
  const env = stubEnv();
  const res = await worker.fetch(
    new Request('https://channel.example/', { headers: { Upgrade: 'websocket' } }), env);
  assert.equal(res.status, 400);
  assert.match((await res.json()).error, /must name an account/);
  assert.deepEqual(env.asked, [], 'no Durable Object should have been touched');
});

test('a socket naming an account reaches that account object', async () => {
  const env = stubEnv();
  const res = await worker.fetch(
    new Request(`https://channel.example/a/${ACCOUNT}`, { headers: { Upgrade: 'websocket' } }), env);
  assert.equal(res.status, 200);
  assert.deepEqual(env.asked, [`account:${ACCOUNT}`]);
});

test('publish without the token is refused before any object is touched', async () => {
  const env = stubEnv();
  const res = await publish(env, { account_id: ACCOUNT, kind: 'posting.decided', payload: {} });
  assert.equal(res.status, 401);
  assert.deepEqual(env.asked, [],
    'an unauthenticated publish must not reach an account object at all');
});

test('a wrong token is refused', async () => {
  const env = stubEnv();
  const res = await publish(env, { account_id: ACCOUNT, kind: 'posting.decided' },
                            { authorization: 'Bearer wrong' });
  assert.equal(res.status, 401);
});

test('an unset token is a 503 that names it, never an open door', async () => {
  // The same rule the board's middleware holds: an unset variable looks like
  // nothing at all, and falling open there would look exactly like working.
  const env = stubEnv({ token: '' });
  const res = await publish(env, { account_id: ACCOUNT, kind: 'posting.decided' },
                            { authorization: 'Bearer anything' });
  assert.equal(res.status, 503);
  assert.match((await res.json()).error, /JOBBY_CHANNEL_TOKEN/);
});

test('publish with the token reaches the account object', async () => {
  const env = stubEnv();
  const res = await publish(env, { account_id: ACCOUNT, kind: 'posting.decided', payload: { a: 1 } },
                            { authorization: 'Bearer secret' });
  assert.equal(res.status, 200);
  assert.deepEqual(env.asked, [`account:${ACCOUNT}`]);
});

test('publish with no account_id is refused', async () => {
  const env = stubEnv();
  const res = await publish(env, { kind: 'posting.decided' }, { authorization: 'Bearer secret' });
  assert.equal(res.status, 400);
  assert.deepEqual(env.asked, []);
});

test('healthz answers without a token and touches nothing', async () => {
  const env = stubEnv();
  const res = await worker.fetch(new Request('https://channel.example/healthz'), env);
  assert.equal(res.status, 200);
  assert.deepEqual(env.asked, []);
});

test('the publish body Vercel sends is the body this accepts', () => {
  // One contract in two files. `writes.publish` posts exactly these three
  // fields to `${JOBBY_CHANNEL_URL}/publish` with a bearer token; the shape is
  // asserted here so a change on either side fails rather than drifts.
  const src = readWrites();
  assert.match(src, /\$\{url\}\/publish/);
  assert.match(src, /authorization["']?\s*:\s*`Bearer \$\{process\.env\.JOBBY_CHANNEL_TOKEN/);
  assert.match(src, /account_id:\s*account/);
  assert.match(src, /body:\s*JSON\.stringify\(\{\s*account_id/);
});

function readWrites() {
  return readFileSync(new URL('../apps/web/app/_ui/writes.ts', import.meta.url), 'utf8');
}

test('the account reaches the Durable Object, so envelopes carry it', async () => {
  // Observed on the live channel before it was a test: every envelope went out
  // `account_id: null`, because the shell built `new Channel()` and never told
  // it which account it was. A DO cannot recover its own name from its id, so
  // the path is where it comes from -- and both the socket and the publish
  // request carry it.
  const { TenantChannel } = await import('../apps/worker/src/index.mjs');
  const store = new Map();
  const ctx = {
    blockConcurrencyWhile: async (fn) => fn(),
    storage: { get: async (k) => store.get(k), put: async (k, v) => void store.set(k, v) },
  };
  const doInstance = new TenantChannel(ctx, {});
  const res = await doInstance.fetch(new Request(
    `https://channel/a/${ACCOUNT}/publish`,
    { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ kind: 'posting.decided', payload: { posting_id: 'p1' } }) }));
  assert.equal(res.status, 200);
  assert.equal(store.get('channel').accountId, ACCOUNT,
    'the channel must know which account it is, or every envelope lies');
});

