import { NextRequest, NextResponse } from "next/server";
import {
  accountId, publish, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { pool } from "../../_ui/db";
import {
  ACTION_QUEUE, DECISION_QUEUE, EVENT_QUEUE, STATUS_QUEUE, orderFlags, QueueRefused, order,
} from "../../_ui/queue";

/**
 * The board's writes. **Behind `live_board`, which is off.**
 *
 * `applications` is the source of truth. `job_decisions` is the Found lane's
 * exclusion set and nothing here or anywhere else reads it for a lane; a
 * posting entering that set is its own event, not a transition out of Found,
 * because Found is an absence and has no `from`.
 *
 * The order is commit, then publish, and step 3 failing after step 2 is the
 * recoverable direction. See `writes.publish`.
 */
export const dynamic = "force-dynamic";

type Body = {
  applicationId?: string;
  /** A device action that changes no state. See `ACTION_QUEUE`. */
  action?: string;
  status?: string;
  /** Named so the refusal below can be about a field rather than a stranger. */
  phase?: string;
  event?: string;
  /**
   * A Retry rather than a first Apply: the device starts an application parked
   * in Needs you only for an order carrying this. See `orderFlags`.
   */
  retry?: boolean;
  /** The Found path: a posting the reader decided about. */
  postingId?: string;
  decision?: string;
};

/**
 * A legacy application id, checked at the route before anything reads it.
 *
 * **This used to live only inside `order()`, and six accepted values never
 * reach `order()`.** `STATUSES` held five names and `STATUS_QUEUE` three;
 * `EVENTS` held four and `EVENT_QUEUE` one; `phase` had no map at all. A body
 * naming one of the six skipped the queue, skipped the validation inside it,
 * and went straight to the upsert, where any string at all became a
 * `legacy_id` and a row. A peer session proved it on production with
 * `{applicationId: "!!probe-not-a-real-id!!", status: "review"}`: 200, one
 * `applications` row, one `idempotency_keys` row. The row is gone.
 *
 * It is the same character class `order()` enforces, hoisted, because a check
 * that only runs on some paths is a check whose coverage is an accident of
 * which map happens to have an entry.
 */
const APPLICATION_ID = /^[A-Za-z0-9._-]{1,128}$/;

/**
 * What a client may set: **the keys of the queue maps, never a list beside
 * them.**
 *
 * A status or an event the daemon has no queue for is not a thing this board
 * can order, and accepting it wrote a row for work nobody was going to do --
 * which is the exact failure `order()` refuses an unconfigured queue to
 * prevent, arriving by the one door that never opens it. Deriving the set
 * means a queue added tomorrow is accepted without anybody adding it twice,
 * and a queue removed stops being accepted the same day. Same move as parity
 * deriving its columns from the mirror's own INSERT.
 *
 * Measured 2026-09-09 across v2's whole surface: the only values any control
 * sends are `inflight`, `dropped`, `watch`, the `focus` action and the
 * `completed` event. All five have queues, so this refuses nothing a reader
 * can click.
 *
 * `phase` is refused outright and has no map on purpose. It is Class B, the
 * device owns it, and the board writing it is the `coalesce(excluded.phase,
 * ...)` fault CLAUDE.md records one layer down: a NULL meant "keep whatever
 * you had", so the branch could only ever be wrong.
 */
const STATUSES = new Set(Object.keys(STATUS_QUEUE));
const EVENTS = new Set(Object.keys(EVENT_QUEUE));

/**
 * What a Found card can be decided: the state to store, and the lane the work
 * goes to.
 *
 * **`found` is not among them and cannot be.** Found is the absence of a
 * decision row, so there is no state to write. `scripts/v2_lanes.py` says the
 * same of the lane and migration 0002's check constraint refuses the value.
 *
 * **The three states nothing writes are still not written here.** `queued`,
 * `applying` and `needs_you` are legal in the constraint and are stages of
 * work rather than opinions about a posting, and `v2_lanes.py` says why
 * nothing should start writing them: it would be a second copy of something
 * `applications` already holds correctly, on a parity gate path. So Apply
 * records the opinion `watching` and puts the *work* in `applications` as
 * `inflight`, which is the split the schema already assumes.
 *
 * `pass` writes the decision and no application: never offer this again is not
 * the start of work on it.
 */
const DECISIONS: Record<string, { state: string; status: string | null }> = {
  watch: { state: "watching", status: "watch" },
  apply: { state: "watching", status: "inflight" },
  pass:  { state: "dropped",  status: null },
};

export async function POST(request: NextRequest) {
  let account: string;
  try {
    account = await accountId();
    // First, before the body is even read: the credential can write, so this
    // is the only thing standing between a stray request and the user's store.
    await requireWritesEnabled(account);
  } catch (err) {
    if (err instanceof WriteRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }

  const key = request.headers.get("idempotency-key");
  if (!key) {
    return NextResponse.json(
      { error: "an Idempotency-Key header is required on every mutation" },
      { status: 400 });
  }

  let body: Body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "body is not JSON" }, { status: 400 });
  }

  try {
    const already = await replay(account, key, body);
    if (already) return NextResponse.json(already.body, { status: already.status });
  } catch (err) {
    if (err instanceof WriteRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }

  if (body.postingId) return decide(account, key, body);

  // ---- an action, which orders work and writes nothing --------------------
  //
  // **No row, on purpose.** "Open & finish" asks the device to put a window on
  // the user's screen; nothing about the application changed, so there is
  // nothing to record. A route that wrote a row here would be asserting a
  // state transition that did not happen, which is what `status: "inflight"`
  // was doing until #206.
  if (body.action) {
    const legacy = (body.applicationId ?? "").trim();
    if (!legacy) {
      return NextResponse.json(
        { error: "an action needs an applicationId" }, { status: 400 });
    }
    if (!APPLICATION_ID.test(legacy)) {
      return NextResponse.json(
        { error: "applicationId is not a usable application id" },
        { status: 400 });
    }
    const queue = ACTION_QUEUE[body.action];
    if (!queue) {
      return NextResponse.json(
        { error: `unknown action ${body.action}`,
          actions: Object.keys(ACTION_QUEUE) }, { status: 400 });
    }
    try {
      await order(queue, legacy);
    } catch (err) {
      if (err instanceof QueueRefused) {
        return NextResponse.json({ error: err.message }, { status: err.status });
      }
      throw err;
    }
    // 202: the device has been asked and has not acted yet. A 200 would claim
    // the window is up.
    const answer = { applicationId: legacy, action: body.action, queue,
                     state: "sent" };
    await remember(account, key, body, 202, answer);
    return NextResponse.json(answer, { status: 202 });
  }

  const legacyId = (body.applicationId ?? "").trim();
  if (!legacyId) {
    return NextResponse.json(
      { error: "applicationId or postingId is required" }, { status: 400 });
  }
  // Before the status, before the event, before anything that could write.
  if (!APPLICATION_ID.test(legacyId)) {
    return NextResponse.json(
      { error: "applicationId is not a usable application id" },
      { status: 400 });
  }
  // The device owns `phase`. A board that sets it is asserting a stage of work
  // it cannot observe, and there is no queue that would make it true.
  if (body.phase !== undefined) {
    return NextResponse.json(
      { error: "phase is written by the device, not by the board" },
      { status: 400 });
  }
  if (body.status && !STATUSES.has(body.status)) {
    return NextResponse.json(
      { error: `unknown status ${body.status}`, statuses: [...STATUSES] },
      { status: 400 });
  }
  if (body.event && !EVENTS.has(body.event)) {
    return NextResponse.json(
      { error: `unknown event ${body.event}`, events: [...EVENTS] },
      { status: 400 });
  }
  if (!body.status && !body.event) {
    return NextResponse.json(
      { error: "a status or an event is required" }, { status: 400 });
  }

  // ---- the queue, then the commit ---------------------------------------
  // Only a drop is a work order here. The other statuses are stages the daemon
  // moves an application through, and a board observes those rather than
  // ordering them; see STATUS_QUEUE.
  if (body.status && STATUS_QUEUE[body.status]) {
    try {
      await order(STATUS_QUEUE[body.status], legacyId, orderFlags(body));
    } catch (err) {
      if (err instanceof QueueRefused) {
        return NextResponse.json({ error: err.message }, { status: err.status });
      }
      throw err;
    }
  }
  // An event is a claim about what happened, and the device is where that
  // claim has to land: `history.jsonl` is what the duplicate guard, the board
  // sync and the artifact push all read. See `EVENT_QUEUE`.
  if (body.event && EVENT_QUEUE[body.event]) {
    try {
      await order(EVENT_QUEUE[body.event], legacyId);
    } catch (err) {
      if (err instanceof QueueRefused) {
        return NextResponse.json({ error: err.message }, { status: err.status });
      }
      throw err;
    }
  }

  // **Which queue this click went to, so parity can tell "the device has not
  // answered yet" from "the device never answered".** Not a "v2 wrote this"
  // flag: that would be permanent, and the one v2 finding that exists today is
  // a row nothing ever acted on. Scale Media 49dac26ae2c5c6ef holds `inflight`
  // against a session file that has said `watch` since 2026-09-05, because v2
  // wrote it two hours before #206 added the enqueue. A provenance flag would
  // have hidden exactly that. `class_b` clears these when the device answers
  // and `verify_parity.AWAITING_SECONDS` bounds how long they may stand.
  //
  // Read off the maps that were just used, never restated: a second copy of
  // STATUS_QUEUE is the drift GATE_PATHS has already paid for.
  const ordered = (body.status ? STATUS_QUEUE[body.status] : null)
    ?? (body.event ? EVENT_QUEUE[body.event] : null)
    ?? null;

  const client = await pool().connect();
  let applicationId: string;
  try {
    await client.query("begin");
    await client.query("select set_config('app.account_id', $1, true)", [account]);
    const { rows } = await client.query(
      `insert into applications
         (account_id, legacy_id, status, phase, pending_order,
          pending_order_at, updated_at)
       values ($1, $2, coalesce($3, 'unknown'), $4, $5,
               case when $5::text is null then null else now() end, now())
       on conflict (account_id, legacy_id) do update
         set status = coalesce($3, applications.status),
             phase = coalesce($4, applications.phase),
             -- coalesce, so a click that orders nothing does not clear an
             -- order still outstanding from an earlier one. Only the device
             -- clears it.
             pending_order = coalesce($5, applications.pending_order),
             pending_order_at = case
               when $5::text is null then applications.pending_order_at
               else now() end,
             updated_at = now()
       returning id`,
      // $4 is always null: `phase` is refused above. The `coalesce` stays so
      // the upsert preserves whatever the device last wrote rather than
      // blanking it, which is what a bare `excluded.phase` would do.
      [account, legacyId, body.status ?? null, null, ordered]);
    applicationId = rows[0].id;
    if (body.event) {
      await client.query(
        `insert into application_events (account_id, application_id, kind, at)
         values ($1, $2, $3, now())`,
        [account, applicationId, body.event]);
    }
    await client.query("commit");
  } catch (err) {
    await client.query("rollback").catch(() => {});
    throw err;
  } finally {
    client.release();
  }

  // ---- then the publish -------------------------------------------------
  // `needs_you` and `phase` are gone from here because they are gone from the
  // route: neither has a queue, both are refused above, and a branch for a
  // value that can no longer arrive is a sentence about a mechanism that stops
  // the next reader checking.
  const kind = body.event === "completed" ? "application.completed"
    : "application.changed";
  const published = await publish(account, kind, {
    application_id: applicationId,
    ...(body.status ? { to: body.status } : {}),
  });

  const answer = { applicationId, kind, published };
  await remember(account, key, body, 200, answer);
  return NextResponse.json(answer);
}

/** A uuid, checked before it reaches a cast that would 500 on it. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The Found path: a reader decided about a posting.
 *
 * **The decision row and the application are one transaction.** A decision
 * without an application takes the card out of Found and puts it nowhere,
 * which is a role that has left the board entirely; an application without a
 * decision leaves the same card sitting in Found under a role already being
 * worked. Either half alone is worse than neither.
 *
 * Two events come out of it, not one. Found's membership is the decision and
 * the lane's is the application; they are different objects, and an event that
 * tried to be both would need a `from: "found"` the design refuses.
 */
async function decide(account: string, key: string, body: Body) {
  const postingId = (body.postingId ?? "").trim();
  if (!UUID.test(postingId)) {
    return NextResponse.json({ error: "postingId must be a uuid" }, { status: 400 });
  }
  const decision = body.decision ?? "";
  if (!(decision in DECISIONS)) {
    return NextResponse.json(
      { error: `unknown decision ${decision}`, decisions: Object.keys(DECISIONS) },
      { status: 400 });
  }
  const { state, status } = DECISIONS[decision];

  const client = await pool().connect();
  let applicationId: string | null = null;
  try {
    // Read outside the transaction, because the enqueue below needs the legacy
    // id and a transaction held open across a call to Cloudflare is a row lock
    // held for a network round trip.
    const posting = await client.query(
      "select legacy_id from postings where id = $1", [postingId]);
    if (!posting.rows.length) {
      return NextResponse.json({ error: "no such posting" }, { status: 404 });
    }

    // **Before the commit, because the enqueue is the work.** A row recorded
    // for a click the daemon never hears about is a board that says it did
    // something and a machine that never heard, which is the worst failure
    // this project has. Failing here writes nothing and the reader retries.
    // See `_ui/queue.ts` for why this ordering is the inverse of `publish`'s.
    await order(DECISION_QUEUE[decision], posting.rows[0].legacy_id);

    await client.query("begin");
    await client.query("select set_config('app.account_id', $1, true)", [account]);
    await client.query(
      `insert into job_decisions
         (account_id, posting_id, state, decided_at, decided_from)
       values ($1, $2, $3, now(), 'v2')
       on conflict (account_id, posting_id) do update
         set state = $3, decided_at = now(), decided_from = 'v2'`,
      [account, postingId, state]);
    if (status) {
      // `legacy_id` carries the posting's, so the device store and this row
      // name the same job when Phase 3 reconciles them. Inventing an id here
      // would make that reconciliation a guess.
      const { rows } = await client.query(
        `insert into applications
           (account_id, legacy_id, status, posting_id, pending_order,
            pending_order_at, created_at, updated_at)
         values ($1, $2, $3, $4, $5, now(), now(), now())
         on conflict (account_id, legacy_id) do update
           set status = $3, posting_id = $4, pending_order = $5,
               pending_order_at = now(), updated_at = now()
         returning id`,
        [account, posting.rows[0].legacy_id, status, postingId,
         DECISION_QUEUE[decision]]);
      applicationId = rows[0].id;
    }
    await client.query("commit");
  } catch (err) {
    await client.query("rollback").catch(() => {});
    // A refused enqueue is a 502 or a 503 that says why, not a 500. It fires
    // before `begin`, so this rollback is a no-op on that path; it is left
    // unconditional rather than made clever, because a rollback skipped by a
    // mistaken branch is a transaction left open on a pooled connection.
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  } finally {
    client.release();
  }

  const published = await publish(account, "posting.decided",
    { posting_id: postingId, decision, state });
  if (applicationId) {
    await publish(account, "application.changed",
      { application_id: applicationId, to: status });
  }

  const answer = { postingId, applicationId, decision, state, published };
  await remember(account, key, body, 200, answer);
  return NextResponse.json(answer);
}
