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

const KEY = 'jobby:queue';
export const QUEUES = ['apply', 'scan', 'marked'];

const empty = () => ({ apply: [], scan: [], marked: [] });

function conf(env) {
  const account = env.CF_ACCOUNT_ID;
  const namespace = env.CF_KV_NAMESPACE_ID;
  const token = env.CF_API_TOKEN;
  if (!account || !namespace || !token) return null;
  return {
    url: `https://api.cloudflare.com/client/v4/accounts/${account}/storage/kv/namespaces/${namespace}/values/${encodeURIComponent(KEY)}`,
    token,
  };
}

export function configured(env) {
  return conf(env) !== null;
}

export async function readQueue(env) {
  const c = conf(env);
  if (!c) throw new Error('queue not configured: need CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, CF_API_TOKEN');
  const r = await fetch(c.url, { headers: { authorization: `Bearer ${c.token}` } });
  // A key that has never been written is a 404, which is an empty queue, not a
  // fault. Every other non-OK status is a real failure and must not be
  // mistaken for "nothing waiting".
  if (r.status === 404) return empty();
  if (!r.ok) throw new Error(`KV read failed: ${r.status} ${(await r.text()).slice(0, 200)}`);
  try {
    const parsed = JSON.parse(await r.text());
    return { ...empty(), ...parsed };
  } catch {
    return empty();
  }
}

export async function writeQueue(env, data) {
  const c = conf(env);
  if (!c) throw new Error('queue not configured');
  const body = new FormData();
  body.set('value', JSON.stringify(data));
  body.set('metadata', '{}');
  const r = await fetch(c.url, {
    method: 'PUT',
    headers: { authorization: `Bearer ${c.token}` },
    body,
  });
  if (!r.ok) throw new Error(`KV write failed: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return true;
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
