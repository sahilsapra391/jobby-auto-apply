// The board's click queue, on Cloudflare Workers KV.
//
// This replaced Vercel Blob, which suspended the store after the watcher began
// polling it every two seconds. The lesson is in the shape of the free limits
// rather than in the vendor: KV allows 100,000 key reads a day but only 1,000
// list requests, so a queue that has to be *listed* to be read is unusable at
// any poll rate worth having. Blob had the same problem with a different name.
//
// So the entire queue lives under ONE key. A poll is a single read: 28,800 a
// day at a three second interval, against a 100,000 ceiling. Writes happen only
// when somebody clicks or a runner claims, a few dozen a day against 1,000.
//
// The cost of coalescing is that a write is read-modify-write with no
// compare-and-swap, so a click landing in the same instant as a claim can be
// lost. That is survivable here and already covered: the board re-sends clicks
// it remembers, and enqueue dedupes by application id.

import { kvGet, kvPut, configured } from './kv.mjs';

const KEY = 'jobby:queue';
export const QUEUES = ['apply', 'scan', 'marked', 'drop', 'restore', 'focus'];
export { configured };

const empty = () => ({ apply: [], scan: [], marked: [], drop: [], restore: [], focus: [] });

export async function readQueue(env) {
  const raw = await kvGet(env, KEY);
  if (raw === null) return empty();
  try {
    return { ...empty(), ...JSON.parse(raw) };
  } catch {
    return empty();
  }
}

export async function writeQueue(env, data) {
  return kvPut(env, KEY, JSON.stringify(data));
}

export async function enqueue(env, queue, applicationId) {
  const data = await readQueue(env);
  const list = data[queue] || (data[queue] = []);
  // Idempotent per application, so the board can safely re-send a click it
  // remembers without the role being applied to twice.
  if (list.some((x) => x.applicationId === applicationId)) {
    return { queued: applicationId, deduped: true };
  }
  list.push({ applicationId, at: new Date().toISOString() });
  await writeQueue(env, data);
  return { queued: applicationId };
}

export async function claimOne(env, queue) {
  const data = await readQueue(env);
  const list = data[queue] || [];
  if (!list.length) return [];
  const head = list.shift();
  await writeQueue(env, data);
  return [head];
}

export async function claimAll(env, queue) {
  const data = await readQueue(env);
  const list = data[queue] || [];
  if (!list.length) return [];
  data[queue] = [];
  await writeQueue(env, data);
  return list;
}
