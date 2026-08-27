import test from 'node:test';
import assert from 'node:assert/strict';

process.env.CF_ACCOUNT_ID = 'account';
process.env.CF_KV_NAMESPACE_ID = 'namespace';
process.env.CF_API_TOKEN = 'token';
process.env.DASHBOARD_PASSWORD = 'test-password';

import { gzipSync } from 'node:zlib';

const values = new Map();
const originalFetch = globalThis.fetch;

globalThis.fetch = async (input, options = {}) => {
  const url = new URL(input);
  const method = options.method || 'GET';
  if (url.hostname === 't3.gstatic.com' && url.pathname === '/faviconV2') {
    const asked = new URL(url.searchParams.get('url'));
    if (asked.hostname !== 'example.com') {
      return new Response('', { status: 404 });
    }
    return new Response(new Uint8Array(256).fill(137), {
      status: 200, headers: { 'content-type': 'image/png' },
    });
  }
  const marker = '/storage/kv/namespaces/namespace/values/';
  const position = url.pathname.indexOf(marker);
  if (position < 0) return new Response('', { status: 404 });
  const key = decodeURIComponent(url.pathname.slice(position + marker.length));
  if (method === 'GET') {
    return values.has(key)
      ? new Response(values.get(key), { status: 200 })
      : new Response('', { status: 404 });
  }
  if (method === 'PUT') {
    values.set(key, String(options.body.get('value')));
    return new Response('', { status: 200 });
  }
  return new Response('', { status: 405 });
};

const { default: handler } = await import('../deploy/api/board.js');
const { putBoard, putBoardState, getBoardState } = await import('../deploy/kvcontent.mjs');

function auth() {
  return `Basic ${Buffer.from('user:test-password').toString('base64')}`;
}

function request(path, body, method = 'POST', authenticated = true) {
  const headers = { origin: 'https://board.example' };
  if (authenticated) headers.authorization = auth();
  if (method === 'POST') headers['x-jobby-request'] = '1';
  if (body !== undefined || method === 'POST') headers['content-type'] = 'application/json';
  return new Request(`https://board.example${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

test.after(() => {
  globalThis.fetch = originalFetch;
});

test.beforeEach(() => {
  values.clear();
});

test('provider controls stay behind the dashboard password', async () => {
  const response = await handler.fetch(
    request('/api/provider', { provider: 'openai', target: 'local', action: 'connect' }, 'POST', false)
  );
  assert.equal(response.status, 401);
  assert.equal(values.size, 0);
});

test('sanitized board state is compressed and health reports its publication', async () => {
  const state = JSON.stringify({ profile: { firstName: 'A' }, sessions: [] });
  await putBoardState(process.env, state, '2026-08-24T12:00:00Z');
  const envelope = await getBoardState(process.env);
  assert.equal(envelope.schemaVersion, 1);
  assert.equal(envelope.rendererSchemaVersion, 1);
  assert.equal(envelope.encoding, 'gzip+base64');
  assert.equal(envelope.generatedAt, '2026-08-24T12:00:00Z');
  assert.notEqual(envelope.payload, Buffer.from(state).toString('base64'));

  const response = await handler.fetch(request('/healthz', undefined, 'GET', false));
  assert.equal(response.status, 200);
  const health = await response.json();
  assert.equal(health.statePublished, true);
  assert.equal(health.stateGeneratedAt, '2026-08-24T12:00:00Z');
  assert.equal(health.rendererSchemaVersion, health.stateSchemaVersion);
});

test('the posting route decodes the state envelope it is given', async () => {
  // The regression this pins: getBoardState returns {schemaVersion, encoding,
  // payload} with the state gzipped inside it, and the route read .jobs
  // straight off that envelope. Every id missed, so every card reported that
  // the posting had no headings. A stand-in that served jobs.json directly
  // could not have shown it, which is how it reached production.
  const state = {
    jobs: {
      abc123: {
        title: 'Product Manager',
        sections: {
          responsibilities: ['Own the roadmap.', 'Mentor the team.'],
          requirements: ['5 years of product experience.'],
        },
      },
    },
  };
  values.set('jobby:board-state', JSON.stringify({
    schemaVersion: 1,
    rendererSchemaVersion: 1,
    encoding: 'gzip+base64',
    generatedAt: '2026-08-25T12:00:00Z',
    payload: gzipSync(Buffer.from(JSON.stringify(state), 'utf8')).toString('base64'),
  }));

  const found = await handler.fetch(request('/api/posting?id=abc123', undefined, 'GET'));
  assert.equal(found.status, 200);
  const body = await found.json();
  assert.equal(body.ok, true);
  assert.deepEqual(body.responsibilities, ['Own the roadmap.', 'Mentor the team.']);
  assert.deepEqual(body.requirements, ['5 years of product experience.']);

  // An id the state does not carry is an empty answer, not an error.
  const missing = await handler.fetch(request('/api/posting?id=nosuchjob', undefined, 'GET'));
  assert.equal(missing.status, 200);
  assert.deepEqual((await missing.json()).responsibilities, []);

  // And the gate and the id shape are both enforced.
  const anonymous = await handler.fetch(
    request('/api/posting?id=abc123', undefined, 'GET', false));
  assert.equal(anonymous.status, 401);
  const hostile = await handler.fetch(
    request('/api/posting?id=' + encodeURIComponent('../etc'), undefined, 'GET'));
  assert.equal(hostile.status, 400);
});

test('company logos stay behind the password and proxy only safe domains', async () => {
  const anonymous = await handler.fetch(
    request('/api/company-logo?domain=example.com', undefined, 'GET', false)
  );
  assert.equal(anonymous.status, 401);

  const logo = await handler.fetch(
    request('/api/company-logo?domain=example.com', undefined, 'GET')
  );
  assert.equal(logo.status, 200);
  assert.equal(logo.headers.get('content-type'), 'image/png');
  assert.match(logo.headers.get('cache-control'), /private/);

  const unsafe = await handler.fetch(
    request('/api/company-logo?src=https%3A%2F%2Fhostile.example%2Ftrack.png', undefined, 'GET')
  );
  assert.equal(unsafe.status, 404);

  // alt=1 means "try the other TLDs of this label". example.io misses and
  // example.com answers, so the walk has to reach it.
  const expanded = await handler.fetch(
    request('/api/company-logo?domain=example.io&alt=1', undefined, 'GET')
  );
  assert.equal(expanded.status, 200);

  // Without the flag there is nothing to fall back to.
  const single = await handler.fetch(
    request('/api/company-logo?domain=example.io', undefined, 'GET')
  );
  assert.equal(single.status, 404);

  // A domain nothing answers for stays a 404, which is what leaves the
  // monogram in place rather than showing another company's mark.
  const missing = await handler.fetch(
    request('/api/company-logo?domain=nothing-here.example&alt=1', undefined, 'GET')
  );
  assert.equal(missing.status, 404);

  // Only well-formed domains reach the network at all.
  const hostile = await handler.fetch(
    request('/api/company-logo?domain=' + encodeURIComponent('a b/../evil'), undefined, 'GET')
  );
  assert.equal(hostile.status, 404);
});

test('authenticated cross-site and simple-content posts are refused', async () => {
  const crossSite = request(
    '/api/provider',
    { provider: 'openai', target: 'documents', action: 'connect' },
  );
  crossSite.headers.set('origin', 'https://hostile.example');
  assert.equal((await handler.fetch(crossSite)).status, 403);

  const simple = request('/api/apply', { applicationId: 'guessed-id' });
  simple.headers.set('content-type', 'text/plain');
  assert.equal((await handler.fetch(simple)).status, 415);

  const noToken = request('/api/scan', {});
  noToken.headers.delete('x-jobby-request');
  assert.equal((await handler.fetch(noToken)).status, 403);
  assert.equal(values.size, 0);
});

test('provider connection requests use only the local device queue', async () => {
  const response = await handler.fetch(
    request('/api/provider', { provider: 'openai', target: 'local', action: 'connect' })
  );
  assert.equal(response.status, 202);
  const refused = await handler.fetch(
    request('/api/provider', { provider: 'gemini', target: 'documents', action: 'connect' })
  );
  assert.equal(refused.status, 400);

  const local = JSON.parse(values.get('jobby:queue:providerLocal'));
  assert.equal(local[0].provider, 'openai');
  assert.equal(values.has('jobby:queue:providerDocuments'), false);
  assert.equal(values.has('jobby:queue'), false);
});

test('legacy target both maps to the one local device queue', async () => {
  const response = await handler.fetch(
    request('/api/provider', { provider: 'anthropic', target: 'both', action: 'connect' })
  );
  assert.equal(response.status, 202);

  const local = JSON.parse(values.get('jobby:queue:providerLocal'));
  assert.equal(local.length, 1);
  assert.equal(local[0].provider, 'anthropic');
  assert.equal(local[0].action, 'connect');
  assert.match(local[0].connectionKey,
    /^1-[0-9]{13}-[0-9a-f]{8}-[0-9a-f-]{27}$/);
  assert.equal(values.has('jobby:queue:providerDocuments'), false);
  assert.equal(values.has('jobby:queue'), false);
});

test('only the retry endpoint marks an Apply request as an explicit retry', async () => {
  let response = await handler.fetch(
    request('/api/apply', { applicationId: 'first-application' })
  );
  assert.equal(response.status, 202);
  response = await handler.fetch(
    request('/api/retry', { applicationId: 'second-application' })
  );
  assert.equal(response.status, 202);

  const queue = JSON.parse(values.get('jobby:queue'));
  assert.equal(queue.apply[0].retry, undefined);
  assert.equal(queue.apply[1].retry, true);
});

test('unknown providers and targets are rejected before a queue write', async () => {
  const badProvider = await handler.fetch(
    request('/api/provider', { provider: 'arbitrary-cli', target: 'local', action: 'connect' })
  );
  assert.equal(badProvider.status, 400);
  const badTarget = await handler.fetch(
    request('/api/provider', { provider: 'openai', target: 'some-host', action: 'connect' })
  );
  assert.equal(badTarget.status, 400);
  assert.equal(values.size, 0);
});

test('provider status returns only the published local device status', async () => {
  const checkedAt = new Date().toISOString();
  const local = {
    checkedAt,
    activeProvider: 'openai',
    providers: [{ id: 'openai', enabled: true, status: 'connected' }],
  };
  values.set('jobby:board', JSON.stringify({ html: '<!doctype html>', providers: local }));

  const response = await handler.fetch(request('/api/providers', undefined, 'GET'));
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.local, local);
  assert.equal('documents' in body, false);
});

test('stale local status is reported as offline', async () => {
  await putBoard(process.env, '<!doctype html>', '2026-01-01T00:00:00Z', {
    checkedAt: '2026-01-01T00:00:00Z', providers: [],
  });
  values.set('jobby:worker:local:status', JSON.stringify({
    checkedAt: '2026-01-01T00:00:00Z', providers: [],
  }));
  const response = await handler.fetch(request('/api/providers', undefined, 'GET'));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).local, null);
});

test('live Mac status takes precedence over the last board publication', async () => {
  const checkedAt = new Date().toISOString();
  const stale = {
    checkedAt,
    activeProvider: null,
    providers: [{ id: 'openai', enabled: false, status: 'disconnected' }],
  };
  const live = {
    checkedAt,
    activeProvider: 'openai',
    providers: [{ id: 'openai', enabled: true, status: 'connected' }],
  };
  values.set('jobby:board', JSON.stringify({ html: '<!doctype html>', providers: stale }));
  values.set('jobby:worker:local:status', JSON.stringify(live));

  const response = await handler.fetch(request('/api/providers', undefined, 'GET'));
  const body = await response.json();
  assert.deepEqual(body.local, live);
});

// --- the Profile page ------------------------------------------------------
//
// Everything the Profile page shows arrives here rather than in the page. The
// board at its embed cap measured 3,825,578 bytes against a 3.7 MB margin and
// the Memory Bank is 74 KB, so it never fitted; and 22 of those entries are
// compensation and demographic answers, so it should not have.

async function publishBank(bank) {
  await putBoardState(process.env, JSON.stringify({
    userId: 'account-1',
    profile: { firstName: 'Sahil' },
    sessions: [], history: [], roles: {}, meta: {}, jobs: {},
    memoryBank: {
      userId: 'account-1',
      identities: [{ key: 'us', countryScope: ['US'], isDefault: true,
                     contact: { location: 'Charlotte, NC' }, resumeRef: 'Resume.pdf' }],
      resumeContent: { banner: 'PRODUCT MANAGER' },
      answers: bank,
    },
  }), '2026-08-25T12:00:00Z');
}

test('the Memory Bank stays behind the dashboard password', async () => {
  await publishBank([{ key: 'expected-comp', question: 'Expected compensation?',
                       value: '$185,000', state: 'sensitive', sensitive: true }]);
  const response = await handler.fetch(request('/api/profile', undefined, 'GET', false));
  assert.equal(response.status, 401);
  // And nothing leaked into the challenge body on the way past.
  assert.equal((await response.text()).includes('185,000'), false);
});

test('an authenticated read gets the answers, the identities and the wording', async () => {
  await publishBank([
    { key: 'notice-period', question: 'Notice period?', value: '15 days',
      state: 'confirmed', version: 2 },
    { key: 'office-days', question: 'Days in office?', value: null, state: 'missing',
      tags: ['unanswered'], askedBy: ['acme-pm'] },
  ]);
  const response = await handler.fetch(request('/api/profile', undefined, 'GET'));
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.ok, true);
  assert.equal(payload.answers.length, 2);
  assert.equal(payload.answers[0].value, '15 days');
  assert.equal(payload.answers[1].value, null);
  assert.deepEqual(payload.answers[1].askedBy, ['acme-pm']);
  assert.equal(payload.identities[0].isDefault, true);
  assert.equal(payload.resumeContent.banner, 'PRODUCT MANAGER');
});

test('a sensitive answer is served with its value and its classification', async () => {
  // Reversed from #73 on the owner's instruction: no hidden category on his
  // own board. The classification still travels, because that is what the
  // multi-tenant encryption path selects on (issue #82), and it must not be
  // quietly cleared by a decision about what the page draws.
  await publishBank([{ key: 'expected-comp', question: 'Expected compensation?',
                       value: '$185,000', state: 'sensitive', sensitive: true,
                       dataClass: 'class-2' }]);
  const response = await handler.fetch(request('/api/profile', undefined, 'GET'));
  const payload = await response.json();
  assert.equal(payload.answers[0].value, '$185,000');
  assert.equal(payload.answers[0].sensitive, true);
  assert.equal(payload.answers[0].dataClass, 'class-2');
});

test('the legacy answer field is served as a value', async () => {
  // Four real entries put theirs in `answer`, which is where the field started.
  await publishBank([{ key: 'rate-sql', question: 'Rate yourself on SQL', answer: '9' }]);
  const payload = await (await handler.fetch(request('/api/profile', undefined, 'GET'))).json();
  assert.equal(payload.answers[0].value, '9');
});

test('a bank published by another account is refused, not served', async () => {
  // Single-user today. This is the check that fails closed rather than serving
  // a stranger's answers the day per-user state arrives.
  await putBoardState(process.env, JSON.stringify({
    memoryBank: { userId: 'account-9', answers: [
      { key: 'x', question: 'Q', value: 'someone else’s answer' }] },
  }), '2026-08-25T12:00:00Z');
  const response = await handler.fetch(request('/api/profile', undefined, 'GET'));
  assert.equal(response.status, 403);
  assert.equal((await response.text()).includes('someone'), false);
});

test('a board published before the Profile page existed says so', async () => {
  await putBoardState(process.env, JSON.stringify({ sessions: [] }),
                      '2026-08-25T12:00:00Z');
  const response = await handler.fetch(request('/api/profile', undefined, 'GET'));
  assert.equal(response.status, 409);
  assert.match((await response.json()).error, /Publish again/);
});

test('an answer edit is queued with its version', async () => {
  const response = await handler.fetch(request('/api/answer', {
    editId: 'ans-1', action: 'save', key: 'notice-period',
    question: 'Notice period?', value: '15 days', version: 2,
  }));
  assert.equal(response.status, 202);
  const queue = JSON.parse(values.get('jobby:queue'));
  assert.equal(queue.answerEdits.length, 1);
  assert.equal(queue.answerEdits[0].version, 2);
  assert.equal(queue.answerEdits[0].value, '15 days');
});

test('an answer edit cannot be sent from another site', async () => {
  const bad = new Request('https://board.example/api/answer', {
    method: 'POST',
    headers: { authorization: auth(), origin: 'https://evil.example',
               'content-type': 'application/json', 'x-jobby-request': '1' },
    body: JSON.stringify({ editId: 'ans-1', action: 'save', question: 'Q', value: 'V' }),
  });
  const response = await handler.fetch(bad);
  assert.equal(response.status, 403);
  assert.equal(values.has('jobby:queue'), false);
});

test('an unbounded answer is refused rather than parked in the shared queue', async () => {
  // Every poll reads this key. An unbounded field here is a way to make the
  // whole board slow for everyone.
  const response = await handler.fetch(request('/api/answer', {
    editId: 'ans-1', action: 'save', question: 'Tell us about yourself',
    value: 'x'.repeat(4001),
  }));
  assert.equal(response.status, 400);
  assert.equal(values.has('jobby:queue'), false);
});

test('an edit id that is not a plain token is refused', async () => {
  const response = await handler.fetch(request('/api/answer', {
    editId: '../../etc/passwd', action: 'save', question: 'Q', value: 'V',
  }));
  assert.equal(response.status, 400);
});

test('a delete needs the key it is deleting', async () => {
  const response = await handler.fetch(request('/api/answer', {
    editId: 'del-1', action: 'delete', version: 1,
  }));
  assert.equal(response.status, 400);
});

test('a base resume action is queued and an unknown one is not', async () => {
  const good = await handler.fetch(request('/api/resume', {
    editId: 'res-1', action: 'default', key: 'in',
  }));
  assert.equal(good.status, 202);
  assert.equal(JSON.parse(values.get('jobby:queue')).resumeEdits[0].key, 'in');

  const bad = await handler.fetch(request('/api/resume', {
    editId: 'res-2', action: 'delete-everything', key: 'in',
  }));
  assert.equal(bad.status, 400);
  assert.equal(JSON.parse(values.get('jobby:queue')).resumeEdits.length, 1);
});

test('oversized resume wording is refused', async () => {
  const response = await handler.fetch(request('/api/resume', {
    editId: 'res-3', action: 'content',
    content: { summary: 'x'.repeat(70 * 1024) },
  }));
  assert.equal(response.status, 400);
});

// --- the projection contract ------------------------------------------------
//
// `publishable_state` decides what the device sends and `profilePayload`
// decides what the page receives, and they are one contract written in two
// languages. When they disagree the failure is silent: the device publishes a
// field, the page reads `undefined`, nothing throws, and the board renders
// something plausible and wrong.
//
// That is not hypothetical. `kind`, `category` and `groupKey` shipped to
// production inside the published state, the page was built to read them, the
// whole suite passed, and the Memory Bank drew 156 uncategorised rows because
// this allowlist had not been told about them. `resumeRender` went the same way
// and the resume editor said it had nothing to edit.

test('every field the device publishes on an answer reaches the page', async () => {
  // One record carrying every field publishable_state can put on an answer.
  const complete = {
    key: 'notice-period', question: 'Notice period?', value: '15 days',
    state: 'confirmed', tags: ['logistics'], updatedAt: '2026-08-27T00:00:00Z',
    version: 2, sensitive: false, dataClass: 'class-1', askedBy: ['acme-pm'],
    openedAt: '2026-08-26T00:00:00Z', lastAskedAt: '2026-08-26T00:00:00Z',
    answeredAt: '2026-08-27T00:00:00Z', note: 'a note', remembered: true,
    kind: 'question', category: 'availability', groupKey: 'notice-period|15 days',
  };
  await publishBank([complete]);
  const payload = await (await handler.fetch(request('/api/profile', undefined, 'GET'))).json();
  const served = payload.answers[0];
  const dropped = Object.keys(complete).filter((field) => served[field] === undefined);
  assert.deepEqual(dropped, [],
    'the device publishes these and the route drops them: ' + dropped.join(', '));
});

test('the rendered resume reaches the editor', async () => {
  await publishBank([]);
  const payload = await (await handler.fetch(request('/api/profile', undefined, 'GET'))).json();
  assert.equal(typeof payload.resumeRender, 'string');
});

test('the three classification fields specifically survive the route', async () => {
  // Named separately from the sweep above, because these are the ones the
  // Memory Bank cannot draw a single category or collapse a single row without.
  await publishBank([
    { key: 'a', question: 'Do you require visa sponsorship?', value: 'Yes',
      kind: 'question', category: 'work-authorisation',
      groupKey: 'sponsorship-required|yes' },
    { key: 'b', question: 'POLICY: how is relocation answered?', value: 'Always yes',
      kind: 'policy' },
  ]);
  const payload = await (await handler.fetch(request('/api/profile', undefined, 'GET'))).json();
  assert.equal(payload.answers[0].kind, 'question');
  assert.equal(payload.answers[0].category, 'work-authorisation');
  assert.equal(payload.answers[0].groupKey, 'sponsorship-required|yes');
  assert.equal(payload.answers[1].kind, 'policy');
});
