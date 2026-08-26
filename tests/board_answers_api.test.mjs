// The route that serves one application's answer record to the Submitted modal.
//
// Records live in the same KV space as the resume and cover letter PDFs, which
// is the point -- one publish path, one write-when-changed stamp -- but they
// are not PDFs and must not be served as if they were.

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
  const marker = '/storage/kv/namespaces/namespace/values/';
  const position = url.pathname.indexOf(marker);
  if (position < 0) return new Response('', { status: 404 });
  const key = decodeURIComponent(url.pathname.slice(position + marker.length));
  if (method === 'GET') {
    return values.has(key)
      ? new Response(values.get(key), { status: 200 })
      : new Response('', { status: 404 });
  }
  return new Response('', { status: 405 });
};

const { default: handler } = await import('../deploy/api/board.js');
const { docKey } = await import('../deploy/kvcontent.mjs');

function auth() {
  return `Basic ${Buffer.from('user:test-password').toString('base64')}`;
}

function get(path, authenticated = true) {
  const headers = { origin: 'https://board.example' };
  if (authenticated) headers.authorization = auth();
  return new Request(`https://board.example${path}`, { method: 'GET', headers });
}

function publishRecord(id, record) {
  values.set(docKey(`answers-${id}.json`),
    Buffer.from(JSON.stringify(record), 'utf8').toString('base64'));
}

const RECORD = {
  schema: 1,
  applicationId: 'abc123',
  company: 'Acme',
  role: 'Product Manager',
  capturedAt: '2026-08-26T01:00:00Z',
  ats: 'greenhouse',
  fields: [
    { question: 'Are you authorized to work in the US?', answer: 'Yes',
      source: 'stored', readBack: 'match', sensitive: false },
    { question: 'What are your salary expectations?', answer: '165000',
      source: 'stored', readBack: 'match', sensitive: true },
  ],
};

test.after(() => { globalThis.fetch = originalFetch; });
test.beforeEach(() => { values.clear(); });

test('an answer record is served as JSON behind the board password', async () => {
  publishRecord('abc123', RECORD);
  const response = await handler.fetch(get('/api/answers?id=abc123'));
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type') || '', /application\/json/);
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.captured, true);
  assert.equal(body.record.fields.length, 2);
  assert.equal(body.record.fields[1].sensitive, true);
});

test('the answers route needs the same password as the board', async () => {
  publishRecord('abc123', RECORD);
  const response = await handler.fetch(get('/api/answers?id=abc123', false));
  assert.equal(response.status, 401);
});

test('an application with no published record answers 404, not an empty modal', async () => {
  const response = await handler.fetch(get('/api/answers?id=abc123'));
  assert.equal(response.status, 404);
  const body = await response.json();
  assert.equal(body.captured, false);
  assert.equal(body.record, undefined);
});

test('a malformed record is a miss rather than something the page renders', async () => {
  values.set(docKey('answers-abc123.json'),
    Buffer.from('{"fields":"not a list"}', 'utf8').toString('base64'));
  const response = await handler.fetch(get('/api/answers?id=abc123'));
  assert.equal(response.status, 404);
  values.set(docKey('answers-abc123.json'), 'not base64 json at all !!!');
  const second = await handler.fetch(get('/api/answers?id=abc123'));
  assert.equal(second.status, 404);
});

test('the id is a fixed shape, so it cannot reach another KV key', async () => {
  for (const id of ['../board-state', 'a b', 'abc/def', '', 'x'.repeat(65),
                    'jobby:board']) {
    const response = await handler.fetch(
      get(`/api/answers?id=${encodeURIComponent(id)}`));
    assert.equal(response.status, 400, `id ${JSON.stringify(id)} should be refused`);
  }
});

test('a hostile question is served as JSON data, never as a document', async () => {
  // Every question came off somebody else's form. The route must not be a
  // place where one becomes markup: JSON encoding plus the JSON content type
  // is what makes the browser treat it as data, and the page then builds every
  // row through textContent.
  publishRecord('abc123', {
    ...RECORD,
    fields: [{ question: '</script><img src=x onerror=alert(1)>', answer: '"><b>hi',
               source: 'provided', readBack: 'match', sensitive: false }],
  });
  const response = await handler.fetch(get('/api/answers?id=abc123'));
  assert.match(response.headers.get('content-type') || '', /application\/json/);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  const body = await response.json();
  assert.equal(body.record.fields[0].question, '</script><img src=x onerror=alert(1)>');
});

test('an answer record cannot be fetched through the PDF document route', async () => {
  // Same KV space, different content type. Without the .pdf requirement this
  // returned a JSON record labelled application/pdf.
  publishRecord('abc123', RECORD);
  const response = await handler.fetch(get('/doc/answers-abc123.json'));
  assert.equal(response.status, 404);
});

test('the PDF route still serves a PDF', async () => {
  values.set(docKey('Sahil_Acme_Resume.pdf'),
    Buffer.from('%PDF-1.4 test', 'utf8').toString('base64'));
  const response = await handler.fetch(get('/doc/Sahil_Acme_Resume.pdf'));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'application/pdf');
});
