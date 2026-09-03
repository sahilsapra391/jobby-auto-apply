// Read Class 1 artifacts out of R2, server side, for the board to serve.
//
// The bucket is private and has no public access and no CDN in front of it. The
// board's document links come here, the same Basic-auth gate every other route
// checks has already run, and the function then reads the object and returns
// the bytes. Nothing signed ever reaches the browser.
//
// It used to redirect to a presigned URL, which is what the architecture plan
// first specified, and the measurement is why it does not any more: the
// redirect is a second round trip and the Vercel leg alone is 370 to 430ms, so
// a 119KB resume cost about 180ms more than the KV path it replaced. Mobile is
// the main surface and pays that twice over. Proxying is one round trip, and it
// is also the shape the Workers-era design lands on, where an R2 binding serves
// the object after authz with no signing at all.
//
// presignGet stays because signing is still how the device authenticates its
// own uploads and how an operator gets a temporary link by hand. It is no
// longer on the serving path.
//
// This is a second credential, not the KV one. The Workers KV token cannot see
// R2 and the R2 token cannot see KV. Both live in the environment and neither
// is ever logged.
//
// scripts/r2.py signs the same requests on the device. The two are one contract
// in two languages, and tests/test_r2_contract.py compares them URL for URL,
// because a first version of the company-logo split passed with a suffix
// missing from one half.

const REGION = 'auto';
const SERVICE = 's3';
const ALGORITHM = 'AWS4-HMAC-SHA256';

// Long enough to open a PDF on a phone over a bad connection, short enough that
// the URL sitting in someone's history is not a standing grant.
export const DEFAULT_EXPIRY_SECONDS = 300;

// Named rather than required: the bucket is not a secret, and a fourth thing
// to set up on the Vercel project is a fourth thing to get wrong.
export const DEFAULT_BUCKET = 'jobby-artifacts';

export function conf(env) {
  // R2 and Workers KV are in the same Cloudflare account and the queue already
  // configured that id. The two credentials have no fallback and never will:
  // the KV token cannot see R2 and the R2 token cannot see KV.
  const account = env.R2_ACCOUNT_ID || env.CF_ACCOUNT_ID;
  const bucket = env.R2_BUCKET || DEFAULT_BUCKET;
  const accessKeyId = env.R2_ACCESS_KEY_ID;
  const secretAccessKey = env.R2_SECRET_ACCESS_KEY;
  if (!account || !bucket || !accessKeyId || !secretAccessKey) return null;
  return { account, bucket, accessKeyId, secretAccessKey,
           host: `${account}.r2.cloudflarestorage.com` };
}

export function configured(env) {
  return conf(env) !== null;
}

// AWS leaves A-Z a-z 0-9 - _ . ~ alone and percent-encodes the rest.
// encodeURIComponent spares !'()* as well, so those four are put back by hand.
function uriEncode(value, keepSlash) {
  const encoded = encodeURIComponent(value)
    .replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
  return keepSlash ? encoded.replace(/%2F/g, '/') : encoded;
}

const enc = new TextEncoder();

async function hmac(key, message) {
  const imported = await crypto.subtle.importKey(
    'raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', imported, enc.encode(message)));
}

async function sha256Hex(message) {
  const digest = await crypto.subtle.digest('SHA-256', enc.encode(message));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function signingKey(secret, datestamp) {
  let key = await hmac(enc.encode('AWS4' + secret), datestamp);
  key = await hmac(key, REGION);
  key = await hmac(key, SERVICE);
  return hmac(key, 'aws4_request');
}

function stamps(now) {
  const iso = new Date(now).toISOString();
  const amzDate = iso.slice(0, 19).replace(/[-:]/g, '') + 'Z';
  return { amzDate, datestamp: amzDate.slice(0, 8) };
}

/**
 * A URL that GETs one object and stops working when it expires.
 *
 * Throws rather than returning null when the credential is absent, because a
 * caller that treats a missing URL as "nothing to serve" is how a document link
 * becomes a silent 404 instead of an error someone can act on.
 */
export async function presignGet(env, key, expires = DEFAULT_EXPIRY_SECONDS, now = Date.now(),
                                 responseHeaders = null) {
  const c = conf(env);
  if (!c) throw new Error('R2 is not configured: need R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY');
  const canonicalPath = `/${uriEncode(c.bucket, false)}/${uriEncode(key, true)}`;
  // response-content-* overrides, signed like everything else so nobody can
  // rewrite the filename on a URL we minted. Without them a redirected PDF
  // arrives named after its key, and a phone offers to save "resume.pdf" from
  // an application id it has never heard of.
  const extra = Object.entries(responseHeaders || {}).map(([k, v]) => [k, String(v)]);
  return presign(c, canonicalPath, extra, expires, now);
}

/**
 * The signing every presigned URL shares.
 *
 * Factored out when `listKeys` arrived, and factored rather than copied for the
 * reason this file exists in two languages at all: SigV4 is already written
 * twice and `tests/test_r2_contract.py` holds those two to one behaviour across
 * twelve complete URLs. A third copy inside this same file, differing only in
 * its canonical path, would be the one that drifts and the contract test would
 * not be looking at it.
 *
 * `presignGet`'s output is unchanged by the extraction, which that test is what
 * proves.
 */
async function presign(c, canonicalPath, extraQuery, expires, now) {
  const { amzDate, datestamp } = stamps(now);
  const credential = `${c.accessKeyId}/${datestamp}/${REGION}/${SERVICE}/aws4_request`;

  const query = [
    ['X-Amz-Algorithm', ALGORITHM],
    ['X-Amz-Credential', credential],
    ['X-Amz-Date', amzDate],
    ['X-Amz-Expires', String(Math.trunc(expires))],
    ['X-Amz-SignedHeaders', 'host'],
    ...(extraQuery || []),
  ];
  // Canonical query strings sort by encoded name, then encoded value.
  const canonicalQuery = query
    .map(([k, v]) => [uriEncode(k, false), uriEncode(v, false)])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0)))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');

  const canonicalRequest = [
    'GET', canonicalPath, canonicalQuery, `host:${c.host}\n`, 'host', 'UNSIGNED-PAYLOAD',
  ].join('\n');
  const scope = `${datestamp}/${REGION}/${SERVICE}/aws4_request`;
  const toSign = [ALGORITHM, amzDate, scope, await sha256Hex(canonicalRequest)].join('\n');
  const signature = [...await hmac(await signingKey(c.secretAccessKey, datestamp), toSign)]
    .map((b) => b.toString(16).padStart(2, '0')).join('');

  return `https://${c.host}${canonicalPath}?${canonicalQuery}&X-Amz-Signature=${signature}`;
}

/**
 * Every key under one prefix, paged to the end.
 *
 * **One call answers a whole page of rows, which is the point.** The Submitted
 * lane needs to know which of fifty applications have a stored answer record,
 * and asking per row is fifty round trips for one column -- the shape that put
 * fifty failing `/api/company-logo` requests on every Found page load. A prefix
 * listing is one request for the lot.
 *
 * Keys only. The caller wants existence, and parsing sizes and timestamps out
 * of the XML would be three more things to get wrong for nobody's benefit.
 */
export async function listKeys(env, prefix, limit = 1000) {
  const c = conf(env);
  if (!c) throw new Error('R2 is not configured: need R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY');
  const bucketPath = `/${uriEncode(c.bucket, false)}`;
  const keys = [];
  let token = null;
  // Bounded rather than `while (true)`: a truncation flag that never clears is
  // an infinite loop against somebody's bucket, and this repo has a rule about
  // loops nobody is watching.
  for (let page = 0; page < 100; page += 1) {
    const extra = [['list-type', '2'], ['prefix', prefix],
                   ['max-keys', String(Math.trunc(limit))]];
    if (token) extra.push(['continuation-token', token]);
    const url = await presign(c, bucketPath, extra, 60, Date.now());
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`R2 list answered ${res.status} for prefix ${prefix}`);
    }
    const xml = await res.text();
    for (const m of xml.matchAll(/<Key>([^<]*)<\/Key>/g)) {
      keys.push(m[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>').replace(/&quot;/g, '"')
                    .replace(/&apos;/g, "'"));
    }
    const truncated = /<IsTruncated>true<\/IsTruncated>/.test(xml);
    const next = xml.match(/<NextContinuationToken>([^<]*)<\/NextContinuationToken>/);
    if (!truncated || !next) return keys;
    token = next[1];
  }
  return keys;
}

/**
 * GET one object with a signed request the caller never sees.
 *
 * Signing happens in the query string and the URL is consumed immediately by
 * this same fetch, which keeps one signer rather than adding header-based SigV4
 * as a second implementation to keep in step. The expiry is short because the
 * URL lives for the length of one call.
 *
 * `range` is forwarded when the client sent one, so a phone's PDF viewer can
 * ask for the first pages instead of waiting on the whole file.
 *
 * Throws when the credential is missing or the fetch itself fails. A 404 comes
 * back as a response, not an exception, because an object the index knows and
 * the bucket does not is a fall-through to KV rather than a fault.
 */
export async function fetchObject(env, key, range = null) {
  const url = await presignGet(env, key, 60);
  const headers = {};
  if (range) headers.range = range;
  return fetch(url, { headers });
}
