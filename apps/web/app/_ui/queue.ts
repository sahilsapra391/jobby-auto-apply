/**
 * A v2 click reaches the daemon the way a board click does: through the KV
 * queue the watcher polls.
 *
 * **This exists because the two halves were different systems.** Measured
 * 2026-09-04: `watch.poll_queues` reads Cloudflare KV, the write route wrote
 * Postgres and nothing else, `scripts/jobs_store.py` contains zero SELECTs, and
 * no module under `scripts/` reads Postgres back into the file store. So
 * pressing Watch on a v2 card wrote a row, v2's own lane view read that row and
 * moved the card, and the watcher never learned anything had happened. The
 * board said Watching; the application never started.
 *
 * **The enqueue goes before the commit, which is the opposite of `publish`.**
 * A publish is a notification: it commits first, so nothing is announced that
 * the store does not hold, and a lost publish self-corrects on the next fetch.
 * An enqueue *is the work*, and a lost enqueue never happens and nothing ever
 * fetches it back. So the failure directions are:
 *
 *     enqueue ok, commit fails   the daemon does the work and writes the row
 *                                itself through dual-write, which is exactly
 *                                what a board click does today
 *     enqueue fails              the request errors, nothing is written, and
 *                                the reader presses it again
 *
 * The map mirrors `deploy/api/board.js`'s `QUEUES` rather than inventing
 * names: the same three decisions the board's Found card sends, under the same
 * queue names, so the watcher needs no new case.
 *
 * **A bare `apply` with no preceding `watch` is deliberate.** The board sends
 * watch then apply, and it is the watch that creates the session. Both ride one
 * KV key through read-modify-write with no compare-and-swap, which is how an
 * apply enqueue ate a watch enqueue on 2026-08-28 and wedged the lane for 88
 * ticks. `watch.ensure_application_session` now promotes the head from the pool
 * when its session is missing, so one enqueue is enough and sending two would
 * re-create the race this side of the fence.
 */

// @ts-expect-error -- generated from deploy/kvqueue.mjs, plain JS with no
// types. Same idiom as r2.generated.mjs in the artifact route: one way of
// importing these, not two.
import { enqueue, configured } from "./kvqueue.generated.mjs";

/** Raised when the queue cannot take the work, so the caller writes nothing. */
export class QueueRefused extends Error {
  readonly status: number;
  constructor(message: string, status = 502) {
    super(message);
    this.name = "QueueRefused";
    this.status = status;
  }
}

/**
 * The queue each Found decision goes to, mirroring the board's own endpoints.
 *
 * `pass` is on the list because the board has a `pass` queue and sends it. It
 * records "never offer this again", which is work the device does to its own
 * pool rather than an application; leaving it out would make v2 the only board
 * whose Pass the daemon never hears about.
 */
export const DECISION_QUEUE: Record<string, string> = {
  watch: "watch",
  apply: "apply",
  pass: "pass",
};

/**
 * The queue a status change goes to, for the lanes that are not Found.
 *
 * Only `dropped`, and the omissions are the point. `review`, `active` and
 * `inflight` are stages the daemon itself moves an application through; a
 * board does not order them, it observes them. Enqueueing one would be v2
 * inventing a work order the Python board has no button for.
 */
export const STATUS_QUEUE: Record<string, string> = {
  dropped: "drop",
};

function env() {
  return {
    CF_ACCOUNT_ID: process.env.CF_ACCOUNT_ID,
    CF_KV_NAMESPACE_ID: process.env.CF_KV_NAMESPACE_ID,
    CF_API_TOKEN: process.env.CF_API_TOKEN,
  };
}

/** Whether the bridge can run at all. */
export function bridged(): boolean {
  return configured(env());
}

/**
 * Put one click on the daemon's queue. Throws `QueueRefused` on any doubt.
 *
 * **An unconfigured queue is refused, never skipped.** Falling through to the
 * commit would write a row for work nobody is going to do, which is the exact
 * state this module exists to end -- and it would look like success. The
 * missing variables are named, because "the queue is not configured" sends a
 * reader to the wrong place three times out of four.
 */
export async function order(queue: string, applicationId: string,
                            extra: Record<string, unknown> = {}) {
  const e = env();
  if (!configured(e)) {
    const missing = Object.entries(e)
      .filter(([, v]) => !v).map(([k]) => k);
    throw new QueueRefused(
      `the work queue is not configured on this deployment, so this ` +
      `click would be recorded and never acted on. ` +
      `Set ${missing.join(", ")}.`, 503);
  }
  if (!applicationId || !/^[A-Za-z0-9._-]+$/.test(applicationId)) {
    throw new QueueRefused(`unusable application id ${applicationId}`, 400);
  }
  try {
    return await enqueue(e, queue, applicationId, extra);
  } catch (err) {
    throw new QueueRefused(
      `the work queue would not take this click, so nothing was written: ` +
      `${err instanceof Error ? err.message : String(err)}`);
  }
}
