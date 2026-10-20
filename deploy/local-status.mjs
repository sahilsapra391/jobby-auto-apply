#!/usr/bin/env node
// Publish sanitized provider status for the Mac application worker.

import { readFileSync } from 'node:fs';
import { kvGet, kvPut } from './kv.mjs';
import { LOCAL_STATUS_KEY } from './device_keys.mjs';

async function getLocalStatus(env) {
  const raw = await kvGet(env, LOCAL_STATUS_KEY);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function putLocalStatus(env, status) {
  return kvPut(env, LOCAL_STATUS_KEY, JSON.stringify(status));
}

const [command, path] = process.argv.slice(2);
const PROVIDERS = new Set(['openai', 'anthropic', 'gemini']);
const STATUSES = new Set([
  'connected', 'disconnected', 'connecting', 'checking', 'needs_auth',
  'missing_cli', 'error',
]);

function cleanText(value, limit = 180) {
  return String(value || '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, limit);
}

export function sanitize(input) {
  const rows = [];
  for (const item of input?.providers || []) {
    if (!item || !PROVIDERS.has(item.id)) continue;
    rows.push({
      id: item.id,
      installed: item.installed === null ? null : Boolean(item.installed),
      enabled: Boolean(item.enabled),
      connected: Boolean(item.connected),
      active: Boolean(item.active),
      status: STATUSES.has(item.status) ? item.status : 'error',
      checkedAt: cleanText(item.checkedAt, 40) || null,
      detail: cleanText(item.detail),
    });
  }
  return {
    schemaVersion: 1,
    worker: 'local',
    checkedAt: new Date().toISOString(),
    activeProvider: PROVIDERS.has(input?.activeProvider) ? input.activeProvider : null,
    providers: rows,
  };
}

if (command === 'read') {
  console.log(JSON.stringify((await getLocalStatus(process.env)) || null));
} else if (command === 'write' && path) {
  const status = sanitize(JSON.parse(readFileSync(path, 'utf8')));
  await putLocalStatus(process.env, status);
  console.log(JSON.stringify(status));
} else {
  console.error('usage: local-status.mjs read | write <status.json>');
  process.exit(2);
}
