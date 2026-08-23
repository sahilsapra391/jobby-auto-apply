// The board's click queue, on Cloudflare Workers KV.
//
// This replaced Vercel Blob, which suspended the store after the watcher began
// polling it every two seconds. The lesson is in the shape of the free limits
// rather than in the vendor: KV allows 100,000 key reads a day but only 1,000
// list requests, so a queue that has to be *listed* to be read is unusable at
// any poll rate worth having. Blob had the same problem with a different name.
//
// The application queue lives under one key. A poll is a single read. Provider
// controls are the exception: the Mac and OCI worker are independent consumers,
// so each control lane has its own key. An OCI claim can therefore never write
// an older copy of the Apply queue back over a fresh browser click.
//
// The cost of coalescing is that a write is read-modify-write with no
// compare-and-swap, so a click landing in the same instant as a claim can be
// lost. That is survivable here and already covered: the board re-sends clicks
// it remembers, and enqueue dedupes by application id.

import { kvGet, kvPut, configured } from './kv.mjs';

const KEY = 'jobby:queue';
const DEDICATED_KEYS = {
  providerLocal: 'jobby:queue:providerLocal',
  providerDocuments: 'jobby:queue:providerDocuments',
};
// watch and pass carry job ids from the Found lane rather than application
// ids, but they are the same shape and the same idempotency rule, so they ride
// the existing array machinery instead of growing a second copy of it.
export const QUEUES = [
  'apply', 'scan', 'marked', 'drop', 'restore', 'focus', 'watch', 'pass',
  'providerLocal', 'providerDocuments',
];
// The saved search is one object, not a queue: the board writes it, the watcher
// takes it, and it stays in this same key. A second key would be a second read
// on every poll, which is the whole thing the single-key design exists to avoid.
export const CONFIG_FIELD = 'searchConfig';
export { configured };

const empty = () => ({
  apply: [], scan: [], marked: [], drop: [], restore: [], focus: [],
  watch: [], pass: [], providerLocal: [], providerDocuments: [],
  [CONFIG_FIELD]: null,
});

export async function readQueue(env) {
  const raw = await kvGet(env, KEY);
  if (raw === null) return empty();
  try {
    return {
      ...empty(),
      ...JSON.parse(raw),
      providerLocal: [],
      providerDocuments: [],
    };
  } catch {
    return empty();
  }
}

export async function writeQueue(env, data) {
  return kvPut(env, KEY, JSON.stringify(data));
}

async function readDedicated(env, queue) {
  const raw = await kvGet(env, DEDICATED_KEYS[queue]);
  if (raw === null) return [];
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

async function writeDedicated(env, queue, list) {
  return kvPut(env, DEDICATED_KEYS[queue], JSON.stringify(list));
}

export async function enqueue(env, queue, applicationId, extra = {}) {
  if (DEDICATED_KEYS[queue]) {
    const list = await readDedicated(env, queue);
    if (list.some((x) => x.applicationId === applicationId)) {
      return { queued: applicationId, deduped: true };
    }
    list.push({ applicationId, at: new Date().toISOString(), ...extra });
    await writeDedicated(env, queue, list);
    return { queued: applicationId };
  }
  const data = await readQueue(env);
  const list = data[queue] || (data[queue] = []);
  // Idempotent per application, so the board can safely re-send a click it
  // remembers without the role being applied to twice.
  if (list.some((x) => x.applicationId === applicationId)) {
    return { queued: applicationId, deduped: true };
  }
  // `extra` carries the pasted URL from "add your own link". Optional, so an
  // entry written before this existed still reads correctly.
  list.push({ applicationId, at: new Date().toISOString(), ...extra });
  await writeQueue(env, data);
  return { queued: applicationId };
}

export async function claimOne(env, queue) {
  if (DEDICATED_KEYS[queue]) {
    const list = await readDedicated(env, queue);
    if (!list.length) return [];
    const head = list.shift();
    await writeDedicated(env, queue, list);
    return [head];
  }
  const data = await readQueue(env);
  const list = data[queue] || [];
  if (!list.length) return [];
  const head = list.shift();
  await writeQueue(env, data);
  return [head];
}

export async function claimAll(env, queue) {
  if (DEDICATED_KEYS[queue]) {
    const list = await readDedicated(env, queue);
    if (!list.length) return [];
    await writeDedicated(env, queue, []);
    return list;
  }
  const data = await readQueue(env);
  const list = data[queue] || [];
  if (!list.length) return [];
  data[queue] = [];
  await writeQueue(env, data);
  return list;
}

/** Park the saved search for the watcher. Last write wins, which is the same
 *  bargain every other field in this key already makes. */
export async function putConfig(env, config) {
  const data = await readQueue(env);
  data[CONFIG_FIELD] = { config, at: new Date().toISOString() };
  await writeQueue(env, data);
  return { saved: true };
}

/** Take the saved search and clear the field in one read-modify-write.
 *  Returns null when there is nothing waiting, which is the common case and
 *  must not cost a write. */
export async function takeConfig(env) {
  const data = await readQueue(env);
  const held = data[CONFIG_FIELD];
  if (!held) return null;
  data[CONFIG_FIELD] = null;
  await writeQueue(env, data);
  return held;
}
