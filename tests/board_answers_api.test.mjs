// An answer record served through the document route, which is the only route.
//
// A record is the same class of per-application artifact the resume and the
// cover letter are: same R2 key layout, same filename index, same password
// gate, same dual-read. It has no route of its own, because a second serving
// path is a second auth gate to keep honest. What is worth testing here is the
// part a PDF never exercised: the content type comes from the index entry, so a
// JSON record has to arrive as JSON, and a miss has to be distinguishable from
// a record by something other than the status code alone.

import test from 'node:test';
import assert from 'node:assert/strict';

process.env.CF_ACCOUNT_ID = 'account';
process.env.CF_KV_NAMESPACE_ID = 'namespace';
process.env.CF_API_TOKEN = 'token';
process.env.DASHBOARD_PASSWORD = 'test-password';
process.env.R2_ACCESS_KEY_ID = '0'.repeat(32);
process.env.R2_SECRET_ACCESS_KEY = 'f'.repeat(64);

const values = new Map();
const objects = new Map();
const originalFetch = globalThis.fetch;

globalThis.fetch = async (input, options = {}) => {
  const url = new URL(input);
  const method = options.method || 'GET';
  if (url.hostname.endsWith('.r2.cloudflarestorage.com')) {
    const key = decodeURIComponent(url.pathname.replace('/jobby-artifacts/', ''));
    if (!objects.has(key)) return new Response('', { status: 404 });
    const body = objects.get(key);
    return new Response(body, {
      status: 200,
      headers: { 'content-length': String(body.length), 'accept-ranges': 'bytes' },
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
const { putDoc, putDocIndex } = await import('../deploy/kvcontent.mjs');

function get(path, authenticated = true) {
  const headers = {};
  if (authenticated) {
    headers.authorization = `Basic ${Buffer.from('user:test-password').toString('base64')}`;
  }
  return new Request(`https://board.example${path}`, { method: 'GET', headers });
}

const RECORD = {
  schema: 1,
  applicationId: 'bc839847c93d9e46',
  company: 'Acme',
  role: 'Product Manager',
  capturedAt: '2026-08-26T01:00:00Z',
  ats: 'greenhouse',
  fields: [
    { question: 'Are you authorized to work in the US?', answer: 'Yes',
      source: 'stored', readBack: 'match', sensitive: false },
    { question: 'What are your salary expectations?', answer: '165000',
      source: 'stored', readBack: 'match', sensitive: true },
    { question: 'Enter the verification code', answer: '',
      source: 'provided', readBack: 'match', sensitive: true,
      withheld: 'one-time or verification code' },
  ],
};

const KEY = 'u/account-1/app/bc839847c93d9e46/answers.json';
const NAME = 'answers-bc839847c93d9e46.json';

async function publish(record = RECORD) {
  const body = Buffer.from(JSON.stringify(record), 'utf8');
  objects.set(KEY, body);
  await putDocIndex(process.env, {
    [NAME]: {
      key: KEY, sha256: 'a'.repeat(64), bytes: body.length,
      contentType: 'application/json',
    },
  }, '2026-08-26T00:00:00Z');
}

test.after(() => {
  globalThis.fetch = originalFetch;
  delete process.env.R2_ACCESS_KEY_ID;
  delete process.env.R2_SECRET_ACCESS_KEY;
});

test.beforeEach(async () => {
  values.clear();
  objects.clear();
  process.env.R2_ACCESS_KEY_ID = '0'.repeat(32);
  process.env.R2_SECRET_ACCESS_KEY = 'f'.repeat(64);
  await putDocIndex(process.env, {}, '2026-08-26T00:00:00Z');
});

test('a record comes back from R2 as JSON, byte for byte', async () => {
  await publish();
  const response = await handler.fetch(get(`/doc/${NAME}`));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'application/json');
  const body = await response.text();
  assert.equal(body, JSON.stringify(RECORD));
  const parsed = JSON.parse(body);
  assert.equal(parsed.fields.length, 3);
  assert.equal(parsed.fields[1].sensitive, true);
  assert.equal(parsed.fields[2].withheld, 'one-time or verification code');
  assert.equal(parsed.fields[2].answer, '');
  // Proxied, not redirected: the same property the PDFs have.
  assert.equal(response.headers.get('location'), null);
});

test('a record is behind the same password as the board', async () => {
  await publish();
  const response = await handler.fetch(get(`/doc/${NAME}`, false));
  assert.equal(response.status, 401);
});

test('an application with no record answers 404, not an empty record', async () => {
  const response = await handler.fetch(get(`/doc/${NAME}`));
  assert.equal(response.status, 404);
  // The modal checks the type as well as the status, because a route that can
  // serve several kinds of file should not be trusted to make 404 the only miss.
  assert.match(response.headers.get('content-type') || '', /text\/plain/);
});

test('a JSON record in the KV fallback is not labelled application/pdf', async () => {
  // Nothing is in KV today -- there was no backfill, because there were no
  // records to back fill. But the fallback hardcoded PDF, and a record served
  // as application/pdf is a file the browser refuses rather than a page anyone
  // can read, so the default is now taken from the name.
  await putDoc(process.env, NAME,
    Buffer.from(JSON.stringify(RECORD), 'utf8').toString('base64'));
  const response = await handler.fetch(get(`/doc/${NAME}`));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'application/json');
  assert.equal(JSON.parse(await response.text()).fields.length, 3);
});

test('a PDF still comes back as a PDF from both paths', async () => {
  const pdfKey = 'u/account-1/app/bc839847c93d9e46/resume.pdf';
  objects.set(pdfKey, Buffer.from('%PDF-1.4 resume'));
  await putDocIndex(process.env, {
    'Sahil_Acme_Resume.pdf': {
      key: pdfKey, sha256: 'b'.repeat(64), bytes: 15, contentType: 'application/pdf',
    },
  }, '2026-08-26T00:00:01Z');
  const fromR2 = await handler.fetch(get('/doc/Sahil_Acme_Resume.pdf'));
  assert.equal(fromR2.headers.get('content-type'), 'application/pdf');

  await putDoc(process.env, 'Older_Resume.pdf',
    Buffer.from('%PDF-1.4 old', 'utf8').toString('base64'));
  const fromKv = await handler.fetch(get('/doc/Older_Resume.pdf'));
  assert.equal(fromKv.headers.get('content-type'), 'application/pdf');
});

test('a hostile question survives as data and never as a document', async () => {
  // Every question came off somebody else's form. The route must not be a place
  // where one becomes markup: application/json plus nosniff is what makes the
  // browser treat it as data, and the page then builds every row through
  // textContent.
  await publish({
    ...RECORD,
    fields: [{ question: '</script><img src=x onerror=alert(1)>', answer: '"><b>hi',
               source: 'provided', readBack: 'match', sensitive: false }],
  });
  const response = await handler.fetch(get(`/doc/${NAME}`));
  assert.equal(response.headers.get('content-type'), 'application/json');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  const parsed = JSON.parse(await response.text());
  assert.equal(parsed.fields[0].question, '</script><img src=x onerror=alert(1)>');
});

test('there is no second route serving answer records', async () => {
  // One path, one gate. /api/answers existed on this branch before the R2
  // migration landed and is gone; if it comes back it is a second auth check
  // and a second dual-read to keep in step with this one.
  await publish();
  const response = await handler.fetch(
    get('/api/answers?id=bc839847c93d9e46'));
  assert.notEqual(response.status, 200);
});
