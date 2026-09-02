import { NextRequest, NextResponse } from "next/server";
import {
  accountId, publish, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { pool } from "../../_ui/db";

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
  status?: string;
  phase?: string;
  event?: string;
};

/** What a client may set, named rather than passed through. */
const STATUSES = new Set(["watch", "review", "active", "inflight", "dropped"]);
const EVENTS = new Set(["submitting", "completed", "abandoned", "needs_you"]);

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

  const legacyId = (body.applicationId ?? "").trim();
  if (!legacyId) {
    return NextResponse.json(
      { error: "applicationId is required" }, { status: 400 });
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

  // ---- the commit -------------------------------------------------------
  const client = await pool().connect();
  let applicationId: string;
  try {
    await client.query("begin");
    await client.query("select set_config('app.account_id', $1, true)", [account]);
    const { rows } = await client.query(
      `insert into applications (account_id, legacy_id, status, phase, updated_at)
       values ($1, $2, coalesce($3, 'unknown'), $4, now())
       on conflict (account_id, legacy_id) do update
         set status = coalesce($3, applications.status),
             phase = coalesce($4, applications.phase),
             updated_at = now()
       returning id`,
      [account, legacyId, body.status ?? null, body.phase ?? null]);
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
  const kind = body.event === "completed" ? "application.completed"
    : body.event === "needs_you" ? "application.needs_you"
    : body.phase ? "application.phase"
    : "application.changed";
  const published = await publish(account, kind, {
    application_id: applicationId,
    ...(body.status ? { to: body.status } : {}),
    ...(body.phase ? { phase: body.phase } : {}),
  });

  const answer = { applicationId, kind, published };
  await remember(account, key, body, 200, answer);
  return NextResponse.json(answer);
}
