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

import { readQueue, enqueue as kvEnqueue, claimOne, configured } from '../kvqueue.mjs';
import { DOCUMENTS } from './_documents.js';

// Vercel Blob suspended this store once the watcher started polling it. The
// queue now lives in Cloudflare Workers KV under a single key, because KV
// allows 100,000 key reads a day but only 1,000 list requests: a queue you have
// to list to read is unusable at any poll rate worth having. See kvqueue.mjs.
// /api/retry is deliberately the apply queue: re-running the agent after the
// user has cleared a blocker is the same operation as applying, and the
// runner already reuses documents rather than rebuilding them.
const QUEUES = { '/api/scan': 'scan', '/api/apply': 'apply', '/api/retry': 'apply',
                 '/api/submitted': 'marked', '/api/drop': 'drop',
                 '/api/restore': 'restore' };

const json = (body, status) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json', ...BASE_HEADERS },
});

// All three buttons are the same shape: POST to ask for something, GET to see
// what is waiting, DELETE for a local runner to claim the oldest.
async function handleQueue(request, queue) {
  if (!configured(process.env)) {
    return json({
      ok: false, error: 'queue not configured',
      hint: 'Set CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID and CF_API_TOKEN on this project, then redeploy.',
    }, 503);
  }
  try {
    if (request.method === 'POST') {
      const body = await request.json().catch(() => ({}));
      let id = String(body.applicationId || '').trim();
      if (queue === 'scan' && !id) {
        id = new Date().toISOString().replace(/[:.]/g, '-');
      }
      if (!id || !/^[A-Za-z0-9._-]+$/.test(id)) return json({ ok: false, error: 'bad applicationId' }, 400);
      // Idempotent per application id, so the board can re-send a click it
      // remembers without the role being applied to twice.
      return json({ ok: true, ...(await kvEnqueue(process.env, queue, id)) }, 202);
    }
    if (request.method === 'GET') {
      return json({ ok: true, pending: (await readQueue(process.env))[queue] || [] }, 200);
    }
    if (request.method === 'DELETE') {
      const claimed = await claimOne(process.env, queue);
      return json({ ok: true, claimed: claimed[0] || null }, 200);
    }
  } catch (err) {
    return json({ ok: false, error: String(err.message || err) }, 502);
  }
  return text('Method not allowed.\n', 405, { allow: 'GET, POST, DELETE' });
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

    // The three board buttons. A page on Vercel cannot start a process on the
    // user's machine, so each click is parked in the queue and the local
    // watcher claims it and spawns the agent that does the work.
    if (QUEUES[pathname]) {
      return handleQueue(request, QUEUES[pathname]);
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
