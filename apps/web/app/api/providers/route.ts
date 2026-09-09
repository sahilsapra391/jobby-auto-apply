import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { PROVIDER_QUEUE, QueueRefused, order } from "../../_ui/queue";
import { PROVIDER_LABELS } from "../../providers.generated";

/**
 * Connect, Disconnect and Refresh, ordered on the device.
 *
 * **The connection stays on the device and the button still has to reach it.**
 * `settings/page.tsx` rendered `Connect` as a raw `<button type="button">` with
 * `data-provider` and `data-action` attributes that nothing read, and its own
 * docstring said "the connection itself stays on the device -- nothing here
 * connects anything". That sentence is true about where a credential lives and
 * it is not a reason for the control to send nothing: `watch.py` has claimed
 * `providerLocal/` throughout, and the old board's `deploy/api/board.js` has
 * been enqueueing to it since before v2 existed.
 *
 * Fifth control of that shape, after Confirm submitted, Open & finish, Undrop
 * and Apply-from-Watching. Every one rendered correctly and reached nothing.
 *
 * **This route writes no row, on purpose.** A provider connection is device
 * state that the class B mirror reflects back through `provider_links` once
 * `providers.json` changes; a row written here would be v2 asserting a
 * connection the device has not made. 202 rather than 200 for the same reason
 * the action path returns it: the device has been asked and has not acted yet.
 */
export const dynamic = "force-dynamic";

/** What the daemon accepts. `watch.PROVIDER_ACTIONS` is the other half. */
const ACTIONS = new Set(["connect", "disconnect", "refresh"]);

type Body = { provider?: string; action?: string };

/**
 * A sortable key, so the first successful Connect wins.
 *
 * `providers.begin_connect` stores it and later writes are ordered against it,
 * which is what stops a second click on a slow OAuth flow displacing the
 * connection the first one is still completing. The shape is the old board's:
 * a leading `1-`, a zero-padded millisecond clock so string order is time
 * order, and a uuid so two clicks in one millisecond still differ.
 */
function connectionKey() {
  return `1-${String(Date.now()).padStart(13, "0")}-${crypto.randomUUID()}`;
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

  const provider = (body.provider ?? "").trim();
  const action = (body.action ?? "").trim();
  // The provider list comes from `settings_panel.PROVIDERS` through the
  // generated module, never restated here: a second copy of it is how a
  // provider added on the device becomes one the board refuses.
  if (!(provider in PROVIDER_LABELS)) {
    return NextResponse.json(
      { error: `unknown provider ${provider}`,
        providers: Object.keys(PROVIDER_LABELS) }, { status: 400 });
  }
  if (!ACTIONS.has(action)) {
    return NextResponse.json(
      { error: `unknown provider action ${action}`, actions: [...ACTIONS] },
      { status: 400 });
  }

  // Enqueue first and write nothing. There is no row to lose if this fails,
  // and a click recorded that the daemon never hears is the worst failure this
  // project has; see `_ui/queue.ts`.
  try {
    await order(PROVIDER_QUEUE, `${action}-${provider}`, {
      provider,
      action,
      ...(action === "connect" ? { connectionKey: connectionKey() } : {}),
    });
  } catch (err) {
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }

  const answer = { provider, action, queue: PROVIDER_QUEUE, state: "sent" };
  await remember(account, key, body, 202, answer);
  return NextResponse.json(answer, { status: 202 });
}
