// Mint short-lived presigned GETs for Class 1 artifacts in R2.
//
// The bucket is private and has no public access and no CDN in front of it.
// The board's document links come here, the same Basic-auth gate every other
// route checks has already run, and this hands the browser a URL that works for
// a few minutes and then does not. The bucket credential never reaches the page.
//
// This is a second credential, not the KV one. The Workers KV token cannot see
// R2, and an R2 Workers binding cannot presign; presigning needs the
// S3-compatible token. Both live in the environment and neither is ever logged.
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
  const { amzDate, datestamp } = stamps(now);
  const credential = `${c.accessKeyId}/${datestamp}/${REGION}/${SERVICE}/aws4_request`;
  const canonicalPath = `/${uriEncode(c.bucket, false)}/${uriEncode(key, true)}`;

  const query = [
    ['X-Amz-Algorithm', ALGORITHM],
    ['X-Amz-Credential', credential],
    ['X-Amz-Date', amzDate],
    ['X-Amz-Expires', String(Math.trunc(expires))],
    ['X-Amz-SignedHeaders', 'host'],
  ];
  // response-content-* overrides, signed like everything else so nobody can
  // rewrite the filename on a URL we minted. Without them a redirected PDF
  // arrives named after its key, and a phone offers to save "resume.pdf" from
  // an application id it has never heard of.
  for (const [k, v] of Object.entries(responseHeaders || {})) query.push([k, String(v)]);
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
