// Read or publish sanitized status for the OCI document worker.
//
// Provider credentials never enter KV. This record says only whether a CLI is
// installed, logically connected, and healthy enough to accept document work.
import { readFileSync } from 'node:fs';
import { kvGet, kvPut } from './kv.mjs';

export const WORKER_STATUS_KEY = 'jobby:worker:documents:status';

async function getWorkerStatus(env) {
  const raw = await kvGet(env, WORKER_STATUS_KEY);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function putWorkerStatus(env, status) {
  return kvPut(env, WORKER_STATUS_KEY, JSON.stringify(status));
}

const [command, path] = process.argv.slice(2);
const env = process.env;
const PROVIDERS = new Set(['openai', 'anthropic', 'gemini']);
const STATUSES = new Set([
  'connected', 'disconnected', 'connecting', 'checking', 'needs_auth',
  'missing_cli', 'error',
]);

const cleanText = (value, limit = 180) => String(value || '')
  .replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, limit);

function sanitize(input) {
  const providers = [];
  for (const item of input.providers || []) {
    if (!item || !PROVIDERS.has(item.id)) continue;
    providers.push({
      id: item.id,
      installed: Boolean(item.installed),
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
    worker: 'documents',
    state: input.state === 'stopped' ? 'stopped' : 'online',
    checkedAt: new Date().toISOString(),
    version: cleanText(input.version, 80),
    activeProvider: PROVIDERS.has(input.activeProvider) ? input.activeProvider : null,
    providers,
  };
}

if (command === 'read') {
  console.log(JSON.stringify((await getWorkerStatus(env)) || null));
} else if (command === 'write' && path) {
  const input = JSON.parse(readFileSync(path, 'utf8'));
  const status = sanitize(input);
  await putWorkerStatus(env, status);
  console.log(JSON.stringify(status));
} else {
  console.error('usage: worker-status.mjs read | write <status.json>');
  process.exit(2);
}
