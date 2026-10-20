// A base resume PDF, checked before it is parked for the device.
//
// Two halves. The validation is `planResumeUpload` in `deploy/profile_edits.mjs`,
// run here on real bodies, because the old board and v2 must refuse the same
// files for the same reasons. The route is checked for its order: the write
// gate before the body is planned, the plan before KV is written, and the
// idempotency answer remembered only after the document landed, because a key
// remembered for an upload that never reached KV answers a retry with a
// success nobody earned. `tests/test_resume_upload_contract.py` is the third
// half: that what lands is what `watch.py` takes.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const { planResumeUpload, MAX_UPLOAD_BYTES } =
  await import(join(REPO, 'deploy/profile_edits.mjs'));
const generated = await import(join(REPO, 'apps/web/app/_ui/profileEdits.generated.mjs'));
const { UPLOAD_KEY_PREFIX } = await import(join(REPO, 'deploy/device_keys.mjs'));

const pdf = (size) => Buffer.concat([Buffer.from('%PDF-1.7\n'),
                                     Buffer.alloc(Math.max(0, size - 9), 0x20)]);
const b64 = (buf) => buf.toString('base64');

test('v2 runs the same planner the old board does, not a copy that drifted', () => {
  assert.equal(generated.planResumeUpload.toString(), planResumeUpload.toString());
  assert.equal(generated.MAX_UPLOAD_BYTES, MAX_UPLOAD_BYTES);
});

test('a real PDF under the limit is accepted and keeps its bytes', () => {
  const doc = pdf(87 * 1024);
  const planned = planResumeUpload({ uploadId: 'doc-abc123', pdf: b64(doc) });
  assert.equal(planned.error, undefined);
  assert.equal(planned.uploadId, 'doc-abc123');
  assert.equal(planned.bytes, doc.length);
  assert.equal(Buffer.from(planned.encoded, 'base64').equals(doc), true);
});

test('exactly two megabytes is accepted and one byte more is refused as 413', () => {
  assert.equal(planResumeUpload({ uploadId: 'a', pdf: b64(pdf(MAX_UPLOAD_BYTES)) }).bytes,
               MAX_UPLOAD_BYTES);
  const over = planResumeUpload({ uploadId: 'a', pdf: b64(pdf(MAX_UPLOAD_BYTES + 1)) });
  assert.equal(over.status, 413);
  assert.equal(MAX_UPLOAD_BYTES, 2 * 1024 * 1024);
});

test('a file that is not a PDF is refused by content, whatever it is called', () => {
  const planned = planResumeUpload({ uploadId: 'a', pdf: b64(Buffer.from('PK\u0003\u0004 zip')) });
  assert.equal(planned.status, 415);
  assert.match(planned.error, /not a PDF/);
});

test('an id the queue would refuse, an empty file and junk are all 400', () => {
  for (const body of [
    { uploadId: 'doc 1', pdf: b64(pdf(100)) },
    { uploadId: '../../etc', pdf: b64(pdf(100)) },
    { uploadId: 'x'.repeat(121), pdf: b64(pdf(100)) },
    { pdf: b64(pdf(100)) },
    { uploadId: 'a' },
    { uploadId: 'a', pdf: '' },
    { uploadId: 'a', pdf: '%%%%' },
    { uploadId: 'a', pdf: 12 },
    null,
  ]) {
    const planned = planResumeUpload(body);
    assert.equal(planned.status, 400, `accepted ${JSON.stringify(body)?.slice(0, 60)}`);
  }
});

test('the key the route writes is the key queue.mjs upload-take reads', () => {
  const route = readFileSync(join(REPO, 'apps/web/app/api/resume-upload/route.ts'), 'utf8');
  const queue = readFileSync(join(REPO, 'deploy/queue.mjs'), 'utf8');
  assert.match(route, /kvPut\(e, `\$\{UPLOAD_KEY_PREFIX\}\$\{planned\.uploadId\}`/);
  assert.match(queue, /kvGet\(env, `\$\{UPLOAD_KEY_PREFIX\}\$\{prefix\}`\)/);
  assert.match(queue, /import \{ UPLOAD_KEY_PREFIX \} from '\.\/device_keys\.mjs'/);
  assert.equal(UPLOAD_KEY_PREFIX, 'jobby:upload:');
});

test('the route gates, plans, writes, then remembers, in that order', () => {
  const src = readFileSync(join(REPO, 'apps/web/app/api/resume-upload/route.ts'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '');
  const at = (needle) => {
    const i = src.indexOf(needle);
    assert.ok(i > 0, `the upload route no longer calls ${needle}`);
    return i;
  };
  const gate = at('await requireWritesEnabled(');
  const plan = at('planResumeUpload(body)');
  const put = at('await kvPut(');
  const keep = at('await remember(');
  assert.ok(gate < plan && plan < put && put < keep,
    'the upload route is out of order: gate, plan, KV write, remember');
});
