import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { pendingSearch, QueueRefused, saveSearch } from "../../_ui/queue";
import { savedSearchState } from "../../_ui/db";

/**
 * Save search.
 *
 * **Validated on the machine that owns the schema, not here.** This parks the
 * config in the queue's own field and `scripts/search_config.py` refuses
 * anything it cannot accept. A board that could write an invalid config
 * straight into the store would be a board that can break every later scan,
 * which is the old endpoint's reasoning and it still holds.
 *
 * The button was a raw `<button type="button">` with `disabled={!writesEnabled}`
 * and no handler, so with writes on it rendered fully pressable and posted
 * nothing. That is the state a person only finds by pressing it.
 */
export const dynamic = "force-dynamic";

/**
 * The prefix `watch.take_saved_config` writes into `meta.blocked` when the
 * schema refuses a parked save. `tests/save_search_sends_what_the_form_holds.test.mjs`
 * reads it back out of `watch.py`, so a reworded refusal fails there rather
 * than turning a refused save into one that waits for ever.
 */
const REFUSED_PREFIX = "The saved search was not accepted";

/**
 * Did the last save land? **GET only reads**, like every GET on this board.
 *
 *   pending    the config is still parked in the queue for the device
 *   version    `board_meta.search_config.version`, which the device bumps on
 *              every save it accepts; the button compares it with the one it
 *              read before posting
 *   refused    the device's own sentence, when the schema refused the save
 *
 * The board's endpoint returned the parked config itself. This returns only
 * that one is parked and when, because the page needs the fact and not the
 * search, and a public route has no reason to print it.
 */
export async function GET() {
  let pending: { at: string | null } | null;
  try {
    pending = await pendingSearch();
  } catch (err) {
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }
  const { version, blocked } = await savedSearchState();
  return NextResponse.json({
    ok: true,
    pending: pending !== null,
    pendingAt: pending?.at ?? null,
    version,
    refused: blocked && blocked.startsWith(REFUSED_PREFIX) ? blocked : null,
  });
}

export async function POST(request: NextRequest) {
  let account: string;
  try {
    account = await accountId();
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

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "body is not JSON" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json(
      { error: "the search config must be a JSON object" }, { status: 400 });
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

  try {
    await saveSearch(body as Record<string, unknown>);
  } catch (err) {
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }
  const answer = { saved: true, state: "sent" };
  await remember(account, key, body, 202, answer);
  return NextResponse.json(answer, { status: 202 });
}
