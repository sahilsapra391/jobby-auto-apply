// v2's by-name document read, which is v1's `/doc/<filename>` ported into
// apps/web so the documents v1 opened still open after deploy/ is deleted.
//
// The same five behaviours r2_doc_route.test.mjs pins for v1, asserted against
// apps/web/app/_ui/documents.mjs rather than restated:
//   - the index knows the file        -> the bytes, read from R2
//   - the index does not know it      -> the KV copy
//   - R2 404s on an indexed key       -> KV, because the index is ahead of the bucket
//   - R2 faults and KV is empty       -> 502 naming the fault, not 404
//   - nothing anywhere                -> 404, the only honest one
// plus the third state for the row: "could not look" is null, never an empty set.

import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const env = {
  CF_ACCOUNT_ID: 'account', CF_KV_NAMESPACE_ID: 'namespace', CF_API_TOKEN: 'token',
  R2_ACCESS_KEY_ID: '0'.repeat(32), R2_SECRET_ACCESS_KEY: 'f'.repeat(64),
};

const values = new Map();
const objects = new Map();
let r2Faults = null;
let kvFaults = null;
let lastR2Request = null;
const originalFetch = globalThis.fetch;

globalThis.fetch = async (input, options = {}) => {
  const url = new URL(input);
  const method = options.method || 'GET';
  if (url.hostname.endsWith('.r2.cloudflarestorage.com')) {
    lastR2Request = url.toString();
    if (r2Faults) return new Response('upstream said no', { status: r2Faults });
    const key = decodeURIComponent(url.pathname.replace('/jobby-artifacts/', ''));
    if (!objects.has(key)) return new Response('', { status: 404 });
    const body = objects.get(key);
    const range = (options.headers || {}).range;
    if (range) {
      const [start, end] = range.replace('bytes=', '').split('-').map(Number);
      const slice = body.subarray(start, end + 1);
      return new Response(slice, { status: 206, headers: {
        'content-range': `bytes ${start}-${end}/${body.length}`,
        'content-length': String(slice.length), 'accept-ranges': 'bytes' } });
    }
    return new Response(body, { status: 200, headers: {
      'content-length': String(body.length), 'accept-ranges': 'bytes' } });
  }
  if (method !== 'GET') throw new Error(`the read path sent a ${method}`);
  if (kvFaults) return new Response('kv said no', { status: kvFaults });
  const keys = '/storage/kv/namespaces/namespace/keys';
  if (url.pathname.endsWith(keys)) {
    const prefix = url.searchParams.get('prefix') || '';
    const result = [...values.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name }));
    return Response.json({ result, result_info: { cursor: '' } });
  }
  const marker = '/storage/kv/namespaces/namespace/values/';
  const position = url.pathname.indexOf(marker);
  if (position < 0) return new Response('', { status: 404 });
  const key = decodeURIComponent(url.pathname.slice(position + marker.length));
  return values.has(key)
    ? new Response(values.get(key), { status: 200 })
    : new Response('', { status: 404 });
};

const docs = await import('../apps/web/app/_ui/documents.mjs');

function index(entries) {
  values.set(docs.DOC_INDEX_KEY, JSON.stringify({
    schemaVersion: 1, generatedAt: '2026-09-12T05:53:38Z', docs: entries }));
  docs.resetCaches();
}
function kvCopy(name, text) {
  values.set(docs.docKey(name), Buffer.from(text).toString('base64'));
  docs.resetCaches();
}
async function body(read) {
  return Buffer.from(await new Response(read.body).arrayBuffer()).toString();
}

test.after(() => { globalThis.fetch = originalFetch; });

test.beforeEach(() => {
  values.clear();
  objects.clear();
  r2Faults = null;
  kvFaults = null;
  lastR2Request = null;
  docs.resetCaches();
});

test('an indexed document is read from R2 under the key the index names', async () => {
  // The shared-name shape: three applications recorded one name, the file sits
  // under one of them, and the other two have nothing at their own key.
  const key = 'u/account-1/app/04f960acfe932252/resume.pdf';
  objects.set(key, Buffer.from('%PDF the stored resume'));
  index({ 'Alex_Example_Acme_Resume.pdf': { key, contentType: 'application/pdf' } });
  const read = await docs.readByName(env, 'Alex_Example_Acme_Resume.pdf');
  assert.equal(read.ok, true);
  assert.equal(read.status, 200);
  assert.equal(read.contentType, 'application/pdf');
  assert.equal(await body(read), '%PDF the stored resume');
});

test('nothing signed reaches the caller', async () => {
  const key = 'u/account-1/app/aa/resume.pdf';
  objects.set(key, Buffer.from('%PDF'));
  index({ 'r.pdf': { key } });
  const read = await docs.readByName(env, 'r.pdf');
  const serialised = JSON.stringify(read.headers) + (await body(read));
  for (const secret of [env.R2_SECRET_ACCESS_KEY, env.R2_ACCESS_KEY_ID,
                        'X-Amz-Signature', 'r2.cloudflarestorage.com']) {
    assert.ok(!serialised.includes(secret), `leaked ${secret}`);
  }
  assert.ok(lastR2Request.includes('X-Amz-Signature='));
});

test('a range is forwarded so a phone can page a PDF', async () => {
  const key = 'u/account-1/app/aa/resume.pdf';
  objects.set(key, Buffer.from('0123456789'));
  index({ 'r.pdf': { key } });
  const read = await docs.readByName(env, 'r.pdf', 'bytes=2-5');
  assert.equal(read.status, 206);
  assert.equal(read.headers['content-range'], 'bytes 2-5/10');
  assert.equal(await body(read), '2345');
});

test('R2 first: an indexed document does not serve a stale KV copy', async () => {
  const key = 'u/account-1/app/aa/resume.pdf';
  objects.set(key, Buffer.from('%PDF fresh'));
  index({ 'Both.pdf': { key } });
  kvCopy('Both.pdf', '%PDF stale');
  assert.equal(await body(await docs.readByName(env, 'Both.pdf')), '%PDF fresh');
});

test('an indexed key the bucket does not have falls through to KV', async () => {
  index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf' } });
  kvCopy('Both.pdf', '%PDF from kv');
  const read = await docs.readByName(env, 'Both.pdf');
  assert.equal(read.status, 200);
  assert.equal(await body(read), '%PDF from kv');
});

test('a document the index never learned still comes from KV', async () => {
  index({});
  kvCopy('Legacy_Resume.pdf', '%PDF legacy');
  const read = await docs.readByName(env, 'Legacy_Resume.pdf');
  assert.equal(read.status, 200);
  assert.equal(read.contentType, 'application/pdf');
  assert.equal(await body(read), '%PDF legacy');
});

test('an R2 fault with a KV copy behind it still serves the document', async () => {
  index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf' } });
  kvCopy('Both.pdf', '%PDF from kv');
  r2Faults = 500;
  assert.equal(await body(await docs.readByName(env, 'Both.pdf')), '%PDF from kv');
});

test('an R2 fault with nothing in KV is a 502 naming the fault, not a 404', async () => {
  index({ 'Gone.pdf': { key: 'u/account-1/app/aa/resume.pdf' } });
  r2Faults = 403;
  const read = await docs.readByName(env, 'Gone.pdf');
  assert.equal(read.status, 502);
  assert.match(read.error, /R2 answered 403/);
  assert.ok(!read.error.includes(env.R2_SECRET_ACCESS_KEY));
});

test('an unreadable index is a fault, not a missing document', async () => {
  kvFaults = 500;
  const read = await docs.readByName(env, 'Anything.pdf');
  assert.equal(read.status, 502);
  assert.match(read.error, /index could not be read/);
});

test('an indexed document with R2 unconfigured and no KV copy is 503, not 404', async () => {
  index({ 'Only_In_R2.pdf': { key: 'u/account-1/app/aa/resume.pdf' } });
  const { R2_SECRET_ACCESS_KEY, ...without } = env;
  const read = await docs.readByName(without, 'Only_In_R2.pdf');
  assert.equal(read.status, 503);
});

test('with R2 unconfigured the KV copy still serves', async () => {
  index({ 'Both.pdf': { key: 'u/account-1/app/aa/resume.pdf' } });
  kvCopy('Both.pdf', '%PDF good');
  const { R2_SECRET_ACCESS_KEY, ...without } = env;
  assert.equal(await body(await docs.readByName(without, 'Both.pdf')), '%PDF good');
});

test('a document that exists nowhere is a plain 404', async () => {
  index({});
  assert.deepEqual(await docs.readByName(env, 'Nothing.pdf'), { ok: false, status: 404 });
});

test('a name that is not a plain filename is refused before anything is read', async () => {
  for (const name of ['../../etc/passwd', 'a/b.pdf', '', '..', 'x'.repeat(201) + '.pdf',
                      'a\nb.pdf']) {
    const read = await docs.readByName(env, name);
    assert.equal(read.status, 404, JSON.stringify(name));
  }
  assert.equal(lastR2Request, null);
});

test('a filename with spaces in it is served, not refused', async () => {
  const name = 'Alex Example - Resume - Senior Project Manager.pdf';
  const key = 'u/account-1/app/bd7c44165d98d10a/resume.pdf';
  objects.set(key, Buffer.from('%PDF spaced'));
  index({ [name]: { key } });
  assert.equal(await body(await docs.readByName(env, name)), '%PDF spaced');
});

test('a hostile key in the index cannot escape the user prefix', async () => {
  for (const key of ['../../other-bucket/secret', 'u/account-1/\nx', '/etc/passwd',
                     'x'.repeat(2000), 'u/account-1/app/../../../other-user/resume.pdf',
                     'u/account-1/..', 'u/../account-2/app/a/resume.pdf']) {
    index({ 'Bad.pdf': { key } });
    lastR2Request = null;
    const read = await docs.readByName(env, 'Bad.pdf');
    assert.equal(read.status, 404, key);
    assert.equal(lastR2Request, null, key);
  }
});

test('the content type comes from the index, then the name, never from hostile text', async () => {
  objects.set('u/account-1/app/aa/answers.json', Buffer.from('{}'));
  index({ 'answers.json': { key: 'u/account-1/app/aa/answers.json',
                            contentType: 'application/json' } });
  assert.equal((await docs.readByName(env, 'answers.json')).contentType, 'application/json');
  for (const bad of ['text/html<script>', 'application/pdf\r\nX-Evil: 1', 42]) {
    assert.equal(docs.contentTypeFor('x.pdf', { contentType: bad }), 'application/pdf');
  }
  assert.equal(docs.contentTypeFor('a.json', null), 'application/json');
  assert.equal(docs.contentTypeFor('a.html', null), 'text/html; charset=utf-8');
});

test('servableNames lists what the index or KV can open, and nothing else', async () => {
  index({ 'Indexed.pdf': { key: 'u/account-1/app/aa/resume.pdf' },
          'Hostile.pdf': { key: '../../x' } });
  kvCopy('KvOnly.pdf', '%PDF');
  const names = await docs.servableNames(env, ['Indexed.pdf', 'KvOnly.pdf', 'Hostile.pdf',
                                               'Missing.pdf', '../x.pdf']);
  assert.deepEqual([...names].sort(), ['Indexed.pdf', 'KvOnly.pdf']);
});

test('servableNames is null when it could not look, never an empty set', async () => {
  // An empty set would turn every row into "not stored": a claim about
  // documents drawn from a fault in this deployment.
  kvFaults = 500;
  assert.equal(await docs.servableNames(env, ['Indexed.pdf']), null);
  assert.equal(await docs.servableNames({}, ['Indexed.pdf']), null);
});

test('a missing index is an empty answer, not a fault', async () => {
  // jobby:doc-index is a frozen snapshot publish.py wrote; a namespace without
  // one has nothing indexed, which is a fact about KV rather than a failure.
  const names = await docs.servableNames(env, ['Indexed.pdf']);
  assert.deepEqual([...names], []);
});

test('basename takes the last path segment of a recorded value', () => {
  assert.equal(docs.basename('applications/Initech/Alex_Example_Initech_Resume.pdf'),
               'Alex_Example_Initech_Resume.pdf');
  assert.equal(docs.basename('Plain.pdf'), 'Plain.pdf');
  assert.equal(docs.basename(null), '');
});

test('the by-name path is plain source in apps/web, not generated from deploy/', () => {
  // v1 is about to be deleted. A copy generated from it would break the board
  // the moment the deletion lands.
  const source = readFileSync(new URL('../apps/web/app/_ui/documents.mjs', import.meta.url), 'utf8');
  assert.ok(!/GENERATED/.test(source.split('\n')[0]));
  assert.ok(!source.includes('../../deploy') && !source.includes("from 'deploy"));
});

test('both doors to the serving path are tracked by git', () => {
  // A route git ignores and a route nobody wrote answer identically from CI,
  // which is how applications/ unanchored once ate the write endpoint.
  for (const path of ['apps/web/app/doc/[name]/route.ts',
                      'apps/web/app/api/artifact/route.ts',
                      'apps/web/app/_ui/documents.mjs']) {
    const out = execFileSync('git', ['--literal-pathspecs', 'ls-files', '--error-unmatch', path],
                             { cwd: new URL('..', import.meta.url), encoding: 'utf8' });
    assert.equal(out.trim(), path);
  }
});

test('both doors call the one serving function', () => {
  for (const path of ['../apps/web/app/doc/[name]/route.ts',
                      '../apps/web/app/api/artifact/route.ts']) {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8');
    assert.match(source, /import \{[^}]*readByName[^}]*\} from "\.\.\/\.\.\/_ui\/documents\.mjs"/, path);
    assert.match(source, /await readByName\(process\.env/, path);
  }
});
