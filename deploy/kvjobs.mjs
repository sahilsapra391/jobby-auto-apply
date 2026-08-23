// Encrypted document jobs stored in Cloudflare Workers KV.
//
// Request and result bodies are sealed on the machine that creates them. KV
// receives ciphertext plus a small fixed status record. Every job has its own
// keys, so activity for one document job cannot overwrite another job.

import { randomBytes, randomUUID, timingSafeEqual, webcrypto } from 'node:crypto';
import { conf, configured } from './kv.mjs';

export { configured };

export const VERSION = 1;
export const QUEUE = 'documents';
export const DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60;
export const MAX_TTL_SECONDS = 30 * 24 * 60 * 60;
export const DEFAULT_LEASE_SECONDS = 15 * 60;
export const MAX_PLAINTEXT_BYTES = 16 * 1024 * 1024;
export const MAX_KV_VALUE_BYTES = 24 * 1024 * 1024;

const MIN_TTL_SECONDS = 60;
const PREFIX = 'jobby:documents:';
const INDEX_KEY = `${PREFIX}index`;
const MAX_INDEX_JOBS = 1000;
const STATES = new Set(['queued', 'claimed', 'completed', 'retrieved', 'failed']);
const FAILURE_CODES = new Set([
  'worker_failed',
  'invalid_request',
  'generation_failed',
  'result_invalid',
]);

const subtle = webcrypto.subtle;
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });

export class DocumentTransportError extends Error {
  constructor(code) {
    super(code);
    this.name = 'DocumentTransportError';
    this.code = code;
  }
}

const fail = (code) => { throw new DocumentTransportError(code); };

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function assertJobId(jobId) {
  if (typeof jobId !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(jobId)) {
    fail('job_id_invalid');
  }
  return jobId.toLowerCase();
}

export function documentKeys(jobId) {
  const id = assertJobId(jobId);
  return {
    request: `${PREFIX}request:${id}`,
    claim: `${PREFIX}claim:${id}`,
    status: `${PREFIX}status:${id}`,
    result: `${PREFIX}result:${id}`,
  };
}

function normalizeTtl(value, fallback = DEFAULT_TTL_SECONDS) {
  const ttl = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(ttl) || ttl < MIN_TTL_SECONDS || ttl > MAX_TTL_SECONDS) {
    fail('ttl_invalid');
  }
  return ttl;
}

function isoAfter(seconds, now = Date.now()) {
  return new Date(now + seconds * 1000).toISOString();
}

function remainingTtl(expiresAt) {
  const millis = Date.parse(expiresAt) - Date.now();
  if (!Number.isFinite(millis) || millis < MIN_TTL_SECONDS * 1000) fail('job_expired');
  return Math.max(MIN_TTL_SECONDS, Math.floor(millis / 1000));
}

function decodeBase64(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_+/=-]+$/.test(value)) fail('worker_key_invalid');
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  try {
    return Buffer.from(normalized, 'base64');
  } catch {
    fail('worker_key_invalid');
  }
}

export function decodeWorkerKey(value) {
  if (typeof value !== 'string') fail('worker_key_missing');
  const text = value.trim().replace(/^base64(?:url)?:/i, '');
  let raw;
  if (/^[0-9a-f]{64}$/i.test(text)) {
    raw = Buffer.from(text, 'hex');
  } else {
    raw = decodeBase64(text);
  }
  if (raw.length !== 32) fail('worker_key_invalid');
  return new Uint8Array(raw);
}

async function cryptoKey(workerKey) {
  return subtle.importKey('raw', decodeWorkerKey(workerKey), { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

function base64url(value) {
  return Buffer.from(value).toString('base64url');
}

function fromBase64url(value, code = 'payload_invalid') {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) fail(code);
  try {
    return new Uint8Array(Buffer.from(value, 'base64url'));
  } catch {
    fail(code);
  }
}

function aad(jobId, channel) {
  return encoder.encode(`jobby.documents.v${VERSION}|${assertJobId(jobId)}|${channel}`);
}

function jsonBytes(value, code) {
  let raw;
  try {
    raw = encoder.encode(JSON.stringify(value));
  } catch {
    fail(code);
  }
  if (raw.byteLength > MAX_PLAINTEXT_BYTES) fail('payload_too_large');
  return raw;
}

export async function sealJson(workerKey, jobId, channel, value) {
  const raw = jsonBytes(value, 'payload_invalid');
  const iv = randomBytes(12);
  const encrypted = await subtle.encrypt(
    { name: 'AES-GCM', iv, additionalData: aad(jobId, channel), tagLength: 128 },
    await cryptoKey(workerKey),
    raw,
  );
  const envelope = JSON.stringify({
    version: VERSION,
    algorithm: 'A256GCM',
    iv: base64url(iv),
    ciphertext: base64url(encrypted),
  });
  if (Buffer.byteLength(envelope) > MAX_KV_VALUE_BYTES) fail('payload_too_large');
  return envelope;
}

export async function openJson(workerKey, jobId, channel, envelopeText) {
  if (typeof envelopeText !== 'string' || Buffer.byteLength(envelopeText) > MAX_KV_VALUE_BYTES) {
    fail('payload_invalid');
  }
  let envelope;
  try {
    envelope = JSON.parse(envelopeText);
  } catch {
    fail('payload_invalid');
  }
  if (!isObject(envelope) || envelope.version !== VERSION || envelope.algorithm !== 'A256GCM') {
    fail('payload_invalid');
  }
  const iv = fromBase64url(envelope.iv);
  const ciphertext = fromBase64url(envelope.ciphertext);
  if (iv.byteLength !== 12 || ciphertext.byteLength < 17) fail('payload_invalid');
  let raw;
  try {
    raw = await subtle.decrypt(
      { name: 'AES-GCM', iv, additionalData: aad(jobId, channel), tagLength: 128 },
      await cryptoKey(workerKey),
      ciphertext,
    );
  } catch {
    fail('payload_auth_failed');
  }
  try {
    return JSON.parse(decoder.decode(raw));
  } catch {
    fail('payload_invalid');
  }
}

function cloudflare(env) {
  const value = conf(env);
  if (!value) fail('cloudflare_not_configured');
  return value;
}

async function fetchSafe(url, options) {
  try {
    return await fetch(url, options);
  } catch {
    fail('transport_unavailable');
  }
}

async function getValue(env, key) {
  const c = cloudflare(env);
  const response = await fetchSafe(`${c.base}/${encodeURIComponent(key)}`, {
    headers: { authorization: `Bearer ${c.token}` },
  });
  if (response.status === 404) return null;
  if (!response.ok) fail('transport_read_failed');
  return response.text();
}

async function putValue(env, key, value, ttl = null, metadata = {}) {
  if (typeof value !== 'string' || Buffer.byteLength(value) > MAX_KV_VALUE_BYTES) fail('payload_too_large');
  const c = cloudflare(env);
  const body = new FormData();
  body.set('value', value);
  body.set('metadata', JSON.stringify(metadata));
  if (ttl !== null) body.set('expiration_ttl', String(normalizeTtl(ttl)));
  const response = await fetchSafe(`${c.base}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { authorization: `Bearer ${c.token}` },
    body,
  });
  if (!response.ok) fail('transport_write_failed');
}

async function deleteValue(env, key) {
  const c = cloudflare(env);
  const response = await fetchSafe(`${c.base}/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    headers: { authorization: `Bearer ${c.token}` },
  });
  if (!response.ok && response.status !== 404) fail('transport_delete_failed');
}

async function readIndex(env) {
  const raw = await getValue(env, INDEX_KEY);
  if (raw === null) return [];
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    fail('status_invalid');
  }
  if (!isObject(parsed) || parsed.version !== VERSION || !Array.isArray(parsed.jobs)) {
    fail('status_invalid');
  }
  const jobs = [];
  for (const value of parsed.jobs) {
    try {
      const jobId = assertJobId(value);
      if (!jobs.includes(jobId)) jobs.push(jobId);
    } catch {
      fail('status_invalid');
    }
  }
  if (jobs.length > MAX_INDEX_JOBS) fail('status_invalid');
  return jobs;
}

async function writeIndex(env, jobs) {
  const unique = [...new Set(jobs.map(assertJobId))];
  if (unique.length > MAX_INDEX_JOBS) fail('status_invalid');
  if (!unique.length) {
    await deleteValue(env, INDEX_KEY);
    return;
  }
  await putValue(env, INDEX_KEY, JSON.stringify({ version: VERSION, jobs: unique }), null, {
    version: VERSION,
    queue: QUEUE,
    type: 'index',
  });
}

async function addToIndex(env, jobId) {
  const id = assertJobId(jobId);
  const jobs = await readIndex(env);
  if (!jobs.includes(id)) {
    jobs.push(id);
    await writeIndex(env, jobs);
  }
}

async function removeFromIndex(env, jobId) {
  const id = assertJobId(jobId);
  const jobs = await readIndex(env);
  if (jobs.includes(id)) await writeIndex(env, jobs.filter((value) => value !== id));
}

function safeTimestamp(value) {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return null;
  return new Date(value).toISOString();
}

export function sanitizeStatus(value) {
  if (!isObject(value)) return null;
  let jobId;
  try {
    jobId = assertJobId(value.jobId);
  } catch {
    return null;
  }
  if (value.version !== VERSION || value.queue !== QUEUE || !STATES.has(value.state)) return null;
  const createdAt = safeTimestamp(value.createdAt);
  const updatedAt = safeTimestamp(value.updatedAt);
  const expiresAt = safeTimestamp(value.expiresAt);
  if (!createdAt || !updatedAt || !expiresAt) return null;
  const clean = {
    version: VERSION,
    queue: QUEUE,
    jobId,
    state: value.state,
    createdAt,
    updatedAt,
    expiresAt,
  };
  if (value.state === 'failed' && FAILURE_CODES.has(value.errorCode)) clean.errorCode = value.errorCode;
  return clean;
}

async function readStatus(env, jobId) {
  const raw = await getValue(env, documentKeys(jobId).status);
  if (raw === null) return null;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    fail('status_invalid');
  }
  const status = sanitizeStatus(parsed);
  if (!status) fail('status_invalid');
  return status;
}

async function writeStatus(env, jobId, state, expiresAt, options = {}) {
  if (!STATES.has(state)) fail('status_invalid');
  const previous = options.previous || await readStatus(env, jobId);
  const now = new Date().toISOString();
  const status = {
    version: VERSION,
    queue: QUEUE,
    jobId: assertJobId(jobId),
    state,
    createdAt: previous?.createdAt || now,
    updatedAt: now,
    expiresAt,
  };
  if (state === 'failed') {
    if (!FAILURE_CODES.has(options.errorCode)) fail('failure_code_invalid');
    status.errorCode = options.errorCode;
  }
  const clean = sanitizeStatus(status);
  await putValue(env, documentKeys(jobId).status, JSON.stringify(clean), remainingTtl(expiresAt), {
    version: VERSION,
    queue: QUEUE,
    state,
  });
  return clean;
}

function workerKey(env) {
  if (!env?.JOBBY_WORKER_KEY) fail('worker_key_missing');
  return env.JOBBY_WORKER_KEY;
}

function randomClaimToken() {
  return base64url(randomBytes(32));
}

function sameToken(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false;
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

function validateBundle(bundle) {
  if (!isObject(bundle)) fail('bundle_invalid');
  jsonBytes(bundle, 'bundle_invalid');
  return bundle;
}

export async function enqueueDocumentJob(env, bundle, options = {}) {
  validateBundle(bundle);
  const jobId = assertJobId(options.jobId || randomUUID());
  const ttl = normalizeTtl(options.ttlSeconds);
  const keys = documentKeys(jobId);
  const existing = await getValue(env, keys.request);
  if (existing !== null) {
    let status = await readStatus(env, jobId);
    if (!status) {
      const request = await openRequest(env, jobId, existing);
      const expiresAt = isoAfter(ttl);
      // The first enqueue may have stopped after storing the request. Refresh
      // its TTL before recreating the missing status so both records remain
      // available for the same recovery window.
      await putValue(env, keys.request, existing, ttl, {
        version: VERSION,
        queue: QUEUE,
        type: 'request',
      });
      status = await writeStatus(env, jobId, 'queued', expiresAt, {
        previous: { createdAt: safeTimestamp(request.createdAt) || new Date().toISOString() },
      });
    }
    if (['queued', 'claimed'].includes(status.state)) await addToIndex(env, jobId);
    return { jobId, state: status.state, deduped: true };
  }
  const createdAt = new Date().toISOString();
  const expiresAt = isoAfter(ttl);
  const request = {
    version: VERSION,
    queue: QUEUE,
    jobId,
    createdAt,
    bundle,
  };
  const sealed = await sealJson(workerKey(env), jobId, 'request', request);
  await putValue(env, keys.request, sealed, ttl, { version: VERSION, queue: QUEUE, type: 'request' });
  const status = await writeStatus(env, jobId, 'queued', expiresAt);
  await addToIndex(env, jobId);
  return { jobId, state: status.state, deduped: false };
}

export async function listQueuedDocumentJobs(env) {
  const candidates = await scanDocumentJobCandidates(env);
  return candidates
    .filter(({ status }) => status.state === 'queued')
    .map(({ jobId }) => jobId)
    .sort();
}

export async function listDocumentJobCandidates(env) {
  return (await scanDocumentJobCandidates(env)).map(({ jobId }) => jobId).sort();
}

async function scanDocumentJobCandidates(env) {
  const entries = await readIndex(env);
  const candidates = [];
  for (const jobId of entries) {
    const status = await readStatus(env, jobId);
    if (status && ['queued', 'claimed'].includes(status.state)) {
      candidates.push({ jobId, status });
    }
  }
  if (candidates.length !== entries.length) {
    await writeIndex(env, candidates.map(({ jobId }) => jobId));
  }
  return candidates;
}

async function readClaim(env, jobId) {
  const raw = await getValue(env, documentKeys(jobId).claim);
  if (raw === null) return null;
  const value = await openJson(workerKey(env), jobId, 'claim', raw);
  if (!isObject(value) || value.version !== VERSION || value.jobId !== jobId ||
      typeof value.claimToken !== 'string') fail('claim_invalid');
  return value;
}

async function openRequest(env, jobId, raw) {
  const request = await openJson(workerKey(env), jobId, 'request', raw);
  if (!isObject(request) || request.version !== VERSION || request.queue !== QUEUE ||
      request.jobId !== jobId || !isObject(request.bundle)) fail('payload_invalid');
  return request;
}

export async function claimDocumentJob(env, options = {}) {
  const candidates = options.jobId
    ? [assertJobId(options.jobId)]
    : await listDocumentJobCandidates(env);
  for (const jobId of candidates) {
    const keys = documentKeys(jobId);
    const requestRaw = await getValue(env, keys.request);
    if (requestRaw === null || await getValue(env, keys.result) !== null) {
      await removeFromIndex(env, jobId);
      continue;
    }
    const previousClaim = await readClaim(env, jobId);
    if (previousClaim) {
      if (options.claimToken && sameToken(previousClaim.claimToken, options.claimToken)) {
        const request = await openRequest(env, jobId, requestRaw);
        return {
          jobId,
          state: 'claimed',
          claimed: true,
          deduped: true,
          claimToken: previousClaim.claimToken,
          request,
        };
      }
      continue;
    }
    const status = await readStatus(env, jobId);
    // A claimed status with no claim value means its lease expired after a
    // worker stopped. Document generation is side-effect free outside this
    // queue, so a fresh worker can safely rebuild it from the sealed request.
    if (!status || !['queued', 'claimed'].includes(status.state)) continue;
    const claimToken = options.claimToken || randomClaimToken();
    const remaining = remainingTtl(status.expiresAt);
    const lease = Math.min(normalizeTtl(options.leaseSeconds, DEFAULT_LEASE_SECONDS), remaining);
    const claim = {
      version: VERSION,
      jobId,
      claimToken,
      claimedAt: new Date().toISOString(),
    };
    const sealedClaim = await sealJson(workerKey(env), jobId, 'claim', claim);
    await putValue(env, keys.claim, sealedClaim, lease, { version: VERSION, queue: QUEUE, type: 'claim' });
    let request;
    try {
      request = await openRequest(env, jobId, requestRaw);
    } catch (error) {
      await writeStatus(env, jobId, 'failed', status.expiresAt, {
        previous: status,
        errorCode: 'invalid_request',
      });
      throw error;
    }
    await writeStatus(env, jobId, 'claimed', status.expiresAt, { previous: status });
    return { jobId, state: 'claimed', claimed: true, deduped: false, claimToken, request };
  }
  return null;
}

async function verifyClaim(env, jobId, claimToken) {
  const claim = await readClaim(env, jobId);
  if (!claim) fail('claim_expired');
  if (!sameToken(claim.claimToken, claimToken)) fail('claim_mismatch');
  return claim;
}

export async function heartbeatDocumentJob(env, jobId, claimToken, options = {}) {
  const id = assertJobId(jobId);
  const claim = await verifyClaim(env, id, claimToken);
  const status = await readStatus(env, id);
  if (!status || status.state !== 'claimed') fail('job_not_claimed');
  const remaining = remainingTtl(status.expiresAt);
  const lease = Math.min(normalizeTtl(options.leaseSeconds, DEFAULT_LEASE_SECONDS), remaining);
  const sealed = await sealJson(workerKey(env), id, 'claim', claim);
  await putValue(env, documentKeys(id).claim, sealed, lease, {
    version: VERSION,
    queue: QUEUE,
    type: 'claim',
  });
  return { jobId: id, state: 'claimed' };
}

export async function completeDocumentJob(env, jobId, claimToken, result) {
  const id = assertJobId(jobId);
  if (!isObject(result)) fail('result_invalid');
  jsonBytes(result, 'result_invalid');
  const keys = documentKeys(id);
  const existingResult = await getValue(env, keys.result);
  if (existingResult !== null) {
    let status = await readStatus(env, id);
    if (!status || ['queued', 'claimed'].includes(status.state)) {
      const payload = await openResult(env, id, existingResult);
      let expiresAt = status?.expiresAt || safeTimestamp(payload.expiresAt);
      if (!expiresAt || Date.parse(expiresAt) - Date.now() < MIN_TTL_SECONDS * 1000) {
        expiresAt = isoAfter(DEFAULT_TTL_SECONDS);
        await putValue(env, keys.result, existingResult, remainingTtl(expiresAt), {
          version: VERSION,
          queue: QUEUE,
          type: 'result',
        });
      }
      status = await writeStatus(env, id, 'completed', expiresAt, {
        previous: status || {
          createdAt: safeTimestamp(payload.completedAt) || new Date().toISOString(),
        },
      });
    }
    await removeFromIndex(env, id);
    return { jobId: id, state: 'completed', deduped: true };
  }
  await verifyClaim(env, id, claimToken);
  const status = await readStatus(env, id);
  if (!status || status.state !== 'claimed') fail('job_not_claimed');
  const payload = {
    version: VERSION,
    queue: QUEUE,
    jobId: id,
    completedAt: new Date().toISOString(),
    expiresAt: status.expiresAt,
    result,
  };
  const sealed = await sealJson(workerKey(env), id, 'result', payload);
  await putValue(env, keys.result, sealed, remainingTtl(status.expiresAt), {
    version: VERSION,
    queue: QUEUE,
    type: 'result',
  });
  await writeStatus(env, id, 'completed', status.expiresAt, { previous: status });
  await removeFromIndex(env, id);
  return { jobId: id, state: 'completed', deduped: false };
}

export async function failDocumentJob(env, jobId, claimToken, errorCode = 'worker_failed') {
  const id = assertJobId(jobId);
  if (!FAILURE_CODES.has(errorCode)) fail('failure_code_invalid');
  await verifyClaim(env, id, claimToken);
  const status = await readStatus(env, id);
  if (!status || status.state !== 'claimed') fail('job_not_claimed');
  const failed = await writeStatus(env, id, 'failed', status.expiresAt, {
    previous: status,
    errorCode,
  });
  await removeFromIndex(env, id);
  return failed;
}

export async function retrieveDocumentResult(env, jobId) {
  const id = assertJobId(jobId);
  const keys = documentKeys(id);
  const raw = await getValue(env, keys.result);
  if (raw === null) return null;
  const payload = await openResult(env, id, raw);
  const status = await readStatus(env, id);
  if (status && status.state === 'completed') {
    await writeStatus(env, id, 'retrieved', status.expiresAt, { previous: status });
  }
  return payload;
}

async function openResult(env, jobId, raw) {
  const payload = await openJson(workerKey(env), jobId, 'result', raw);
  if (!isObject(payload) || payload.version !== VERSION || payload.queue !== QUEUE ||
      payload.jobId !== jobId || !isObject(payload.result)) fail('result_invalid');
  return payload;
}

export async function getDocumentStatus(env, jobId) {
  const id = assertJobId(jobId);
  return readStatus(env, id);
}

export async function deleteDocumentJob(env, jobId) {
  const id = assertJobId(jobId);
  const keys = documentKeys(id);
  for (const key of [keys.request, keys.claim, keys.status, keys.result]) {
    await deleteValue(env, key);
  }
  await removeFromIndex(env, id);
  return { jobId: id, state: 'deleted' };
}

export function publicErrorCode(error) {
  if (error instanceof DocumentTransportError && typeof error.code === 'string') return error.code;
  return 'transport_error';
}
