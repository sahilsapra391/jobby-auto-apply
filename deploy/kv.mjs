// Low-level Cloudflare Workers KV access over the REST API, shared by the click
// queue and the board content.
//
// Both moved here off Vercel: the queue because Blob suspended the store under
// polling, the board because baking it into the deployment meant a redeploy for
// every change and Vercel's free plan allows a hundred of those a day.

export function conf(env) {
  const account = env.CF_ACCOUNT_ID;
  const namespace = env.CF_KV_NAMESPACE_ID;
  const token = env.CF_API_TOKEN;
  if (!account || !namespace || !token) return null;
  return {
    base: `https://api.cloudflare.com/client/v4/accounts/${account}/storage/kv/namespaces/${namespace}/values`,
    token,
  };
}

export function configured(env) {
  return conf(env) !== null;
}

/** Raw string value, or null when the key has never been written. */
export async function kvGet(env, key) {
  const c = conf(env);
  if (!c) throw new Error('KV is not configured: need CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, CF_API_TOKEN');
  const r = await fetch(`${c.base}/${encodeURIComponent(key)}`, {
    headers: { authorization: `Bearer ${c.token}` },
  });
  // A key that was never written is a 404, which is emptiness rather than a
  // fault. Everything else is a real failure and must not be mistaken for it.
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`KV read failed for ${key}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return await r.text();
}

export async function kvPut(env, key, value) {
  const c = conf(env);
  if (!c) throw new Error('KV is not configured');
  const body = new FormData();
  body.set('value', value);
  body.set('metadata', '{}');
  const r = await fetch(`${c.base}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { authorization: `Bearer ${c.token}` },
    body,
  });
  if (!r.ok) throw new Error(`KV write failed for ${key}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return true;
}
