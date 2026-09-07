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
 * **`inflight` is here because three buttons send it, and for five days none
 * of them reached the device.** This map held `dropped` alone, under a comment
 * saying `review`, `active` and `inflight` are stages the daemon moves an
 * application through and a board observes rather than orders. That is true of
 * the daemon's own transitions and false of a button labelled Apply: Watching's
 * Apply and both Retry controls post `status: "inflight"`, so `order` was never
 * called, `applications.status` was written anyway, and the daemon heard
 * nothing.
 *
 * Found by parity, on one row: Scale Media / Data Analyst, session file
 * `watch`, Postgres `inflight`, apply queue empty, POST recorded at
 * 2026-09-07T00:18:36Z with a 200. The board said it had started work on a
 * real application and no machine had.
 *
 * It maps to `apply` rather than to a queue of its own, which is what the
 * Python board does: `/api/retry` is deliberately the apply queue, because
 * re-running the agent after a blocker is cleared is the same operation as
 * applying. Three controls, one work order.
 *
 * **The omissions that remain are still omissions.** `review` and `active` are
 * genuinely daemon-owned and no control sends them;
 * `tests/status_writes_reach_the_daemon.test.mjs` is what keeps that true, by
 * failing when a control sends a status this map does not carry.
 */
export const STATUS_QUEUE: Record<string, string> = {
  dropped: "drop",
  inflight: "apply",
  // Undrop, in the Dropped lane. The guard found this one, not a person: it
  // was stranded the same way and for the same reason, and it is the queue the
  // Python board has always sent it to (`/api/restore` -> `restore`, claimed
  // at `watch.py:2465`). One bug reported, two found.
  watch: "restore",
};

/**
 * The queue an *action* goes to. Not a status, and the distinction is the
 * design rather than tidiness.
 *
 * `STATUS_QUEUE` maps a state the application is moving to. "Open & finish"
 * moves nothing: it asks the device to put a window on the user's screen
 * showing the form a parked run left behind. Modelling it as a fourth status
 * would mean inventing a state to carry an instruction, and then something
 * would have to write that state into `applications.status` -- which is the
 * Class B column the device owns, and the exact mistake that left three
 * controls writing a state and ordering no work.
 *
 * So an action enqueues and writes nothing at all. There is no row to update,
 * because nothing about the application changed.
 *
 * **`focus` is not new to the device.** `watch.py:2432` has always claimed
 * this queue: it resolves the posting URL from the session or `roles.json`,
 * picks the application's own slot, calls `browser.show(slot)` to relaunch it
 * with a window -- the slots run `--headless=new` -- and then `browser.focus`
 * to open the page. The Python board sent it from every card. v2 rendered the
 * button disabled, saying this was "not something v2 can do", and that was
 * never true.
 */
export const ACTION_QUEUE: Record<string, string> = {
  focus: "focus",
};

/**
 * The queue an *event* goes to. The fourth control that wrote a row and
 * ordered nothing, found by needing it.
 *
 * **"Confirm submitted" is how a human submission becomes a record, and it
 * only reached Postgres.** The route inserted an `application_events` row and
 * returned 200; `~/.job-application/history.jsonl` never learned, so
 * `dupe_check` would have offered the role again, `sync_board` would have left
 * the card in Needs You, and the R2 push that files the answer record next to
 * the documents never ran. The board said Submitted and the device did not
 * know the application existed in that state.
 *
 * It was invisible to `status_writes_reach_the_daemon.test.mjs` for a reason
 * worth naming rather than patching over: that guard walked the `status` and
 * `action` props and there are three namespaces, not two. The check now reads
 * all three, which is the same correction as deriving the parity comparison
 * from the mirror's own columns instead of listing eleven of twenty-eight.
 *
 * `marked` is the device queue the Python board has always used
 * (`/api/submitted` -> `marked`, claimed at `watch.py:2604`), and
 * `apply_marks.py` is what writes the `completed` event. **No nonce, and that
 * is correct rather than a shortcut**: the nonce proves the agent that clicked
 * Submit is the agent that recorded the confirmation, and here no agent
 * clicked anything. A person did. `finish.py --completed` stays nonce-gated
 * for the agent path and this path never touches it.
 *
 * `submitting` and `abandoned` are in the route's `EVENTS` set and no control
 * sends either, so neither is here. `needs_you` is the same. Adding a control
 * that sends one means adding its queue, and the guard fails until it does.
 */
export const EVENT_QUEUE: Record<string, string> = {
  completed: "marked",
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
