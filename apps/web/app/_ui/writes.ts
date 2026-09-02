import { createHash } from "node:crypto";
import { pool, accountId } from "./db";
import { KINDS } from "./events";

/**
 * The write path. **Every entry point here is behind `live_board`.**
 *
 * The credential v2 holds is write-capable: it is the device's own, and it owns
 * the tables. So nothing about the database stops a write reaching the user's
 * store, and the flag is the only thing that does. That is why the check is
 * here, in the one function every endpoint must call, rather than left to each
 * route to remember, and why it defaults to refusing when the flag is absent
 * or the account cannot be read.
 */
export class WriteRefused extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

/**
 * Whether writes are on for this account, read fresh on every request.
 *
 * Not cached. A flag that is cached is a flag that stays on for the lifetime of
 * a warm lambda after somebody turns it off, and "turn it off" is the entire
 * reason it exists.
 */
export async function writesEnabled(account: string): Promise<boolean> {
  const { rows } = await pool().query(
    "select coalesce((flags->>'live_board')::boolean, false) as on from accounts where id = $1",
    [account]);
  return rows.length > 0 && rows[0].on === true;
}

/** Refuse unless the flag says otherwise. The default is always no. */
export async function requireWritesEnabled(account: string) {
  if (!(await writesEnabled(account))) {
    throw new WriteRefused(
      503,
      "writes are off for this account. v2 is read-only until the Phase 1 " +
      "parity gate closes; the credential can write, so this flag is what " +
      "stops it.");
  }
}

export const digest = (body: unknown) =>
  createHash("sha256").update(JSON.stringify(body ?? null)).digest("hex");

/** How long a key is honoured. */
export const IDEMPOTENCY_HOURS = 24;

/**
 * The stored answer for this key, or null to go ahead.
 *
 * **A key reused with a different body is refused, not served.** Returning the
 * first response to a second question answers something nobody asked, and it
 * does it silently, which is the worst available combination.
 */
export async function replay(account: string, key: string, body: unknown) {
  const { rows } = await pool().query(
    `select request_digest, status, response from idempotency_keys
      where account_id = $1 and key = $2
        and created_at > now() - ($3 || ' hours')::interval`,
    [account, key, String(IDEMPOTENCY_HOURS)]);
  if (!rows.length) return null;
  if (rows[0].request_digest !== digest(body)) {
    throw new WriteRefused(
      409,
      "this Idempotency-Key was used for a different request. Reusing one " +
      "for two bodies is a client fault, and answering the second with the " +
      "first result would be worse than refusing.");
  }
  return { status: rows[0].status as number, body: rows[0].response };
}

export async function remember(
  account: string, key: string, body: unknown, status: number, response: unknown,
) {
  await pool().query(
    `insert into idempotency_keys
       (account_id, key, request_digest, status, response)
     values ($1, $2, $3, $4, $5::jsonb)
     on conflict (account_id, key) do nothing`,
    [account, key, digest(body), status, JSON.stringify(response)]);
}

/**
 * Publish an event to the account's channel, after the commit.
 *
 * **The commit precedes the publish and the ordering is not a preference.** A
 * published event with no row behind it is a board showing a change the
 * database does not have, and it vanishes on the next reconnect, which is worse
 * than a slow board because the user watched it work.
 *
 * Failing here is the recoverable direction: the row is right, the board is
 * briefly stale, and the next fetch corrects it. So it is logged loudly and
 * never retried inline, and it never rolls back the commit.
 */
export async function publish(account: string, kind: string, payload: unknown) {
  if (!KINDS.has(kind)) throw new Error(`unknown event kind ${kind}`);
  const url = process.env.JOBBY_CHANNEL_URL;
  if (!url) {
    console.error("channel not configured; the row is written and the board " +
                  "will not hear about it until it refetches", { kind });
    return false;
  }
  try {
    const res = await fetch(`${url}/publish`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "authorization": `Bearer ${process.env.JOBBY_CHANNEL_TOKEN ?? ""}`,
      },
      body: JSON.stringify({ account_id: account, kind, payload }),
    });
    if (!res.ok) throw new Error(`channel answered ${res.status}`);
    return true;
  } catch (err) {
    console.error("publish failed after the commit; the row is correct and " +
                  "the board is stale until its next fetch", { kind, err });
    return false;
  }
}

export { accountId };
