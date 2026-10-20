// What a Settings card says, run against the real rules.
//
// `_ui/providerLive.ts` is the retired board's `paintHost` ported: the labels,
// the " · Active" marker, "Waiting for sign-in" while a click waits on the
// device, and offline when the device's status is missing or older than
// fifteen minutes. Transpiled and called here, because a state machine that is
// only read is a state machine nobody has seen take a wrong turn.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');
const js = ts.transpileModule(
  readFileSync(join(REPO, 'apps/web/app/_ui/providerLive.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
).outputText;
const live = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const { cardView, readOf, settled, LABELS, FAST_POLL_MS, SLOW_POLL_MS } = live;
const keys = await import(join(REPO, 'deploy/device_keys.mjs'));
const v2keys = await import(join(REPO, 'apps/web/app/_ui/deviceKeys.generated.mjs'));

const row = (over = {}) => ({
  id: 'openai', installed: true, enabled: true, connected: true, active: false,
  status: 'connected', checkedAt: '2026-10-20T00:00:00Z', detail: '', ...over,
});
const liveRead = (...rows) => ({
  kind: 'live', providers: Object.fromEntries(rows.map((r) => [r.id, r])) });

test('the cadence is the board\'s: a second while waiting, thirty otherwise', () => {
  assert.equal(FAST_POLL_MS, 1000);
  assert.equal(SLOW_POLL_MS, 30000);
});

test('the labels are the board\'s own words', () => {
  const python = readFileSync(join(REPO, 'scripts/settings_panel.py'), 'utf8');
  for (const [state, label] of Object.entries(LABELS)) {
    assert.ok(python.includes(`"${state}": "${label}"`),
      `${state} reads "${label}" here and something else on the board`);
  }
});

test('the provider in use is marked Active', () => {
  const view = cardView(undefined, liveRead(row({ active: true })), undefined, 'openai');
  assert.equal(view.label, 'Connected · Active');
  assert.equal(view.active, true);
  assert.equal(view.tone, 'ok');
  const other = cardView(undefined, liveRead(row()), undefined, 'openai');
  assert.equal(other.label, 'Connected');
  assert.equal(other.active, false);
});

test('a Connect waits for sign-in until the device shows it connected', () => {
  const pending = { action: 'connect', started: 0 };
  const before = cardView(undefined, liveRead(row({ enabled: false, connected: false,
    status: 'disconnected' })), pending, 'openai');
  assert.equal(before.label, 'Waiting for sign-in');
  assert.equal(before.tone, 'pending');
  const checking = row({ status: 'checking' });
  assert.equal(settled(pending, checking), false);
  assert.equal(cardView(undefined, liveRead(checking), pending, 'openai').label,
               'Waiting for sign-in');
  assert.equal(settled(pending, row()), true);
  assert.equal(cardView(undefined, liveRead(row()), pending, 'openai').label, 'Connected');
});

test('a Disconnect waits until the device shows it off', () => {
  const pending = { action: 'disconnect', started: 0 };
  assert.equal(cardView(undefined, liveRead(row()), pending, 'openai').label, 'Disconnecting');
  const off = row({ enabled: false, connected: false, status: 'disconnected' });
  assert.equal(settled(pending, off), true);
  const view = cardView(undefined, liveRead(off), pending, 'openai');
  assert.equal(view.label, 'Not connected');
  assert.equal(view.action, 'connect');
});

test('a status the device stopped refreshing is offline, not connected', () => {
  const stored = { status: 'connected', health: 'healthy', detail: null };
  const view = cardView(stored, { kind: 'offline', lastHeard: '2026-10-19T00:00:00Z' },
                        undefined, 'openai');
  assert.equal(view.label, 'Your Device is offline');
  assert.equal(view.tone, 'bad');
  assert.equal(view.active, false);
});

test('a status this deployment could not read is not called offline', () => {
  const view = cardView(undefined, { kind: 'unreadable', why: 'KV 500' }, undefined, 'openai');
  assert.equal(view.label, 'Status could not be read');
});

test('before the first poll the card draws what the mirror held', () => {
  const view = cardView({ status: 'connected', health: 'quota', detail: 'spent' },
                        { kind: 'unread' }, undefined, 'gemini');
  assert.equal(view.label, 'Connected, quota');
  assert.equal(view.action, 'disconnect');
  assert.equal(view.detail, 'spent');
});

test('the route\'s three answers become three reads', () => {
  assert.equal(readOf(200, { ok: true, local: { providers: [row()] } }).kind, 'live');
  assert.deepEqual(readOf(200, { ok: true, local: null, lastHeard: 'x', why: 'stale' }),
                   { kind: 'offline', lastHeard: 'x' });
  assert.deepEqual(readOf(503, { ok: false, error: 'KV is not configured' }),
                   { kind: 'unreadable', why: 'KV is not configured' });
});

test('fifteen minutes is the line, on both sides of it', () => {
  const now = Date.parse('2026-10-20T12:00:00Z');
  const at = (minutesAgo) => ({ checkedAt: new Date(now - minutesAgo * 60_000).toISOString() });
  assert.ok(keys.freshStatus(at(14.9), now));
  assert.equal(keys.freshStatus(at(15.1), now), null);
  assert.equal(keys.freshStatus({ checkedAt: 'junk' }, now), null);
  assert.equal(keys.freshStatus(null, now), null);
  assert.equal(keys.STATUS_STALE_MS, 15 * 60 * 1000);
});

test('v2 reads the key the device writes', async () => {
  const kvcontent = await import(join(REPO, 'deploy/kvcontent.mjs'));
  assert.equal(v2keys.LOCAL_STATUS_KEY, kvcontent.LOCAL_STATUS_KEY);
  assert.equal(v2keys.LOCAL_STATUS_KEY, 'jobby:worker:local:status');
  const route = readFileSync(join(REPO, 'apps/web/app/api/providers/route.ts'), 'utf8');
  assert.match(route, /readDeviceJson<.*>\(LOCAL_STATUS_KEY\)/);
  assert.match(route, /freshStatus\(raw\)/);
});
