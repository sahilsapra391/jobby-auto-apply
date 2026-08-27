// The /doc/<filename> route, which is the only thing on the board that changed
// shape when artifacts moved to R2.
//
// Five behaviours are load-bearing and each of them is a way this could serve
// nothing without saying so:
//   - the index knows the file        -> the bytes, read from R2 by the function
//   - the index does not know it yet  -> the KV copy, unchanged
//   - R2 404s on an indexed key       -> KV, because the index is ahead of the bucket
//   - R2 faults and KV is empty       -> an error the reader can act on, not 404
//   - nothing anywhere                -> 404, which is the only honest 404 here
//
// And one that is a security property rather than an availability one: no
// signed URL may appear in anything the browser receives.

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

// Objects the fake bucket holds, by key. `r2Faults` forces a status instead.
const objects = new Map();
let r2Faults = null;
let lastR2Request = null;

globalThis.fetch = async (input, options = {}) => {
  const url = new URL(input);
  const method = options.method || 'GET';
  if (url.hostname.endsWith('.r2.cloudflarestorage.com')) {
    lastR2Request = { url: url.toString(), headers: options.headers || {} };
    if (r2Faults) return new Response('upstream said no', { status: r2Faults });
    const key = decodeURIComponent(url.pathname.replace('/jobby-artifacts/', ''));
    if (!objects.has(key)) return new Response('', { status: 404 });
    const body = objects.get(key);
    const range = (options.headers || {}).range;
    if (range) {
      const [start, end] = range.replace('bytes=', '').split('-').map(Number);
      const slice = body.subarray(start, (end || body.length - 1) + 1);
      return new Response(slice, { status: 206, headers: {
        'content-range': `bytes ${start}-${end || body.length - 1}/${body.length}`,
        'content-length': String(slice.length), 'accept-ranges': 'bytes' } });
    }
    return new Response(body, { status: 200, headers: {
      'content-length': String(body.length), 'accept-ranges': 'bytes' } });
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
  objects.clear();
  r2Faults = null;
  lastR2Request = null;
  process.env.R2_ACCESS_KEY_ID = '0'.repeat(32);
  process.env.R2_SECRET_ACCESS_KEY = 'f'.repeat(64);
  await index({});
});

test('a document link is still behind the dashboard password', async () => {
  const response = await handler.fetch(get('/doc/Sahil_Sapra_Acme_Resume.pdf', false));
  assert.equal(response.status, 401);
});

test('an indexed artifact is read from R2 and returned as bytes', async () => {
  const key = 'u/account-1/app/bc839847c93d9e46/resume.pdf';
  objects.set(key, Buffer.from('%PDF-1.4 the real resume'));
  await index({
    'Sahil_Sapra_Acme_Resume.pdf': {
      key, sha256: 'a'.repeat(64), bytes: 24, contentType: 'application/pdf',
    },
  });
  const response = await handler.fetch(get('/doc/Sahil_Sapra_Acme_Resume.pdf'));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'application/pdf');
  assert.equal(response.headers.get('content-disposition'),
    'inline; filename="Sahil_Sapra_Acme_Resume.pdf"');
  assert.equal(await response.text(), '%PDF-1.4 the real resume');
  // One round trip for the reader. The redirect this replaced cost two.
  assert.equal(response.headers.get('location'), null);
});

test('nothing signed reaches the browser', async () => {
  // The whole point of proxying rather than redirecting. A signed URL in a
  // response is one in browser history, in a screenshot, and in a shared link,
  // and it keeps working after the reader loses access to the board.
  const key = 'u/account-1/app/aa/resume.pdf';
  objects.set(key, Buffer.from('%PDF'));
  await index({ 'r.pdf': { key, contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/r.pdf'));
  const serialised = JSON.stringify([...response.headers]) + (await response.text());
  for (const secret of [process.env.R2_SECRET_ACCESS_KEY, process.env.R2_ACCESS_KEY_ID,
                        'X-Amz-Signature', 'r2.cloudflarestorage.com']) {
    assert.ok(!serialised.includes(secret), `leaked ${secret}`);
  }
  // It did happen, just on the far side of the function.
  assert.ok(lastR2Request.url.includes('X-Amz-Signature='));
});

test('a range request is forwarded so a phone can page a PDF', async () => {
  const key = 'u/account-1/app/aa/resume.pdf';
  objects.set(key, Buffer.from('0123456789'));
  await index({ 'r.pdf': { key, contentType: 'application/pdf' } });
  const request = new Request('https://board.example/doc/r.pdf', {
    headers: { authorization: `Basic ${Buffer.from('user:test-password').toString('base64')}`,
               range: 'bytes=2-5' } });
  const response = await handler.fetch(request);
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('content-range'), 'bytes 2-5/10');
  assert.equal(await response.text(), '2345');
});

test('an indexed key the bucket does not have falls through to KV', async () => {
  // The index is ahead of the bucket. That is emptiness, not a fault, so it
  // must not be reported as one while KV may still hold the document.
  await putDoc(process.env, 'Both.pdf', Buffer.from('%PDF from kv').toString('base64'));
  await index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/Both.pdf'));
  assert.equal(response.status, 200);
  assert.equal(await response.text(), '%PDF from kv');
});

test('a document the index has not learned yet still comes from KV', async () => {
  await putDoc(process.env, 'Legacy_Resume.pdf', Buffer.from('%PDF-1.4 legacy').toString('base64'));
  const response = await handler.fetch(get('/doc/Legacy_Resume.pdf'));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'application/pdf');
  assert.equal(await response.text(), '%PDF-1.4 legacy');
});

test('R2 first: an indexed document does not serve a stale KV copy', async () => {
  const key = 'u/account-1/app/aa/resume.pdf';
  objects.set(key, Buffer.from('%PDF fresh'));
  await putDoc(process.env, 'Both.pdf', Buffer.from('%PDF stale').toString('base64'));
  await index({ 'Both.pdf': { key, contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/Both.pdf'));
  assert.equal(await response.text(), '%PDF fresh');
});

test('with R2 unconfigured the KV copy still serves the document', async () => {
  await putDoc(process.env, 'Both.pdf', Buffer.from('%PDF good').toString('base64'));
  await index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  delete process.env.R2_SECRET_ACCESS_KEY;   // configured() now fails, KV answers
  const response = await handler.fetch(get('/doc/Both.pdf'));
  assert.equal(response.status, 200);
  assert.equal(await response.text(), '%PDF good');
});

test('an R2 fault with nothing in KV is an error, not a dead link', async () => {
  await index({ 'Gone.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  r2Faults = 403;   // a revoked or mistyped credential, the realistic case
  const response = await handler.fetch(get('/doc/Gone.pdf'));
  assert.equal(response.status, 502);
  const body = await response.text();
  assert.match(body, /could not be read/);
  assert.match(body, /still on the machine that made it/);
  assert.ok(!body.includes(process.env.R2_SECRET_ACCESS_KEY));
});

test('an R2 fault with a KV copy behind it still serves the document', async () => {
  await putDoc(process.env, 'Both.pdf', Buffer.from('%PDF from kv').toString('base64'));
  await index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  r2Faults = 500;
  const response = await handler.fetch(get('/doc/Both.pdf'));
  assert.equal(response.status, 200);
  assert.equal(await response.text(), '%PDF from kv');
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
  const key = 'u/account-1/app/bd7c44165d98d10a/resume.pdf';
  objects.set(key, Buffer.from('%PDF spaced'));
  await index({ [name]: { key, contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/' + encodeURIComponent(name)));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-disposition'), `inline; filename="${name}"`);
  assert.equal(await response.text(), '%PDF spaced');
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

test('a document is never cached and never indexed', async () => {
  objects.set('u/account-1/app/aa/resume.pdf', Buffer.from('%PDF'));
  await index({ 'r.pdf': { key: 'u/account-1/app/aa/resume.pdf', contentType: 'application/pdf' } });
  const response = await handler.fetch(get('/doc/r.pdf'));
  assert.equal(response.headers.get('cache-control'), 'no-store, max-age=0');
  assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
});
