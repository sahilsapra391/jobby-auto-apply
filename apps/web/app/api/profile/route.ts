import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
import { editOutcomes } from "../../_ui/db";
import { QueueRefused, order } from "../../_ui/queue";
// @ts-expect-error -- generated from deploy/profile_edits.mjs, plain JS with
// no types. Same idiom as kvqueue.generated.mjs and r2.generated.mjs.
import { planAnswerEdit, planResumeEdit } from "../../_ui/profileEdits.generated.mjs";

/**
 * Profile edits: the Memory Bank and the base resumes.
 *
 * **Enqueue-only. Nothing here writes the subject of the edit.** An answer
 * lives in `answers.json` on the device and reaches `answer_bank` through the
 * Class B mirror, and the resume registry reaches `profiles` the same way, so
 * the device is the only writer of both. A route that also wrote them would
 * make two writers for one row, which is the state parity exists to detect and
 * would then be unable to: a divergence it caused itself.
 *
 * So this is the whole write: plan it, put it on the queue, remember the
 * key. What happened to it arrives later in `edit_outcomes`, written by the
 * device when `store.py` decides, and the board reads that rather than
 * assuming.
 *
 * **No optimistic row and no publish.** There is nothing to publish: the change
 * has not happened yet, and an event saying it had would be the board claiming
 * a state the store does not hold. The client draws its own "sent" for the
 * seconds until `pending` appears, and a "sent" that never becomes "pending"
 * is itself the signal that nothing heard.
 */
export const dynamic = "force-dynamic";

/** Which planner owns which kind of edit. Named, never taken from the body. */
const PLANNERS: Record<string, (body: unknown) => {
  error?: string; status?: number; queue?: string; editId?: string;
  extra?: Record<string, unknown>;
}> = {
  answer: planAnswerEdit,
  resume: planResumeEdit,
};

/**
 * What the board has heard about recent edits.
 *
 * Polled by the Profile page while it has an edit in flight, and only then.
 * The device writes `edit_outcomes` through the Class B mirror, which is not
 * on the channel, so there is no event to wait for; a poll that stops when
 * everything has settled is the honest shape rather than a socket that would
 * never fire.
 */
export async function GET() {
  try {
    await accountId();
  } catch (err) {
    if (err instanceof WriteRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }
  return NextResponse.json({ outcomes: await editOutcomes() });
}

export async function POST(request: NextRequest) {
  let account: string;
  try {
    account = await accountId();
    // Before the body is read. The credential can write, and this flag is the
    // only thing between a stray request and the user's store.
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

  let body: { kind?: string } & Record<string, unknown>;
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

  const plan = PLANNERS[String(body.kind ?? "")];
  if (!plan) {
    return NextResponse.json(
      { error: `kind must be one of ${Object.keys(PLANNERS).join(", ")}` },
      { status: 400 });
  }
  const planned = plan(body);
  if (planned.error) {
    return NextResponse.json({ error: planned.error },
                             { status: planned.status ?? 400 });
  }

  try {
    await order(planned.queue!, planned.editId!, planned.extra);
  } catch (err) {
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }

  // 202, because the queue has it and the device has not decided. A 200 here
  // would be this route claiming an outcome it does not have and cannot know:
  // `store.py` genuinely refuses edits, and `edit_outcomes` is where that
  // lands.
  const answer = { editId: planned.editId, queue: planned.queue,
                   action: planned.extra?.action ?? null, state: "sent" };
  await remember(account, key, body, 202, answer);
  return NextResponse.json(answer, { status: 202 });
}
