import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DocumentTransportError,
  sealJson,
  openJson,
  enqueueDocumentJob,
  listQueuedDocumentJobs,
  listDocumentJobCandidates,
  claimDocumentJob,
  completeDocumentJob,
  failDocumentJob,
  retrieveDocumentResult,
  getDocumentStatus,
  deleteDocumentJob,
  documentKeys,
} from '../deploy/kvjobs.mjs';

const JOB_ID = '01890f47-3c7e-7d20-8c4a-2d8f9a107dd1';
const SECOND_JOB_ID = '01890f47-3c7e-7d20-8c4a-2d8f9a107dd2';
const THIRD_JOB_ID = '01890f47-3c7e-7d20-8c4a-2d8f9a107dd3';
const INDEX_KEY = 'jobby:documents:index';
const KEY = Buffer.alloc(32, 7).toString('base64');
const OTHER_KEY = Buffer.alloc(32, 9).toString('base64');
const ENV = {
  CF_ACCOUNT_ID: 'account',
  CF_KV_NAMESPACE_ID: 'namespace',
  CF_API_TOKEN: 'token',
  JOBBY_WORKER_KEY: KEY,
};

function fakeCloudflare() {
  const values = new Map();
  const calls = [];
  const failures = [];
  const failNext = (method, key, status = 503) => {
    failures.push({ method, key, status });
  };
  const fetch = async (input, options = {}) => {
    const url = new URL(input);
    const method = options.method || 'GET';
    calls.push({ url: url.toString(), method });
    const marker = '/storage/kv/namespaces/namespace/';
    const tail = url.pathname.slice(url.pathname.indexOf(marker) + marker.length);

    if (tail === 'keys') {
      const prefix = url.searchParams.get('prefix') || '';
      const result = [...values]
        .filter(([name]) => name.startsWith(prefix))
        .map(([name, item]) => ({ name, metadata: item.metadata }));
      return Response.json({ success: true, result, result_info: { cursor: '' } });
    }

    const rawKey = tail.replace(/^values\//, '');
    const key = decodeURIComponent(rawKey);
    const failureIndex = failures.findIndex(
      (failure) => failure.method === method && failure.key === key,
    );
    if (failureIndex !== -1) {
      const [{ status }] = failures.splice(failureIndex, 1);
      return new Response('', { status });
    }
    if (method === 'GET') {
      const item = values.get(key);
      return item ? new Response(item.value, { status: 200 }) : new Response('', { status: 404 });
    }
    if (method === 'PUT') {
      values.set(key, {
        value: String(options.body.get('value')),
        metadata: JSON.parse(String(options.body.get('metadata') || '{}')),
        ttl: Number(options.body.get('expiration_ttl')),
      });
      return new Response('', { status: 200 });
    }
    if (method === 'DELETE') {
      values.delete(key);
      return new Response('', { status: 200 });
    }
    return new Response('', { status: 405 });
  };
  return { values, calls, failNext, fetch };
}

async function withCloudflare(callback) {
  const original = globalThis.fetch;
  const cloudflare = fakeCloudflare();
  globalThis.fetch = cloudflare.fetch;
  try {
    return await callback(cloudflare);
  } finally {
    globalThis.fetch = original;
  }
}

test('AES-GCM binds a payload to its job and channel', async () => {
  const sealed = await sealJson(KEY, JOB_ID, 'request', { private: 'candidate details' });
  assert.deepEqual(
    await openJson(KEY, JOB_ID, 'request', sealed),
    { private: 'candidate details' },
  );
  await assert.rejects(
    openJson(OTHER_KEY, JOB_ID, 'request', sealed),
    (error) => error instanceof DocumentTransportError && error.code === 'payload_auth_failed',
  );
  await assert.rejects(
    openJson(KEY, JOB_ID, 'result', sealed),
    (error) => error instanceof DocumentTransportError && error.code === 'payload_auth_failed',
  );
});

test('an idle worker uses one value read and never calls the KV list API', async () => {
  await withCloudflare(async ({ calls }) => {
    assert.equal(await claimDocumentJob(ENV), null);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, 'GET');
    assert.doesNotMatch(calls[0].url, /\/keys(?:\?|$)/);
  });
});

test('a document job completes through the encrypted lifecycle', async () => {
  await withCloudflare(async ({ values }) => {
    const bundle = { company: 'Private Employer', profile: { email: 'person@example.com' } };
    const queued = await enqueueDocumentJob(ENV, bundle, { jobId: JOB_ID });
    assert.deepEqual(queued, { jobId: JOB_ID, state: 'queued', deduped: false });
    assert.deepEqual(await listQueuedDocumentJobs(ENV), [JOB_ID]);

    const storedText = [...values.values()].map((item) => item.value).join('\n');
    const storedMetadata = JSON.stringify([...values.values()].map((item) => item.metadata));
    assert.doesNotMatch(storedText, /Private Employer|person@example\.com/);
    assert.doesNotMatch(storedMetadata, /Private Employer|person@example\.com/);

    const claim = await claimDocumentJob(ENV);
    assert.equal(claim.jobId, JOB_ID);
    assert.deepEqual(claim.request.bundle, bundle);
    assert.equal(typeof claim.claimToken, 'string');

    const result = { files: [{ name: 'resume.pdf', data: 'JVBERi0=' }] };
    assert.equal(
      (await completeDocumentJob(ENV, JOB_ID, claim.claimToken, result)).state,
      'completed',
    );
    const retrieved = await retrieveDocumentResult(ENV, JOB_ID);
    assert.deepEqual(retrieved.result, result);
    assert.equal((await getDocumentStatus(ENV, JOB_ID)).state, 'retrieved');
  });
});

test('enqueue is idempotent for an existing opaque job id', async () => {
  await withCloudflare(async () => {
    const first = await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    const second = await enqueueDocumentJob(ENV, { value: 2 }, { jobId: JOB_ID });
    assert.equal(first.deduped, false);
    assert.equal(second.deduped, true);
    const claim = await claimDocumentJob(ENV, { jobId: JOB_ID });
    assert.deepEqual(claim.request.bundle, { value: 1 });
  });
});

test('enqueue retry repairs a request whose status write failed', async () => {
  await withCloudflare(async ({ values, failNext }) => {
    failNext('PUT', documentKeys(JOB_ID).status);
    await assert.rejects(
      enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID }),
      (error) => error instanceof DocumentTransportError && error.code === 'transport_write_failed',
    );
    assert.equal(values.has(documentKeys(JOB_ID).request), true);
    assert.equal(values.has(documentKeys(JOB_ID).status), false);
    assert.equal(values.has(INDEX_KEY), false);

    const retried = await enqueueDocumentJob(ENV, { value: 2 }, { jobId: JOB_ID });
    assert.deepEqual(retried, { jobId: JOB_ID, state: 'queued', deduped: true });
    assert.equal((await getDocumentStatus(ENV, JOB_ID)).state, 'queued');
    assert.deepEqual(await listQueuedDocumentJobs(ENV), [JOB_ID]);

    const claim = await claimDocumentJob(ENV, { jobId: JOB_ID });
    assert.deepEqual(claim.request.bundle, { value: 1 });
  });
});

test('complete retry repairs a claimed status after the result was stored', async () => {
  await withCloudflare(async ({ failNext, values }) => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    const claim = await claimDocumentJob(ENV);
    failNext('PUT', documentKeys(JOB_ID).status);
    await assert.rejects(
      completeDocumentJob(ENV, JOB_ID, claim.claimToken, { value: 2 }),
      (error) => error instanceof DocumentTransportError && error.code === 'transport_write_failed',
    );
    assert.equal((await getDocumentStatus(ENV, JOB_ID)).state, 'claimed');
    assert.equal(values.has(documentKeys(JOB_ID).result), true);

    const retried = await completeDocumentJob(ENV, JOB_ID, claim.claimToken, { value: 2 });
    assert.deepEqual(retried, { jobId: JOB_ID, state: 'completed', deduped: true });
    assert.equal((await getDocumentStatus(ENV, JOB_ID)).state, 'completed');
    assert.equal(values.has(INDEX_KEY), false);
  });
});

test('complete retry recreates a missing status for an existing result', async () => {
  await withCloudflare(async ({ values }) => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    const claim = await claimDocumentJob(ENV);
    await completeDocumentJob(ENV, JOB_ID, claim.claimToken, { value: 2 });
    values.delete(documentKeys(JOB_ID).status);

    const retried = await completeDocumentJob(ENV, JOB_ID, claim.claimToken, { value: 2 });
    assert.deepEqual(retried, { jobId: JOB_ID, state: 'completed', deduped: true });
    assert.equal((await getDocumentStatus(ENV, JOB_ID)).state, 'completed');
    assert.deepEqual((await retrieveDocumentResult(ENV, JOB_ID)).result, { value: 2 });
  });
});

test('candidate scans prune missing and terminal jobs from the persistent index', async () => {
  await withCloudflare(async ({ values }) => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    await enqueueDocumentJob(ENV, { value: 2 }, { jobId: SECOND_JOB_ID });
    const claim = await claimDocumentJob(ENV, { jobId: SECOND_JOB_ID });
    await completeDocumentJob(ENV, SECOND_JOB_ID, claim.claimToken, { value: 3 });
    values.set(INDEX_KEY, {
      value: JSON.stringify({
        version: 1,
        jobs: [JOB_ID, SECOND_JOB_ID, THIRD_JOB_ID],
      }),
      metadata: {},
      ttl: 0,
    });

    assert.deepEqual(await listDocumentJobCandidates(ENV), [JOB_ID]);
    assert.deepEqual(JSON.parse(values.get(INDEX_KEY).value).jobs, [JOB_ID]);
  });
});

test('status reads do not heal the index, while enqueue retries and targeted claims still work', async () => {
  await withCloudflare(async ({ values }) => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    values.delete(INDEX_KEY);

    assert.equal((await getDocumentStatus(ENV, JOB_ID)).state, 'queued');
    assert.equal(values.has(INDEX_KEY), false);

    const claim = await claimDocumentJob(ENV, { jobId: JOB_ID });
    assert.equal(claim.jobId, JOB_ID);
    assert.equal(claim.state, 'claimed');
  });
});

test('a worker failure exposes only an allowlisted code', async () => {
  await withCloudflare(async () => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    const claim = await claimDocumentJob(ENV);
    await failDocumentJob(ENV, JOB_ID, claim.claimToken, 'generation_failed');
    assert.deepEqual(
      Object.keys(await getDocumentStatus(ENV, JOB_ID)).sort(),
      ['createdAt', 'errorCode', 'expiresAt', 'jobId', 'queue', 'state', 'updatedAt', 'version'].sort(),
    );
    assert.equal((await getDocumentStatus(ENV, JOB_ID)).errorCode, 'generation_failed');
  });
});

test('deleting a job removes request, claim, status, and result records', async () => {
  await withCloudflare(async ({ values }) => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    const claim = await claimDocumentJob(ENV);
    await completeDocumentJob(ENV, JOB_ID, claim.claimToken, { value: 2 });
    await deleteDocumentJob(ENV, JOB_ID);
    assert.equal(values.size, 0);
    assert.equal(await getDocumentStatus(ENV, JOB_ID), null);
  });
});

test('an expired claim is reclaimed after a worker crash', async () => {
  await withCloudflare(async ({ values }) => {
    await enqueueDocumentJob(ENV, { value: 1 }, { jobId: JOB_ID });
    const first = await claimDocumentJob(ENV);
    values.delete(documentKeys(JOB_ID).claim);

    const second = await claimDocumentJob(ENV);
    assert.equal(second.jobId, JOB_ID);
    assert.notEqual(second.claimToken, first.claimToken);
    assert.deepEqual(second.request.bundle, { value: 1 });
  });
});
