// The /doc/<filename> route, which is the only thing on the board that changed
// shape when artifacts moved to R2.
//
// Four behaviours are load-bearing and each of them is a way this could serve
// nothing without saying so:
//   - the index knows the file        -> redirect to a presigned URL
//   - the index does not know it yet  -> the KV copy, unchanged
//   - presign fails and KV is empty   -> an error the reader can act on, not 404
//   - nothing anywhere                -> 404, which is the only honest 404 here

import test from 'node:test';
import assert from 'node:assert/strict';

process.env.CF_ACCOUNT_ID = 'account';
process.env.CF_KV_NAMESPACE_ID = 'namespace';
process.env.CF_API_TOKEN = 'token';
process.env.DASHBOARD_PASSWORD = 'test-password';
process.env.R2_ACCESS_KEY_ID = '0'.repeat(32);
process.env.R2_SECRET_ACCESS_KEY = 'f'.repeat(64);

const values = new Map();
const originalFetch = globalThis.fetch;

globalThis.fetch = async (input, options = {}) => {
  const url = new URL(input);
  const method = options.method || 'GET';
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
const { putDoc, putDocIndex, DOC_INDEX_KEY } = await import('../deploy/kvcontent.mjs');

function get(path, authenticated = true) {
  const headers = {};
  if (authenticated) {
    headers.authorization = `Basic ${Buffer.from('user:test-password').toString('base64')}`;
  }
  return new Request(`https://board.example${path}`, { method: 'GET', headers });
}

// The index is cached for twenty seconds inside kvcontent, the same way the
// board is, so every test has to write through putDocIndex rather than poking
// the map, or it reads the previous test's answer.
async function index(docs) {
  await putDocIndex(process.env, docs, '2026-08-25T00:00:00Z');
}

test.after(() => {
  globalThis.fetch = originalFetch;
  delete process.env.R2_ACCESS_KEY_ID;
  delete process.env.R2_SECRET_ACCESS_KEY;
});

test.beforeEach(async () => {
  values.clear();
  process.env.R2_ACCESS_KEY_ID = '0'.repeat(32);
  process.env.R2_SECRET_ACCESS_KEY = 'f'.repeat(64);
  await index({});
});

test('a document link is still behind the dashboard password', async () => {
  const response = await handler.fetch(get('/doc/Sahil_Sapra_Acme_Resume.pdf', false));
  assert.equal(response.status, 401);
});

test('an indexed artifact redirects to a presigned R2 URL', async () => {
  await index({
    'Sahil_Sapra_Acme_Resume.pdf': {
      key: 'u/account-1/app/bc839847c93d9e46/resume.pdf',
      sha256: 'a'.repeat(64), bytes: 129909, contentType: 'application/pdf',
    },
  });
  const response = await handler.fetch(get('/doc/Sahil_Sapra_Acme_Resume.pdf'));
  assert.equal(response.status, 302);
  const location = new URL(response.headers.get('location'));
  assert.equal(location.hostname, 'account.r2.cloudflarestorage.com');
  assert.equal(location.pathname,
    '/jobby-artifacts/u/account-1/app/bc839847c93d9e46/resume.pdf');
  assert.equal(location.searchParams.get('X-Amz-Algorithm'), 'AWS4-HMAC-SHA256');
  assert.equal(location.searchParams.get('X-Amz-Expires'), '300');
  assert.match(location.searchParams.get('X-Amz-Signature'), /^[0-9a-f]{64}$/);
  // The filename the reader sees comes from the link, not from the key, and it
  // is inside the signature so nobody can rewrite it on a URL we minted.
  assert.equal(location.searchParams.get('response-content-disposition'),
    'inline; filename="Sahil_Sapra_Acme_Resume.pdf"');
  assert.equal(location.searchParams.get('response-content-type'), 'application/pdf');
});

test('the secret never appears in the redirect', async () => {
  await index({
    'r.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' },
  });
  const response = await handler.fetch(get('/doc/r.pdf'));
  const location = response.headers.get('location');
  assert.ok(!location.includes(process.env.R2_SECRET_ACCESS_KEY));
  // The access key id is half of the credential scope and is meant to be there;
  // the secret is what must never travel.
  assert.ok(location.includes(process.env.R2_ACCESS_KEY_ID));
});

test('a document the index has not learned yet still comes from KV', async () => {
  await putDoc(process.env, 'Legacy_Resume.pdf', Buffer.from('%PDF-1.4 legacy').toString('base64'));
  const response = await handler.fetch(get('/doc/Legacy_Resume.pdf'));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'application/pdf');
  assert.equal(await response.text(), '%PDF-1.4 legacy');
});

test('R2 first: an indexed document does not fall back to a stale KV copy', async () => {
  await putDoc(process.env, 'Both.pdf', Buffer.from('%PDF stale').toString('base64'));
  await index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/Both.pdf'));
  assert.equal(response.status, 302);
});

test('with R2 unconfigured the KV copy still serves the document', async () => {
  await putDoc(process.env, 'Both.pdf', Buffer.from('%PDF good').toString('base64'));
  await index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  delete process.env.R2_SECRET_ACCESS_KEY;   // configured() now fails, KV answers
  const response = await handler.fetch(get('/doc/Both.pdf'));
  assert.equal(response.status, 200);
  assert.equal(await response.text(), '%PDF good');
});

test('a presign failure with nothing in KV is an error, not a dead link', async () => {
  await index({ 'Gone.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  const { presignGet } = await import('../deploy/r2.mjs');
  // Force the mint itself to throw, which is the shape of a bad credential or
  // a WebCrypto failure rather than a missing one.
  const realSign = crypto.subtle.sign;
  crypto.subtle.sign = async () => { throw new Error('signing unavailable'); };
  try {
    const response = await handler.fetch(get('/doc/Gone.pdf'));
    assert.equal(response.status, 502);
    const body = await response.text();
    assert.match(body, /could not be signed/);
    assert.match(body, /still on the machine that made it/);
    assert.ok(!body.includes(process.env.R2_SECRET_ACCESS_KEY));
  } finally {
    crypto.subtle.sign = realSign;
    void presignGet;
  }
});

test('a document that exists nowhere is a plain 404', async () => {
  const response = await handler.fetch(get('/doc/Nothing.pdf'));
  assert.equal(response.status, 404);
  assert.equal(await response.text(), 'No such document.\n');
});

test('a filename that is not a plain filename is refused before anything is read', async () => {
  for (const path of ['/doc/../../etc/passwd', '/doc/a%2Fb.pdf', '/doc/',
                      '/doc/' + 'x'.repeat(201) + '.pdf']) {
    const response = await handler.fetch(get(path));
    assert.equal(response.status, 404, path);
  }
});

test('a filename with spaces in it is served, not refused', async () => {
  // Ten of the 426 stored artifacts are named this way and two of them are
  // documents an employer received. The old guard rejected the name before
  // anything was looked up, so those two links have always been dead.
  const name = 'Sahil Sapra - Resume - Senior Project Manager.pdf';
  await index({ [name]: { key: 'u/account-1/app/bd7c44165d98d10a/resume.pdf',
                          contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/' + encodeURIComponent(name)));
  assert.equal(response.status, 302);
  assert.equal(new URL(response.headers.get('location'))
    .searchParams.get('response-content-disposition'),
    `inline; filename="${name}"`);
});

test('a hostile key in the index cannot escape the user prefix', async () => {
  // The index is ours, but it arrives through KV and ends up inside a signed
  // URL, so it is checked rather than trusted.
  for (const key of ['../../other-bucket/secret', 'u/account-1/\nx', '/etc/passwd',
                     'x'.repeat(2000), 'u/account-1/app/../../../other-user/resume.pdf',
                     'u/account-1/..', 'u/../account-2/app/a/resume.pdf']) {
    await index({ 'Bad.pdf': { key, contentType: 'application/pdf' } });
    const response = await handler.fetch(get('/doc/Bad.pdf'));
    assert.equal(response.status, 404, key);
  }
});

test('a redirect is never cached and never indexed', async () => {
  await index({ 'r.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/r.pdf'));
  assert.equal(response.headers.get('cache-control'), 'no-store, max-age=0');
  assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
});
