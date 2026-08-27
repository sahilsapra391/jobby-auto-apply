// Serves the dashboard's queue, document, logo, health, and provider endpoints.
//
// The board names companies, roles, referral contacts and drafted messages, so
// it is not something to leave on a public URL. Vercel's own Deployment
// Protection does not cover production domains on the Hobby plan, which is why
// the gate lives here instead of in project settings.
//
// Fails closed: with no DASHBOARD_PASSWORD set, it serves 503 rather than the
// board.

import { gunzipSync } from 'node:zlib';
import { getBoard, getBoardState, getDoc, getDocIndex, getLocalStatus } from '../kvcontent.mjs';
import { fetchObject, configured as r2Configured } from '../r2.mjs';

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

// The same envelope api/page.py reads: gzip, base64, with a ceiling so a
// malformed value cannot be a decompression bomb.
const MAX_STATE_BYTES = 20 * 1024 * 1024;

function decodeBoardState(envelope) {
  try {
    const compressed = Buffer.from(envelope.payload, 'base64');
    const bytes = gunzipSync(compressed, { maxOutputLength: MAX_STATE_BYTES });
    return JSON.parse(bytes.toString('utf8'));
  } catch {
    return null;
  }
}

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

// --- the Profile page ------------------------------------------------------
//
// The Memory Bank is 150 entries and about 74 KB. Measured on 2026-08-25, the
// board at its embed cap is 3,840,457 bytes against a 3.7 MB budget, which
// leaves 39,274 bytes: the bank does not fit in the page and never will. So it
// travels in the published state and is fetched by the panel that needs it,
// the same bargain /api/posting already makes for posting sections.
//
// Every answer travels, sensitive ones included: the owner asked for a board
// with no hidden category on it, and it is his data behind his own password.
// That reverses when a second account exists; see issue #82. What is still
// never here is a secret, because secrets are refused at capture in store.py
// and were never written in the first place.
//
// Single-user today. The payload names the account it belongs to and this
// route refuses one that names a different account, so the day per-user state
// arrives this fails closed instead of serving a stranger's answers.
const VIEWER_USER_ID = process.env.JOBBY_USER_ID || 'account-1';

// Every field of an answer the page is allowed to see.
//
// **An allowlist drops new fields silently, and it did.** The device started
// publishing `kind`, `category` and `groupKey`, the page was built to read
// them, every test passed, and the board showed 156 uncategorised rows because
// this list had not been told. Nothing errored. The three names below carry a
// reminder rather than a comment because the next field will arrive the same
// way.
//
// `sensitive` and `dataClass` travel too: they no longer change what the page
// draws, they are the classification the multi-tenant encryption path selects
// on, per issue #82.
const ANSWER_FIELDS = ['key', 'question', 'state', 'tags', 'updatedAt', 'version',
  'sensitive', 'dataClass', 'askedBy', 'openedAt', 'lastAskedAt',
  'answeredAt', 'note', 'remembered',
  // Added with the Profile rebuild. Adding a field to publishable_state is only
  // half of shipping it; this is the other half.
  'kind', 'category', 'groupKey'];

function profilePayload(state) {
  const bank = (state && state.memoryBank) || null;
  if (!bank) return null;
  const owner = bank.userId || VIEWER_USER_ID;
  if (owner !== VIEWER_USER_ID) return { mismatch: owner };
  const answers = (Array.isArray(bank.answers) ? bank.answers : [])
    .filter((entry) => entry && typeof entry === 'object')
    .map((entry) => {
      const out = {};
      for (const field of ANSWER_FIELDS) {
        if (entry[field] !== undefined) out[field] = entry[field];
      }
      // `answer` is where the field started and four entries still use it.
      const value = entry.value !== undefined && entry.value !== null
        ? entry.value : entry.answer;
      out.value = value === undefined || value === null ? null : String(value).slice(0, 4000);
      return out;
    });
  return {
    userId: owner,
    answers,
    identities: Array.isArray(bank.identities) ? bank.identities : [],
    resumeContent: bank.resumeContent && typeof bank.resumeContent === 'object'
      ? bank.resumeContent : null,
    // The resume as it renders, which is what the editor shows. Dropped here
    // for the same reason the three fields above were: the payload grew and
    // this projection did not.
    resumeRender: typeof bank.resumeRender === 'string' ? bank.resumeRender : '',
  };
}

// Bounded because every one of these rides the single queue key the watcher
// polls every few seconds, and an unbounded field there is a way to make the
// whole board slow for everyone.
const LIMITS = { editId: 120, key: 120, question: 600, value: 4000, state: 20 };

function boundedString(value, limit) {
  if (typeof value !== 'string') return null;
  return value.length <= limit ? value : null;
}

const ANSWER_ACTIONS = new Set(['save', 'delete']);
const RESUME_ACTIONS = new Set(['default', 'content', 'remove', 'add', 'update']);

async function handleAnswerEdit(request) {
  if (!configured(process.env)) return json({ ok: false, error: 'queue not configured' }, 503);
  if (request.method !== 'POST') return text('Method not allowed.\n', 405, { allow: 'POST' });
  try {
    const body = await request.json().catch(() => ({}));
    const editId = boundedString(body.editId, LIMITS.editId);
    if (!editId || !/^[A-Za-z0-9._-]+$/.test(editId)) {
      return json({ ok: false, error: 'bad editId' }, 400);
    }
    const action = String(body.action || 'save');
    if (!ANSWER_ACTIONS.has(action)) return json({ ok: false, error: 'unknown action' }, 400);
    const version = Number.isInteger(body.version) && body.version >= 0 ? body.version : 0;
    const extra = { action, version };
    const key = boundedString(body.key, LIMITS.key);
    if (key !== null && key !== '') extra.key = key;
    if (action === 'save') {
      const question = boundedString(body.question, LIMITS.question);
      const value = boundedString(body.value, LIMITS.value);
      if (!question || !question.trim()) return json({ ok: false, error: 'the question is required' }, 400);
      if (value === null) return json({ ok: false, error: 'that answer is too long' }, 400);
      extra.question = question;
      extra.value = value;
      const state = boundedString(body.state, LIMITS.state);
      if (state) extra.state = state;
      if (Array.isArray(body.tags)) {
        extra.tags = body.tags.filter((t) => typeof t === 'string').slice(0, 20)
          .map((t) => t.slice(0, 60));
      }
    } else if (!extra.key) {
      return json({ ok: false, error: 'delete needs the key' }, 400);
    }
    return json({ ok: true, ...(await kvEnqueue(process.env, 'answerEdits', editId, extra)) }, 202);
  } catch (err) {
    return json({ ok: false, error: String(err.message || err) }, 502);
  }
}

async function handleResumeEdit(request) {
  if (!configured(process.env)) return json({ ok: false, error: 'queue not configured' }, 503);
  if (request.method !== 'POST') return text('Method not allowed.\n', 405, { allow: 'POST' });
  try {
    const body = await request.json().catch(() => ({}));
    const editId = boundedString(body.editId, LIMITS.editId);
    if (!editId || !/^[A-Za-z0-9._-]+$/.test(editId)) {
      return json({ ok: false, error: 'bad editId' }, 400);
    }
    const action = String(body.action || '');
    if (!RESUME_ACTIONS.has(action)) return json({ ok: false, error: 'unknown action' }, 400);
    const extra = { action };
    if (action === 'add' || action === 'update') {
      const key = boundedString(body.key, LIMITS.key);
      if (!key || !/^[A-Za-z0-9._-]+$/.test(key)) return json({ ok: false, error: 'bad base resume key' }, 400);
      extra.key = key;
      const label = boundedString(body.label, LIMITS.key);
      if (label) extra.label = label;
      // Two-letter codes only. The store validates again; this keeps anything
      // unbounded out of the queue every poll has to read.
      const countries = Array.isArray(body.countries) ? body.countries : [];
      const codes = countries.filter((c) => typeof c === 'string' && /^[A-Za-z]{2}$/.test(c))
        .slice(0, 64).map((c) => c.toUpperCase());
      if (action === 'add' && !codes.length) {
        return json({ ok: false, error: 'a new base resume needs at least one country' }, 400);
      }
      if (codes.length) extra.countries = codes;
      const copyFrom = boundedString(body.copyFrom, LIMITS.key);
      if (copyFrom) extra.copyFrom = copyFrom;
      if (Number.isInteger(body.priority)) extra.priority = body.priority;
    } else if (action === 'default' || action === 'remove') {
      const key = boundedString(body.key, LIMITS.key);
      if (!key || !/^[A-Za-z0-9._-]+$/.test(key)) return json({ ok: false, error: 'bad base resume key' }, 400);
      extra.key = key;
    } else {
      // The wording, not the layout. Validated against the template's own slot
      // counts on the machine that owns the template; refused here only for
      // size, because the queue is shared and a huge value slows every poll.
      const content = body.content;
      if (!content || typeof content !== 'object' || Array.isArray(content)) {
        return json({ ok: false, error: 'resume content must be an object' }, 400);
      }
      const encoded = JSON.stringify(content);
      if (encoded.length > 64 * 1024) return json({ ok: false, error: 'that resume content is too large' }, 400);
      extra.content = content;
    }
    return json({ ok: true, ...(await kvEnqueue(process.env, 'resumeEdits', editId, extra)) }, 202);
  } catch (err) {
    return json({ ok: false, error: String(err.message || err) }, 502);
  }
}

const LOGO_HOSTS = new Set([
  'cdn-images.himalayas.app',
  'remotive.com',
  'remoteok.com',
  'wwr-pro.s3.amazonaws.com',
  'we-work-remotely.imgix.net',
]);

function trustedLogoSource(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    return LOGO_HOSTS.has(url.hostname.toLowerCase()) ? url : null;
  } catch {
    return null;
  }
}

async function handleCompanyLogo(request, requestUrl) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return text('Method not allowed.\n', 405, { allow: 'GET, HEAD' });
  }
  const source = trustedLogoSource(requestUrl.searchParams.get('src') || '');
  const isSafeDomain = (d) => /^[a-z0-9.-]{3,253}$/.test(d)
    && d.includes('.')
    && !d.startsWith('.')
    && !d.endsWith('.')
    && !d.includes('..');

  // company_logos.py sends one domain. A real employer domain off a direct
  // posting URL arrives on its own; a name we derived arrives with alt=1,
  // meaning try the other TLDs of the same label too. The list lives here
  // rather than in the URL because it is a constant, and spelling it out per
  // job put the Found payload over its page-weight cap.
  const CANDIDATE_TLDS = ['.com', '.io', '.ai', '.co'];
  const primary = String(requestUrl.searchParams.get('domain') || '').trim().toLowerCase();
  const expand = requestUrl.searchParams.get('alt') === '1';
  const domains = [];
  if (isSafeDomain(primary)) {
    domains.push(primary);
    if (expand) {
      const dot = primary.lastIndexOf('.');
      const label = dot > 0 ? primary.slice(0, dot) : primary;
      for (const tld of CANDIDATE_TLDS) {
        const next = label + tld;
        if (next !== primary && isSafeDomain(next)) domains.push(next);
      }
    }
  }

  const targets = source
    ? [source]
    : domains.map((d) => new URL(
        `https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON` +
        `&fallback_opts=TYPE,SIZE&url=https://${d}&size=128`));
  if (!targets.length) return text('No company logo.\n', 404);

  for (const target of targets) {
    try {
      const upstream = await fetch(target, { redirect: 'follow' });
      if (source && !trustedLogoSource(upstream.url)) return text('No company logo.\n', 404);
      const type = (upstream.headers.get('content-type') || '').split(';')[0].toLowerCase();
      if (!upstream.ok || !type.startsWith('image/')) continue;
      const bytes = await upstream.arrayBuffer();
      // A favicon endpoint answers 404 for a domain it does not know, so a
      // wrong guess costs a miss rather than showing a stranger's mark. The
      // floor catches the handful of near-empty replies that are not an icon.
      if (bytes.byteLength < 100 || bytes.byteLength > 750000) continue;
      return new Response(request.method === 'HEAD' ? null : bytes, {
        status: 200,
        headers: {
          ...BASE_HEADERS,
          'cache-control': 'private, max-age=86400',
          'content-type': type,
        },
      });
    } catch {
      // Try the next candidate rather than giving up on the company.
    }
  }
  return text('No company logo.\n', 404);
}

// Application documents: the exact resume and cover letter an employer
// received. They live in R2 now, and the board still links to them by filename
// because that is what every published board and every saved copy already
// says. jobby:doc-index bridges the two.
//
// The function reads the object and returns the bytes. It does not redirect.
// That was the first shape and it was measured: a presigned redirect costs a
// second round trip, and from a laptop on wifi the Vercel leg alone is 370 to
// 430ms whatever it is doing, so a 119KB resume went from 504ms to about
// 684ms. Mobile is this product's main surface and a second round trip costs
// more there, not less. Proxying is one round trip, the same one the KV path
// always had, and R2 is faster than the KV REST API underneath it.
//
// Two things it also buys. No signed URL reaches the browser, so none lands in
// history, in a screenshot, or in a shared link, and nothing the reader holds
// keeps working for five minutes after they lose access. And it is the shape
// the multi-tenant plan is heading for: on Workers an R2 binding serves the
// object directly after the authz check, with no presigning at all. What is
// still needed here is the credential to authenticate the fetch, because Vercel
// has no binding; what goes away is minting URLs for other people to use.
//
// Dual-read, in this order and for this reason. R2 is where new artifacts are
// written, so it is asked first. KV still holds every document published before
// the migration, so it answers for anything the index has not learned yet, and
// it keeps answering until the retirement commit deletes those copies. And when
// both are out, the reply says what went wrong, because returning 404 for a
// credential fault reports it as a document that does not exist.
async function handleDocument(request, pathname) {
  const name = decodeURIComponent(pathname.slice('/doc/'.length));
  // The name comes off the board but arrives as a URL path, so treat it as
  // untrusted: only a plain filename may become a KV key or reach the index.
  //
  // The space is deliberate and was a real bug. Ten of the 426 stored artifacts
  // are named like "Sahil Sapra - Resume - Senior Project Manager.pdf", from
  // before document_contract owned the naming, and two of them are documents an
  // employer actually received and the Submitted tab actually links to. The
  // guard rejected them before anything was even looked up, so those two links
  // have always been dead. A space cannot escape a path segment, cannot open a
  // second KV key, and never reaches the R2 key, which comes from the index and
  // is validated separately below.
  if (!/^[A-Za-z0-9._ -]{1,200}$/.test(name)) return text('No such document.\n', 404);

  // Read once, outside the R2 branch: the KV fallback needs the content type
  // too. Everything KV holds is a PDF, because that is all the pre-R2 publisher
  // ever wrote, so application/pdf was right for as long as documents were the
  // only artifact. They are not: 189 of the 442 indexed artifacts are JSON,
  // HTML or text, and a JSON answer record served as application/pdf is a file
  // the browser refuses rather than a page it renders. Found by a peer session
  // building the answers modal against this route.
  const index = await getDocIndex(process.env).catch(() => null);
  const entry = index && index.docs ? index.docs[name] : null;
  const contentType = (entry && typeof entry.contentType === 'string'
                       && /^[a-z]+\/[a-z0-9.+-]+(; charset=[a-z0-9-]+)?$/i.test(entry.contentType))
    ? entry.contentType
    : 'application/pdf';

  let r2Error = null;
  if (r2Configured(process.env)) {
    // The key is ours, written by the publisher, but it arrives through KV and
    // ends up in a signed request, so it is checked rather than trusted. The
    // dot segments matter: S3 keys are opaque strings and R2 would happily
    // store one containing "..", but an intermediary that normalises the path
    // would then be signing one key and fetching another.
    if (entry && typeof entry.key === 'string'
        && /^u\/[A-Za-z0-9._-]+\/[\x20-\x7e]{1,900}$/.test(entry.key)
        && !entry.key.split('/').includes('..')) {
      try {
        const upstream = await fetchObject(process.env, entry.key, request.headers.get('range'));
        if (upstream.ok || upstream.status === 206) {
          const headers = {
            'content-type': contentType,
            'content-disposition': `inline; filename="${name.replace(/"/g, '')}"`,
            ...BASE_HEADERS,
          };
          // Range matters on a phone: a PDF viewer asks for the first pages and
          // renders them rather than waiting for a 540KB download.
          for (const header of ['content-length', 'content-range', 'accept-ranges']) {
            const value = upstream.headers.get(header);
            if (value) headers[header] = value;
          }
          return new Response(request.method === 'HEAD' ? null : upstream.body,
                              { status: upstream.status, headers });
        }
        // A 404 from R2 means the index is ahead of the bucket. Fall through to
        // KV rather than reporting a fault, because KV may still hold it.
        if (upstream.status !== 404) {
          r2Error = `R2 answered ${upstream.status}`;
        }
      } catch (err) {
        r2Error = String(err.message || err);
      }
    }
  }

  // KV is the not-answered path, not the legacy path, and the difference is
  // load-bearing enough to enumerate. Six ways to arrive here, three of them
  // holding a valid index entry:
  //
  //   1. R2 is not configured at all
  //   2. the index could not be read
  //   3. the index has no entry for this name        <- the legacy documents
  //   4. the entry's key fails the shape or dot-segment check, so a poisoned
  //      index degrades to KV rather than to an error
  //   5. R2 answered 404, which means the index is ahead of the bucket
  //   6. R2 answered something else, or the fetch threw
  //
  // 5 is the one the dual read was built for and the one that is easy to
  // convince yourself cannot happen: it is the window between an artifact
  // being written locally and r2_sync putting it in the bucket. A peer session
  // and I between us wrote three wrong comments about this block before either
  // of us read the branch instead of the previous sentence.
  //
  // Everything KV actually holds is a PDF, because that is all the pre-R2
  // publisher ever wrote, so an indexed non-PDF reaching 5 finds nothing here
  // and falls to the error below rather than being served as the wrong type.
  const b64 = await getDoc(process.env, name).catch(() => null);
  if (b64) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return new Response(request.method === 'HEAD' ? null : bytes, {
      status: 200,
      headers: {
        'content-type': contentType,
        // inline so it opens in the browser rather than forcing a save
        'content-disposition': `inline; filename="${name.replace(/"/g, '')}"`,
        ...BASE_HEADERS,
      },
    });
  }
  if (r2Error) {
    return text(
      `This document is in R2 but it could not be read, so there is nothing to open.\n` +
      `${r2Error}\nThe file is still on the machine that made it.\n`, 502);
  }
  return text('No such document.\n', 404);
}

export default {
  async fetch(request) {
    const requestUrl = new URL(request.url);
    const { pathname } = requestUrl;

    // Unauthenticated, and says nothing about the search: enough to confirm a
    // deploy landed without typing the password.
    if (pathname === '/healthz') {
      const [state, legacy] = await Promise.all([
        getBoardState(process.env).catch(() => null),
        getBoard(process.env).catch(() => null),
      ]);
      return new Response(JSON.stringify({
        ok: true,
        rendererCommit: process.env.VERCEL_GIT_COMMIT_SHA || null,
        rendererSchemaVersion: 1,
        stateGeneratedAt: state ? state.generatedAt : null,
        stateSchemaVersion: state ? state.rendererSchemaVersion : null,
        statePublished: state !== null,
        legacyGeneratedAt: legacy ? legacy.generatedAt : null,
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

    if (pathname === '/api/company-logo') {
      return handleCompanyLogo(request, requestUrl);
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

    // The Profile page's own data. Fetched rather than embedded; see
    // profilePayload above for the measurement that forced that.
    if (pathname === '/api/profile' && request.method === 'GET') {
      const envelope = await getBoardState(process.env).catch(() => null);
      const state = envelope ? decodeBoardState(envelope) : null;
      if (!state) {
        return json({ ok: false, error: 'the board state could not be read' }, 503);
      }
      const payload = profilePayload(state);
      if (payload && payload.mismatch) {
        return json({ ok: false, error: 'this profile belongs to another account' }, 403);
      }
      if (!payload) {
        return json({
          ok: false,
          error: 'this board was published before the Profile page existed. '
                 + 'Publish again from Your Device to fill it.',
        }, 409);
      }
      return json({ ok: true, ...payload }, 200);
    }

    if (pathname === '/api/answer') return handleAnswerEdit(request);
    if (pathname === '/api/resume') return handleResumeEdit(request);

    // What the posting listed under its own headings, for one expanded card.
    // On demand rather than in the page, because the embedded payload is capped
    // at 4MB and sections average about 265 bytes a record; the published state
    // is capped at 20MB and sits at 9.4, so it can afford to carry them.
    if (pathname === '/api/posting' && request.method === 'GET') {
      const id = String(requestUrl.searchParams.get('id') || '');
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) return json({ ok: false }, 400);
      const envelope = await getBoardState(process.env).catch(() => null);
      const state = envelope ? decodeBoardState(envelope) : null;
      const job = state && state.jobs ? state.jobs[id] : null;
      const sections = (job && job.sections) || {};
      const trim = (lines) => (Array.isArray(lines) ? lines : [])
        .map((line) => String(line || '').slice(0, 400))
        .filter(Boolean)
        .slice(0, 12);
      return json({
        ok: true,
        responsibilities: trim(sections.responsibilities),
        requirements: trim(sections.requirements),
      }, 200);
    }

    // Which lane each application is in, so the board can move a card without
    // reloading three megabytes of page to find out. Deliberately small: the
    // status is the only thing that decides a lane, and the client already
    // knows how to draw the card it is moving.
    if (pathname === '/api/lanes' && request.method === 'GET') {
      const envelope = await getBoardState(process.env).catch(() => null);
      const state = envelope ? decodeBoardState(envelope) : null;
      if (!state) return json({ ok: false }, 503);
      const sessions = {};
      for (const session of state.sessions || []) {
        const id = session && session.applicationId;
        if (id) sessions[String(id)] = String(session.status || '');
      }
      // The published state's own stamp, which is how the board tells whether
      // the server has caught up with a move it made optimistically.
      return json({
        ok: true,
        generatedAt: (envelope && envelope.generatedAt) || null,
        sessions,
      }, 200);
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
    // received, and the record of what its form asked. Same password gate as
    // the board, never cached, never indexed. One route, because an answer
    // record is the same class of per-application artifact the PDFs are and a
    // second serving path would be a second auth gate to keep honest.
    if (pathname.startsWith('/doc/')) {
      return handleDocument(request, pathname);
    }

    // Apply request from the Watching tab: build the documents and drive the
    // application. Queued like the others, because the board cannot start work
    // on the agent's machine.
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return text('Method not allowed.\n', 405, { allow: 'GET, HEAD' });
    }

    return text('Not found.\n', 404);
  },
};
