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
