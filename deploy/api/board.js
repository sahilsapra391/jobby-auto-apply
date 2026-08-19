// Serves the generated dashboard behind a password.
//
// The board names companies, roles, referral contacts and drafted messages, so
// it is not something to leave on a public URL. Vercel's own Deployment
// Protection does not cover production domains on the Hobby plan, which is why
// the gate lives here instead of in project settings.
//
// Fails closed: with no DASHBOARD_PASSWORD set, it serves 503 rather than the
// board.

import { HTML, GENERATED_AT } from './_dashboard.js';

const REALM = 'Application board';

// Compare digests rather than the raw strings: equal-length hashes mean the
// loop cannot leak the password's length through timing.
async function sameSecret(a, b) {
  const digest = async (s) =>
    new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  const [x, y] = await Promise.all([digest(a), digest(b)]);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function suppliedPassword(request) {
  const header = request.headers.get('authorization') || '';
  const [scheme, encoded] = header.split(' ');
  if (!encoded || scheme.toLowerCase() !== 'basic') return null;
  let decoded;
  try {
    decoded = atob(encoded);
  } catch {
    return null;
  }
  const colon = decoded.indexOf(':');
  return colon === -1 ? null : decoded.slice(colon + 1);
}

// default-src 'none' means a posting that smuggled markup through the store
// still cannot phone anything home. The board itself needs only inline
// style/script and outbound links.
const CSP = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline'",
  "img-src data:",
  // The icons and manifest are static files under public/, which Vercel serves
  // before the catch-all rewrite. Without this, default-src 'none' blocks the
  // manifest fetch and the board cannot be installed to a home screen.
  "manifest-src 'self'",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

const BASE_HEADERS = {
  'cache-control': 'no-store, max-age=0',
  'x-robots-tag': 'noindex, nofollow, noarchive',
  'referrer-policy': 'no-referrer',
  'x-content-type-options': 'nosniff',
};

const text = (body, status, extra = {}) =>
  new Response(body, {
    status,
    headers: { 'content-type': 'text/plain; charset=utf-8', ...BASE_HEADERS, ...extra },
  });

import { put, list, del } from '@vercel/blob';
import { DOCUMENTS } from './_documents.js';

const QUEUE_PREFIX = 'scan/';
const MARK_PREFIX = 'marked/';
const APPLY_PREFIX = 'apply/';

function queueConfigured() {
  return Boolean(process.env.BLOB_READ_WRITE_TOKEN);
}

// One blob per request. The timestamp lives in the pathname, so listing the
// queue never needs to fetch any content.
async function enqueue() {
  const at = new Date().toISOString();
  await put(`${QUEUE_PREFIX}${at.replace(/[:.]/g, '-')}.json`,
    JSON.stringify({ requestedAt: at }),
    { access: 'private', contentType: 'application/json' });
  return at;
}

async function pendingUnder(prefix) {
  const { blobs } = await list({ prefix });
  return (blobs || [])
    .map((b) => ({ pathname: b.pathname, url: b.url, uploadedAt: b.uploadedAt }))
    .sort((a, b) => String(a.pathname).localeCompare(String(b.pathname)));
}

async function pending() {
  const { blobs } = await list({ prefix: QUEUE_PREFIX });
  return (blobs || [])
    .map((b) => ({ pathname: b.pathname, url: b.url, uploadedAt: b.uploadedAt }))
    .sort((a, b) => String(a.pathname).localeCompare(String(b.pathname)));
}

export default {
  async fetch(request) {
    const { pathname } = new URL(request.url);

    // Unauthenticated, and says nothing about the search: enough to confirm a
    // deploy landed without typing the password.
    if (pathname === '/healthz') {
      return new Response(JSON.stringify({ ok: true, generatedAt: GENERATED_AT }), {
        status: 200,
        headers: { 'content-type': 'application/json', ...BASE_HEADERS },
      });
    }

    const expected = process.env.DASHBOARD_PASSWORD;
    if (!expected) {
      return text(
        'DASHBOARD_PASSWORD is not set on this project, so the board will not be served.\n' +
          'Set it with: vercel env add DASHBOARD_PASSWORD production\n',
        503
      );
    }

    const supplied = suppliedPassword(request);
    if (supplied === null || !(await sameSecret(supplied, expected))) {
      return text('Authentication required.\n', 401, {
        'www-authenticate': `Basic realm="${REALM}", charset="UTF-8"`,
      });
    }

    // Scan queue. The board cannot start anything on the user's machine, so a
    // request is parked here and a local poller claims it.
    if (pathname === '/api/scan') {
      if (!queueConfigured()) {
        return new Response(JSON.stringify({
          ok: false, error: 'queue not configured',
          hint: 'vercel blob create-store <name> --access private --yes, then redeploy.',
        }), { status: 503, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
      }
      try {
        if (request.method === 'POST') {
          const at = await enqueue();
          return new Response(JSON.stringify({ ok: true, queued: true, requestedAt: at }), {
            status: 202, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
        if (request.method === 'GET') {
          const q = await pending();
          return new Response(JSON.stringify({ ok: true, pending: q }), {
            status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
        if (request.method === 'DELETE') {
          // The local poller claims the oldest request when it starts work.
          const q = await pending();
          if (!q.length) {
            return new Response(JSON.stringify({ ok: true, claimed: null }), {
              status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
          }
          await del(q[0].url);
          return new Response(JSON.stringify({ ok: true, claimed: q[0].pathname }), {
            status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
      } catch (err) {
        return new Response(JSON.stringify({ ok: false, error: String(err.message || err) }), {
          status: 502, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
      }
      return text('Method not allowed.\n', 405, { allow: 'GET, POST, DELETE' });
    }

    // Application documents: the exact resume and cover letter an employer
    // received. Same password gate as the board, never cached, never indexed.
    if (pathname.startsWith('/doc/')) {
      const name = decodeURIComponent(pathname.slice('/doc/'.length));
      const b64 = Object.prototype.hasOwnProperty.call(DOCUMENTS, name)
        ? DOCUMENTS[name] : null;
      if (!b64) return text('No such document.\n', 404);
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      return new Response(request.method === 'HEAD' ? null : bytes, {
        status: 200,
        headers: {
          'content-type': 'application/pdf',
          // inline so it opens in the browser rather than forcing a save
          'content-disposition': `inline; filename="${name.replace(/"/g, '')}"`,
          ...BASE_HEADERS,
        },
      });
    }

    // Apply request from the Watching tab: build the documents and drive the
    // application. Queued like the others, because the board cannot start work
    // on the agent's machine.
    if (pathname === '/api/apply') {
      if (!queueConfigured()) {
        return new Response(JSON.stringify({ ok: false, error: 'queue not configured' }), {
          status: 503, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
      }
      try {
        if (request.method === 'POST') {
          const body = await request.json().catch(() => ({}));
          const id = String(body.applicationId || '').trim();
          if (!id || !/^[A-Za-z0-9._-]+$/.test(id)) {
            return new Response(JSON.stringify({ ok: false, error: 'bad applicationId' }), {
              status: 400, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
          }
          const at = new Date().toISOString();
          await put(`${APPLY_PREFIX}${at.replace(/[:.]/g, '-')}__${id}.json`,
            JSON.stringify({ applicationId: id, requestedAt: at }),
            { access: 'private', contentType: 'application/json' });
          return new Response(JSON.stringify({ ok: true, queued: id }), {
            status: 202, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
        if (request.method === 'GET') {
          return new Response(JSON.stringify({ ok: true, pending: await pendingUnder(APPLY_PREFIX) }), {
            status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
      } catch (err) {
        return new Response(JSON.stringify({ ok: false, error: String(err.message || err) }), {
          status: 502, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
      }
      return text('Method not allowed.\n', 405, { allow: 'GET, POST' });
    }

    // "Mark submitted" from the Needs you tab. Queued the same way as scans:
    // the board cannot write to the store on the agent's machine.
    if (pathname === '/api/submitted') {
      if (!queueConfigured()) {
        return new Response(JSON.stringify({ ok: false, error: 'queue not configured' }), {
          status: 503, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
      }
      try {
        if (request.method === 'POST') {
          const body = await request.json().catch(() => ({}));
          const id = String(body.applicationId || '').trim();
          if (!id || !/^[A-Za-z0-9._-]+$/.test(id)) {
            return new Response(JSON.stringify({ ok: false, error: 'bad applicationId' }), {
              status: 400, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
          }
          const at = new Date().toISOString();
          await put(`${MARK_PREFIX}${at.replace(/[:.]/g, '-')}__${id}.json`,
            JSON.stringify({ applicationId: id, company: body.company || '', role: body.role || '', markedAt: at }),
            { access: 'private', contentType: 'application/json' });
          return new Response(JSON.stringify({ ok: true, marked: id }), {
            status: 202, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
        if (request.method === 'GET') {
          return new Response(JSON.stringify({ ok: true, pending: await pendingUnder(MARK_PREFIX) }), {
            status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
        if (request.method === 'DELETE') {
          const q = await pendingUnder(MARK_PREFIX);
          if (!q.length) {
            return new Response(JSON.stringify({ ok: true, claimed: null }), {
              status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
          }
          // The store is private, so the blob URL is not fetchable without a
          // token. Everything the poller needs is in the pathname:
          // marked/<timestamp>__<applicationId>.json
          const base = q[0].pathname.slice(MARK_PREFIX.length).replace(/\.json$/, '');
          const sep = base.indexOf('__');
          const claimed = sep === -1
            ? { applicationId: base }
            : { markedAt: base.slice(0, sep), applicationId: base.slice(sep + 2) };
          await del(q[0].url);
          return new Response(JSON.stringify({ ok: true, claimed }), {
            status: 200, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
        }
      } catch (err) {
        return new Response(JSON.stringify({ ok: false, error: String(err.message || err) }), {
          status: 502, headers: { 'content-type': 'application/json', ...BASE_HEADERS } });
      }
      return text('Method not allowed.\n', 405, { allow: 'GET, POST, DELETE' });
    }

    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return text('Method not allowed.\n', 405, { allow: 'GET, HEAD' });
    }

    return new Response(request.method === 'HEAD' ? null : HTML, {
      status: 200,
      headers: {
        'content-type': 'text/html; charset=utf-8',
        'content-security-policy': CSP,
        ...BASE_HEADERS,
      },
    });
  },
};
