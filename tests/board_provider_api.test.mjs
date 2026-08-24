import test from 'node:test';
import assert from 'node:assert/strict';

process.env.CF_ACCOUNT_ID = 'account';
process.env.CF_KV_NAMESPACE_ID = 'namespace';
process.env.CF_API_TOKEN = 'token';
process.env.DASHBOARD_PASSWORD = 'test-password';

const values = new Map();
const originalFetch = globalThis.fetch;

globalThis.fetch = async (input, options = {}) => {
  const url = new URL(input);
  const method = options.method || 'GET';
  if (url.hostname === 'www.google.com' && url.pathname === '/s2/favicons') {
    return new Response(Uint8Array.from([137, 80, 78, 71]), {
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
const { putBoard } = await import('../deploy/kvcontent.mjs');

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
