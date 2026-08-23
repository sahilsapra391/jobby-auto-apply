// The board page and the application documents, read from KV at request time
// rather than baked into the deployment.
//
// They used to be generated into api/_dashboard.js and api/_documents.js and
// shipped with the code, which had two consequences. Every board change was a
// full redeploy, and Vercel's free plan allows a hundred a day; thirty
// applications spent the lot. And the generated modules were untracked, so a
// build from GitHub had no board to serve at all, which is what made a proper
// merge-to-deploy pipeline impossible.
//
// Now a board update is one KV write and deployments carry only code.

import { kvGet, kvPut, configured } from './kv.mjs';

export const BOARD_KEY = 'jobby:board';
export const LOCAL_STATUS_KEY = 'jobby:worker:local:status';
export const WORKER_STATUS_KEY = 'jobby:worker:documents:status';
export const docKey = (name) => `jobby:doc:${name}`;

// A serverless instance handles several requests before it is recycled, so a
// short cache turns a burst of page loads into one KV read without ever
// serving anything meaningfully stale.
const CACHE_MS = 20_000;
let cached = null;

export async function getBoard(env) {
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.value;
  const raw = await kvGet(env, BOARD_KEY);
  if (raw === null) return null;
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  cached = { at: Date.now(), value };
  return value;
}

export async function putBoard(env, html, generatedAt, providers = null) {
  cached = null;
  return kvPut(env, BOARD_KEY, JSON.stringify({ html, generatedAt, providers }));
}

export async function getWorkerStatus(env) {
  const raw = await kvGet(env, WORKER_STATUS_KEY);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function getLocalStatus(env) {
  const raw = await kvGet(env, LOCAL_STATUS_KEY);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function putLocalStatus(env, status) {
  return kvPut(env, LOCAL_STATUS_KEY, JSON.stringify(status));
}

export async function putWorkerStatus(env, status) {
  return kvPut(env, WORKER_STATUS_KEY, JSON.stringify(status));
}

/** Base64 of one application document, or null if it was never published. */
export async function getDoc(env, name) {
  return kvGet(env, docKey(name));
}

export async function putDoc(env, name, base64) {
  return kvPut(env, docKey(name), base64);
}

export { configured };
