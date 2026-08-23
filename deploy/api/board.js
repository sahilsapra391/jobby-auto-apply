// Serves the generated dashboard behind a password.
//
// The board names companies, roles, referral contacts and drafted messages, so
// it is not something to leave on a public URL. Vercel's own Deployment
// Protection does not cover production domains on the Hobby plan, which is why
// the gate lives here instead of in project settings.
//
// Fails closed: with no DASHBOARD_PASSWORD set, it serves 503 rather than the
// board.

import { getBoard, getDoc, getLocalStatus } from '../kvcontent.mjs';

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
  // 'unsafe-inline' for the board's own scripts, 'self' for Vercel Analytics
  "script-src 'unsafe-inline' 'self'",
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

import { readQueue, enqueue as kvEnqueue, claimOne, putConfig, takeConfig, configured, CONFIG_FIELD } from '../kvqueue.mjs';

// Vercel Blob suspended this store once the watcher started polling it. The
// queue now lives in Cloudflare Workers KV under a single key, because KV
// allows 100,000 key reads a day but only 1,000 list requests: a queue you have
// to list to read is unusable at any poll rate worth having. See kvqueue.mjs.
// /api/retry is deliberately the apply queue: re-running the agent after the
// user has cleared a blocker is the same operation as applying, and the
// runner already reuses documents rather than rebuilding them.
// /api/watch and /api/pass carry a job id from the Found lane rather than an
// application id. Same shape, same idempotency rule, same single key.
const QUEUES = { '/api/scan': 'scan', '/api/apply': 'apply', '/api/retry': 'apply',
                 '/api/submitted': 'marked', '/api/drop': 'drop',
                 '/api/restore': 'restore', '/api/focus': 'focus',
                 '/api/watch': 'watch', '/api/pass': 'pass' };

const json = (body, status) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json', ...BASE_HEADERS },
});

function rejectCrossSiteMutation(request) {
  if (request.method !== 'POST') return null;
  const expectedOrigin = new URL(request.url).origin;
  if (request.headers.get('origin') !== expectedOrigin) {
    return json({ ok: false, error: 'cross-site request refused' }, 403);
  }
  const contentType = (request.headers.get('content-type') || '').toLowerCase();
  if (!contentType.startsWith('application/json')) {
    return json({ ok: false, error: 'application/json required' }, 415);
  }
  if (request.headers.get('x-jobby-request') !== '1') {
    return json({ ok: false, error: 'request token missing' }, 403);
  }
  return null;
}

const PROVIDERS = new Set(['openai', 'anthropic', 'gemini']);
const PROVIDER_ACTIONS = new Set(['connect', 'disconnect', 'refresh']);
const PROVIDER_TARGETS = {
  local: 'providerLocal',
};
const WORKER_STALE_MS = 15 * 60 * 1000;

function freshStatus(value) {
  if (!value || typeof value !== 'object') return null;
  const checked = Date.parse(value.checkedAt || '');
  if (!Number.isFinite(checked) || Date.now() - checked > WORKER_STALE_MS) return null;
  return value;
}

async function handleProviderAction(request) {
  if (!configured(process.env)) {
    return json({ ok: false, error: 'queue not configured' }, 503);
  }
  if (request.method !== 'POST') {
    return text('Method not allowed.\n', 405, { allow: 'POST' });
  }
  try {
    const body = await request.json().catch(() => ({}));
    const provider = String(body.provider || '').trim();
    const action = String(body.action || '').trim();
    const target = String(body.target || '').trim();
    if (!PROVIDERS.has(provider)) {
      return json({ ok: false, error: 'unknown provider' }, 400);
    }
    if (!PROVIDER_ACTIONS.has(action)) {
      return json({ ok: false, error: 'unknown provider action' }, 400);
    }
    // Older published boards sent target=both. Treat that as the one local
    // registry so an open tab keeps working after the local-only migration.
    const normalizedTarget = target === 'both' ? 'local' : target;
    const queues = normalizedTarget === 'local'
      ? Object.entries(PROVIDER_TARGETS)
      : Object.entries(PROVIDER_TARGETS).filter(([name]) => name === target);
    if (!queues.length) {
      return json({ ok: false, error: 'unknown provider target' }, 400);
    }
    const id = `${action}-${provider}`;
    // The sortable key makes the first successful Connect click sticky.
    const connectionKey = action === 'connect'
      ? `1-${String(Date.now()).padStart(13, '0')}-${crypto.randomUUID()}`
      : null;
    const results = await Promise.all(queues.map(async ([name, queue]) => ({
      target: name,
      ...(await kvEnqueue(process.env, queue, id, {
        provider,
        action,
        ...(connectionKey ? { connectionKey } : {}),
      })),
    })));
    return json({ ok: true, target, provider, action, queued: results }, 202);
  } catch (err) {
    return json({ ok: false, error: String(err.message || err) }, 502);
  }
}

// All three buttons are the same shape: POST to ask for something, GET to see
// what is waiting, DELETE for a local runner to claim the oldest.
async function handleQueue(request, queue, pathname = '') {
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
      // "Add your own link" rides the scan queue rather than growing an
      // endpoint of its own. Only http(s) is accepted: the value ends up in an
      // agent's prompt and in an href, and neither should take a javascript:.
      const extra = pathname === '/api/retry' ? { retry: true } : {};
      if (queue === 'scan' && body.url) {
        const link = String(body.url).trim();
        if (!/^https?:\/\//i.test(link)) return json({ ok: false, error: 'the link must be http or https' }, 400);
        if (link.length > 2000) return json({ ok: false, error: 'that link is too long' }, 400);
        extra.url = link;
      }
      // Idempotent per application id, so the board can re-send a click it
      // remembers without the role being applied to twice.
      return json({ ok: true, ...(await kvEnqueue(process.env, queue, id, extra)) }, 202);
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

// The saved search rides the same key as the queues. Read-modify-write, so a
// save landing at the same instant as a claim can be lost — the same bargain
// every other field here already makes, and the board re-sends what it
// remembers.
async function handleSearchConfig(request) {
  if (!configured(process.env)) {
    return json({ ok: false, error: 'queue not configured' }, 503);
  }
  try {
    if (request.method === 'GET') {
      const held = (await readQueue(process.env))[CONFIG_FIELD];
      return json({ ok: true, pending: held || null }, 200);
    }
    if (request.method === 'POST') {
      const body = await request.json().catch(() => null);
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        return json({ ok: false, error: 'the search config must be a JSON object' }, 400);
      }
      // Validated on the machine that owns the schema, not here. This endpoint
      // parks it; scripts/search_config.py refuses anything it cannot accept,
      // and a board that could write an invalid config straight into the store
      // would be a board that can break every later scan.
      return json({ ok: true, ...(await putConfig(process.env, body)) }, 202);
    }
    if (request.method === 'DELETE') {
      return json({ ok: true, taken: await takeConfig(process.env) }, 200);
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
      const health = await getBoard(process.env).catch(() => null);
      return new Response(JSON.stringify({
        ok: true,
        generatedAt: health ? health.generatedAt : null,
        boardPublished: health !== null,
      }), {
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

    const rejectedMutation = rejectCrossSiteMutation(request);
    if (rejectedMutation) return rejectedMutation;

    // The board buttons. A page on Vercel cannot start a process on the
    // user's machine, so each click is parked in the queue and the local
    // watcher claims it and spawns the agent that does the work.
    if (QUEUES[pathname]) {
      return handleQueue(request, QUEUES[pathname], pathname);
    }

    if (pathname === '/api/provider') {
      return handleProviderAction(request);
    }

    if (pathname === '/api/providers' && request.method === 'GET') {
      const [board, localStatus] = await Promise.all([
        getBoard(process.env).catch(() => null),
        getLocalStatus(process.env).catch(() => null),
      ]);
      return json({
        ok: true,
        local: freshStatus(localStatus) ||
          freshStatus(board && board.providers ? board.providers : null),
      }, 200);
    }

    // The saved search. Not a queue: one object, written by the board and
    // taken by the watcher, living in the same key as everything else because
    // a second key would be a second read on every three-second poll.
    if (pathname === '/api/search-config') {
      return handleSearchConfig(request);
    }

    // Application documents: the exact resume and cover letter an employer
    // received. Same password gate as the board, never cached, never indexed.
    if (pathname.startsWith('/doc/')) {
      const name = decodeURIComponent(pathname.slice('/doc/'.length));
      // The name comes off the board but arrives as a URL path, so treat it as
      // untrusted: only a plain filename may become a KV key.
      if (!/^[A-Za-z0-9._-]+$/.test(name)) return text('No such document.\n', 404);
      const b64 = await getDoc(process.env, name).catch(() => null);
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

    // The page itself comes from KV, so publishing it is a write rather than a
    // deployment. Deployments carry only code now.
    let board;
    try {
      board = await getBoard(process.env);
    } catch (err) {
      return text(`The board could not be read: ${String(err.message || err)}\n`, 502);
    }
    if (!board) {
      return text('No board has been published yet.\n' +
                  'Run: python3 scripts/publish.py\n', 503);
    }
    return new Response(request.method === 'HEAD' ? null : board.html, {
      status: 200,
      headers: {
        'content-type': 'text/html; charset=utf-8',
        'content-security-policy': CSP,
        ...BASE_HEADERS,
      },
    });
  },
};
