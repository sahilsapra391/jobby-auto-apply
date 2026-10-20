// Application documents by filename: the half of v1's `/doc/<name>` that v2
// did not carry, and the canonical copy of it now that `deploy/` is going away.
//
// This file is plain source, not generated. `deploy/api/board.js` held the only
// implementation of the lookup below, and generating v2's copy from a directory
// about to be deleted would make the deletion break the board.
//
// Why a filename still has to resolve at all. `/api/artifact` keys on the
// application and the kind, `u/{user}/app/{legacyId}/{kind}.{ext}`, and that is
// right for everything written since `document_contract` put the application id
// in the name. It is not right for the documents written before, which v1
// reached through `jobby:doc-index` in KV:
//
//   - a filename several applications recorded (three roles at one company, one
//     `Alex_Example_Acme_Resume.pdf`) is stored once, under whichever
//     application the index attributed it to, so the other two have nothing at
//     their own key. Measured on production 2026-10-20: 14 Submitted cells,
//     every one indexed, every one served by v1 and shown as "not attributable"
//     by v2 with no way to open it.
//   - a document the index never learned lives only in KV as `jobby:doc:<name>`.
//     0 of the 149 such keys today, and the fallback stays because v1 served
//     them and nothing about a count of zero keeps it zero.
//
// The index is a frozen snapshot. `publish.py` wrote it and no longer runs.
// That is fine for what it is for: every document it maps was made before the
// fixed path existed, and every document made since is at the fixed path.
//
// Dual-read order is v1's, and so is the reason for it. R2 first, because that
// is where the bytes are. KV for anything the index does not know, and for an
// indexed key R2 answers 404 on, which is the index being ahead of the bucket
// (emptiness, not a fault). Any other R2 failure with no KV copy behind it is a
// 502 naming the fault. A 404 is said only when nothing anywhere has it.

import { kvGet, configured as kvConfigured, conf as kvConf } from './kv.generated.mjs';
import { configured as r2Configured, fetchObject } from './r2.generated.mjs';

export const DOC_INDEX_KEY = 'jobby:doc-index';
export const docKey = (name) => `jobby:doc:${name}`;

// A plain filename. The space is deliberate: ten stored artifacts are named
// "Alex Example - Resume - Senior Project Manager.pdf", from before
// document_contract owned the naming, and v1's first guard refused them so
// their links were always dead. A space cannot escape a path segment or open a
// second KV key, and it never reaches the R2 key, which comes from the index.
const NAME = /^[A-Za-z0-9._ -]{1,200}$/;

// The key arrives through KV and ends up inside a signed request, so it is
// checked rather than trusted. The dot segments matter: an intermediary that
// normalises the path would sign one key and fetch another.
const KEY = /^u\/[A-Za-z0-9._-]+\/[\x20-\x7e]{1,900}$/;

const TYPE = /^[a-z]+\/[a-z0-9.+-]+(; charset=[a-z0-9-]+)?$/i;

export function plainName(name) {
  return typeof name === 'string' && NAME.test(name) && name !== '.' && name !== '..';
}

export function safeKey(key) {
  return typeof key === 'string' && KEY.test(key) && !key.split('/').includes('..');
}

/** The basename of a recorded value, which is sometimes a path. */
export function basename(value) {
  const text = String(value ?? '').trim();
  return text.slice(text.lastIndexOf('/') + 1);
}

/** Content type for a name, from the index when it says, else from the name. */
export function contentTypeFor(name, entry) {
  if (entry && typeof entry.contentType === 'string' && TYPE.test(entry.contentType)) {
    return entry.contentType;
  }
  const lower = String(name).toLowerCase();
  if (lower.endsWith('.json')) return 'application/json';
  if (lower.endsWith('.html')) return 'text/html; charset=utf-8';
  if (lower.endsWith('.txt')) return 'text/plain; charset=utf-8';
  // Everything KV holds is a PDF, because that is all the pre-R2 publisher
  // wrote, so this is v1's default rather than a guess.
  return 'application/pdf';
}

// A serverless instance handles several requests before it is recycled, so a
// short cache turns a burst of page loads into one KV read.
const CACHE_MS = 60_000;
let cachedIndex = null;
let cachedKvNames = null;

/** Reset the caches. For tests, which write a new index per case. */
export function resetCaches() {
  cachedIndex = null;
  cachedKvNames = null;
}

/**
 * `{schemaVersion, generatedAt, docs: {name: {key, contentType, ...}}}`, `null`
 * when KV has no index, and a throw when KV could not be read. Those are
 * different answers and the caller keeps them apart.
 */
export async function getDocIndex(env) {
  if (cachedIndex && Date.now() - cachedIndex.at < CACHE_MS) return cachedIndex.value;
  const raw = await kvGet(env, DOC_INDEX_KEY);
  let value = null;
  if (raw !== null) {
    try {
      value = JSON.parse(raw);
    } catch {
      value = null;
    }
    if (!value || value.schemaVersion !== 1 || !value.docs || typeof value.docs !== 'object') {
      value = null;
    }
  }
  cachedIndex = { at: Date.now(), value };
  return value;
}

/**
 * Every filename KV holds a document copy for. One paged listing rather than a
 * probe per row, for the same reason the bucket is listed once.
 */
export async function kvDocNames(env) {
  if (cachedKvNames && Date.now() - cachedKvNames.at < CACHE_MS) return cachedKvNames.names;
  const c = kvConf(env);
  if (!c) throw new Error('KV is not configured: need CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, CF_API_TOKEN');
  const keysUrl = c.base.replace(/\/values$/, '/keys');
  const prefix = docKey('');
  const names = new Set();
  let cursor = '';
  for (let page = 0; page < 50; page += 1) {
    const params = new URLSearchParams({ prefix, limit: '1000' });
    if (cursor) params.set('cursor', cursor);
    const r = await fetch(`${keysUrl}?${params}`, {
      headers: { authorization: `Bearer ${c.token}` },
    });
    if (!r.ok) throw new Error(`KV key listing failed: ${r.status}`);
    const body = await r.json();
    for (const item of body.result || []) {
      if (typeof item?.name === 'string') names.add(item.name.slice(prefix.length));
    }
    cursor = body.result_info?.cursor || '';
    if (!cursor) break;
  }
  cachedKvNames = { at: Date.now(), names };
  return names;
}

/**
 * Which of these filenames can be opened by name, or `null` when that could not
 * be determined. Never an empty set for "could not look": a row told "not
 * stored" goes looking for a missing file, and the file is sitting in R2.
 */
export async function servableNames(env, names) {
  if (!kvConfigured(env)) return null;
  let index;
  let kvNames;
  try {
    [index, kvNames] = await Promise.all([getDocIndex(env), kvDocNames(env)]);
  } catch {
    return null;
  }
  const docs = index ? index.docs : {};
  const out = new Set();
  for (const name of names) {
    if (!plainName(name)) continue;
    const entry = docs[name];
    if ((entry && safeKey(entry.key)) || kvNames.has(name)) out.add(name);
  }
  return out;
}

/**
 * Read one document by filename. Returns one of
 *
 *   {ok: true, status, body, contentType, headers}   bytes to serve
 *   {ok: false, status: 404}                         nothing anywhere has it
 *   {ok: false, status: 502, error}                  something could not be read
 *   {ok: false, status: 503, error}                  nothing is configured to look
 *
 * `headers` carries only what is safe to forward from R2: length, range and
 * accept-ranges. No signed URL reaches the caller.
 */
export async function readByName(env, name, range = null) {
  if (!plainName(name)) return { ok: false, status: 404 };
  const r2On = r2Configured(env);
  const kvOn = kvConfigured(env);
  if (!r2On && !kvOn) {
    return { ok: false, status: 503,
             error: 'document storage is not configured for this deployment' };
  }

  let entry = null;
  let indexError = null;
  if (kvOn) {
    try {
      const index = await getDocIndex(env);
      entry = index ? index.docs[name] ?? null : null;
    } catch (err) {
      indexError = `the document index could not be read: ${String(err?.message ?? err).slice(0, 160)}`;
    }
  }
  const contentType = contentTypeFor(name, entry);

  let r2Error = null;
  if (r2On && entry && safeKey(entry.key)) {
    try {
      const upstream = await fetchObject(env, entry.key, range);
      if (upstream.ok || upstream.status === 206) {
        const headers = {};
        for (const header of ['content-length', 'content-range', 'accept-ranges']) {
          const value = upstream.headers.get(header);
          if (value) headers[header] = value;
        }
        return { ok: true, status: upstream.status, body: upstream.body, contentType, headers };
      }
      // 404 is the index being ahead of the bucket: fall through to KV.
      if (upstream.status !== 404) r2Error = `R2 answered ${upstream.status}`;
    } catch (err) {
      r2Error = String(err?.message ?? err).slice(0, 200);
    }
  }

  // KV is the not-answered path. Six ways to arrive: R2 unconfigured, the index
  // unreadable, no entry, an entry whose key fails the checks, R2 404, or any
  // other R2 failure.
  let kvError = null;
  if (kvOn) {
    try {
      const b64 = await kvGet(env, docKey(name));
      if (b64) {
        const bytes = Buffer.from(b64, 'base64');
        return { ok: true, status: 200, body: bytes, contentType,
                 headers: { 'content-length': String(bytes.length) } };
      }
    } catch (err) {
      kvError = `KV could not be read: ${String(err?.message ?? err).slice(0, 160)}`;
    }
  }
  const fault = [indexError, r2Error, kvError].filter(Boolean).join('; ');
  if (fault) return { ok: false, status: 502, error: fault };
  if (!r2On && entry && safeKey(entry.key)) {
    // The index says the bucket has it and this deployment cannot ask the
    // bucket. That is "could not look", never "is not there".
    return { ok: false, status: 503,
             error: 'this document is in R2 and artifact storage is not configured for this deployment' };
  }
  return { ok: false, status: 404 };
}
