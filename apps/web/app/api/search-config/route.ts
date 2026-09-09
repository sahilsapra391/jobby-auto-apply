import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { QueueRefused, saveSearch } from "../../_ui/queue";

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
