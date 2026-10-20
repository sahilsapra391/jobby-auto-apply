// @ts-expect-error -- generated from deploy/kv.mjs, plain JS with no types.
// Same idiom as kvqueue.generated.mjs: one KV client, not two.
import { kvGet, configured } from "./kv.generated.mjs";

/**
 * Read one JSON value the device published to KV. Read-only: nothing in v2
 * writes these keys, and nothing here can.
 *
 * **Three answers, never two.** Found, not found, and could not look. A KV
 * outage or a missing credential must not come back as `null`, because `null`
 * is what "the device never wrote this" looks like, and the two send a reader
 * to opposite places: one to the device, the other to this deployment's
 * environment. CLAUDE.md's "could not look is never is not there" is the rule.
 */
export type DeviceRead<T> =
  | { state: "found"; value: T }
  | { state: "absent" }
  | { state: "unreadable"; why: string };

function env() {
  return {
    CF_ACCOUNT_ID: process.env.CF_ACCOUNT_ID,
    CF_KV_NAMESPACE_ID: process.env.CF_KV_NAMESPACE_ID,
    CF_API_TOKEN: process.env.CF_API_TOKEN,
  };
}

export async function readDeviceJson<T = unknown>(key: string): Promise<DeviceRead<T>> {
  const e = env();
  if (!configured(e)) {
    const missing = Object.entries(e).filter(([, v]) => !v).map(([k]) => k);
    return { state: "unreadable",
             why: `KV is not configured on this deployment; set ${missing.join(", ")}.` };
  }
  let raw: string | null;
  try {
    raw = await kvGet(e, key);
  } catch (err) {
    return { state: "unreadable",
             why: err instanceof Error ? err.message : String(err) };
  }
  if (raw === null) return { state: "absent" };
  try {
    return { state: "found", value: JSON.parse(raw) as T };
  } catch {
    return { state: "unreadable", why: `${key} holds something that is not JSON` };
  }
}
