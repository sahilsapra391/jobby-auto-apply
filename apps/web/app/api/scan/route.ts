import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { QueueRefused, order } from "../../_ui/queue";

/**
 * Scan now, and Add your own link, which rides the same queue.
 *
 * **Both rendered and reached nothing.** `FoundSurface.tsx` drew them as raw
 * `<button type="button">` with no handler, and v2 had no scan route: the old
 * board's `found_lane.SCRIPT` posted `/api/scan` and `deploy/api/board.js`
 * enqueued it, and neither half came across. CLAUDE.md says Scan now is the
 * only scan action in the UI, so the lane's one deliberate way to ask for work
 * was inert.
 *
 * The link rides this queue rather than getting an endpoint of its own,
 * matching the old board exactly. Only http(s) is accepted, because the value
 * reaches an agent's prompt and an href and neither should take a
 * `javascript:`.
 *
 * The id is a timestamp when none is given, which is what makes two Scan now
 * presses two requests rather than one idempotent one -- `enqueue` is keyed by
 * id, and the daemon runs one scan at a time behind `lock_holder("scan")`
 * anyway.
 */
export const dynamic = "force-dynamic";

const SCAN_QUEUE = "scan";

type Body = { url?: string };

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

  const extra: Record<string, unknown> = {};
  const link = (body.url ?? "").trim();
  if (link) {
    if (!/^https?:\/\//i.test(link)) {
      return NextResponse.json(
        { error: "the link must be http or https" }, { status: 400 });
    }
    if (link.length > 2000) {
      return NextResponse.json({ error: "that link is too long" }, { status: 400 });
    }
    extra.url = link;
  }

  const id = new Date().toISOString().replace(/[:.]/g, "-");
  try {
    await order(SCAN_QUEUE, id, extra);
  } catch (err) {
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }

  const answer = { queue: SCAN_QUEUE, id, state: "sent",
                   ...(link ? { url: link } : {}) };
  await remember(account, key, body, 202, answer);
  return NextResponse.json(answer, { status: 202 });
}
