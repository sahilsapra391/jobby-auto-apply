// The owner session cookie, run rather than read.
//
// **A gate nobody has seen refuse anything is a hypothesis.** The resume
// wording editor is the one owner-only surface on the site, and the cookie in
// front of it has four ways to be wrong that a review would not notice: an
// unsigned value accepted, a tampered one accepted, an expired one accepted,
// or a correct one refused. Each is exercised here against the real
// `_ui/ownerToken.ts`, transpiled and imported, with a fixed clock.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = join(REPO, 'apps/web/app/_ui/ownerToken.ts');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

const js = ts.transpileModule(readFileSync(SOURCE, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const owner = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const {
  ownerConfig, signSession, verifySession, passwordMatches, cookieAttributes,
  SESSION_SECONDS, SESSION_COOKIE,
} = owner;

const NOW = 1_790_000_000_000;
const ENV = {
  JOBBY_OWNER_PASSWORD: 'correct horse battery',
  JOBBY_SESSION_SECRET: 'x'.repeat(48),
};
const cfg = ownerConfig(ENV);

test('the fixture is a configured owner, or nothing below means anything', () => {
  assert.equal(cfg.ok, true);
});

test('a valid session is accepted for thirty days and not a second longer', () => {
  const token = signSession(cfg, NOW);
  assert.equal(verifySession(cfg, token, NOW), true);
  assert.equal(verifySession(cfg, token, NOW + (SESSION_SECONDS - 1) * 1000), true);
  assert.equal(SESSION_SECONDS, 30 * 24 * 60 * 60);
});

test('an expired session is refused', () => {
  const token = signSession(cfg, NOW);
  assert.equal(verifySession(cfg, token, NOW + SESSION_SECONDS * 1000), false);
  assert.equal(verifySession(cfg, token, NOW + (SESSION_SECONDS + 3600) * 1000), false);
});

test('an unsigned or malformed value is refused', () => {
  for (const bad of [undefined, null, '', 'owner', 'v1', 'v1.123',
                     `v1.${Math.floor(NOW / 1000) + 3600}.`,
                     `v1.${Math.floor(NOW / 1000) + 3600}`,
                     `v2.${Math.floor(NOW / 1000) + 3600}.abc`,
                     'v1.notanumber.abc', 'v1.1.2.3']) {
    assert.equal(verifySession(cfg, bad, NOW), false, `accepted ${bad}`);
  }
});

test('a tampered session is refused', () => {
  const token = signSession(cfg, NOW);
  const [v, exp, sig] = token.split('.');
  // A later expiry with the old signature: the forgery that matters most.
  assert.equal(verifySession(cfg, `${v}.${Number(exp) + 86400}.${sig}`, NOW), false);
  // One character of the signature changed.
  const flipped = sig.slice(0, -1) + (sig.endsWith('A') ? 'B' : 'A');
  assert.equal(verifySession(cfg, `${v}.${exp}.${flipped}`, NOW), false);
  // A plausible signature of the right length that nobody minted.
  assert.equal(verifySession(cfg, `${v}.${exp}.${'A'.repeat(sig.length)}`, NOW), false);
});

test('a token claiming to outlive any session is refused even if signed', () => {
  // Signed "now" a year from now, so its expiry is far past what a session
  // minted at NOW could carry.
  const future = signSession(cfg, NOW + 365 * 86400 * 1000);
  assert.equal(verifySession(cfg, future, NOW), false);
});

test('rotating the secret or the password ends every session', () => {
  const token = signSession(cfg, NOW);
  const rotatedSecret = ownerConfig({ ...ENV, JOBBY_SESSION_SECRET: 'y'.repeat(48) });
  const rotatedPassword = ownerConfig({ ...ENV, JOBBY_OWNER_PASSWORD: 'another long password' });
  assert.equal(verifySession(rotatedSecret, token, NOW), false);
  assert.equal(verifySession(rotatedPassword, token, NOW), false);
});

test('the password check accepts the password and nothing else', () => {
  assert.equal(passwordMatches(cfg, 'correct horse battery'), true);
  for (const bad of ['', 'correct horse batter', 'correct horse battery ',
                     'CORRECT HORSE BATTERY', null, undefined, 42, {}]) {
    assert.equal(passwordMatches(cfg, bad), false, `accepted ${String(bad)}`);
  }
});

test('unset or short variables mean the editor is off, and say how to turn it on', () => {
  for (const env of [{}, { JOBBY_OWNER_PASSWORD: ENV.JOBBY_OWNER_PASSWORD },
                     { JOBBY_SESSION_SECRET: ENV.JOBBY_SESSION_SECRET },
                     { ...ENV, JOBBY_OWNER_PASSWORD: 'short' },
                     { ...ENV, JOBBY_SESSION_SECRET: 'too-short-secret' }]) {
    const off = ownerConfig(env);
    assert.equal(off.ok, false);
    assert.match(off.why, /JOBBY_(OWNER_PASSWORD|SESSION_SECRET)/);
    assert.match(off.why, /Vercel/);
  }
});

test('the cookie is HttpOnly, Secure, SameSite=Lax and site-wide', () => {
  const attrs = cookieAttributes(SESSION_SECONDS);
  assert.deepEqual(attrs, {
    httpOnly: true, secure: true, sameSite: 'lax', path: '/', maxAge: SESSION_SECONDS,
  });
  assert.equal(SESSION_COOKIE, 'jobby.owner');
});

test('the session route signs in with these functions and nothing looser', () => {
  // The route is where a pass-through would hide: a `POST` that set the
  // cookie without asking `passwordMatches`, or a cookie set without the
  // attributes above.
  const route = readFileSync(
    join(REPO, 'apps/web/app/api/owner/session/route.ts'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '');
  const check = route.indexOf('passwordMatches(');
  const set = route.indexOf('cookies.set(SESSION_COOKIE, signSession(');
  assert.ok(check > 0 && set > check,
    'the sign-in route sets the owner cookie without checking the password first');
  assert.ok(route.includes('cookieAttributes(SESSION_SECONDS)'),
    'the sign-in cookie is not set with the shared attributes');
});
