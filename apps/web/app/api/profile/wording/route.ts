import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../../_ui/writes";
import { QueueRefused, order } from "../../../_ui/queue";
import { config, isOwner } from "../../../_ui/owner";
import { readDeviceJson } from "../../../_ui/deviceKv";
// @ts-expect-error -- generated from deploy/profile_edits.mjs, plain JS.
import { planResumeEdit } from "../../../_ui/profileEdits.generated.mjs";
// @ts-expect-error -- generated from deploy/device_keys.mjs, plain JS.
import { OWNER_WORDING_KEY } from "../../../_ui/deviceKeys.generated.mjs";

/**
 * The resume wording, for its owner and nobody else.
 *
 * **The only owner-gated route on the site.** Everything else is public by
 * the owner's decision (`middleware.ts`). This one is not, because the wording
 * is his employment history in his own words: `class_b._PROFILE_EXCLUDED`
 * keeps it out of Postgres, the public Profile page never draws it, and the
 * public outcomes list drops it (`db.editOutcomes`).
 *
 * **Where the wording comes from.** The device publishes it, with the
 * rendered preview the editor draws, to `jobby:owner:resume-wording`
 * (`scripts/wording_publish.py`, called by the watcher whenever the wording,
 * the template or the profile behind them changes). That key is a projection
 * of `resume-content.json` stamped with the digest it was built from; the file
 * on the device always wins, and a save here is an edit on the queue rather
 * than a write to that key, so the device stays the only writer.
 *
 * GET answers 401 to anyone not signed in and 503 when the owner has not set
 * the two environment variables, before KV is touched, so the gate is not a
 * timing oracle for whether a wording exists.
 *
 * POST is the retired board's `/api/resume` with the owner check in front:
 * a `content` edit on `resumeEdits`, planned by the shared `planResumeEdit`,
 * enqueued, and decided by the device (`store.py resume-content-put`, which
 * refuses a list whose length differs from the template's). Its outcome
 * arrives in `edit_outcomes` like every other Profile edit, with the
 * submitted wording left out.
 */
export const dynamic = "force-dynamic";

const PRIVATE = { "cache-control": "private, no-store" };

/** The gate, in the order that leaks least. */
async function refuseUnlessOwner(): Promise<NextResponse | null> {
  const cfg = config();
  if (!cfg.ok) {
    return NextResponse.json({ error: cfg.why, signIn: false },
                             { status: 503, headers: PRIVATE });
  }
  if (!(await isOwner())) {
    return NextResponse.json(
      { error: "Sign in as the owner to edit the resume wording.", signIn: true },
      { status: 401, headers: PRIVATE });
  }
  return null;
}

type Published = {
  schemaVersion?: number;
  publishedAt?: string;
  digest?: string;
  content?: Record<string, unknown>;
  render?: string;
};

export async function GET() {
  const refused = await refuseUnlessOwner();
  if (refused) return refused;

  const read = await readDeviceJson<Published>(OWNER_WORDING_KEY);
  if (read.state === "unreadable") {
    return NextResponse.json({ error: `The wording could not be read: ${read.why}` },
                             { status: 503, headers: PRIVATE });
  }
  if (read.state === "absent") {
    // Not "no wording": the device has not published it yet. The watcher does
    // on its first tick after this change reaches it.
    return NextResponse.json(
      { error: "Your device has not published your resume wording yet. It does "
             + "so on the watcher's first tick after this version reaches it." },
      { status: 404, headers: PRIVATE });
  }
  const value = read.value ?? {};
  if (!value.content || typeof value.content !== "object") {
    return NextResponse.json(
      { error: "The published wording is not in a shape this editor reads." },
      { status: 502, headers: PRIVATE });
  }
  return NextResponse.json({
    content: value.content,
    render: typeof value.render === "string" ? value.render : "",
    publishedAt: value.publishedAt ?? null,
    digest: value.digest ?? null,
  }, { headers: PRIVATE });
}

export async function POST(request: NextRequest) {
  const refused = await refuseUnlessOwner();
  if (refused) return refused;

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

  let body: Record<string, unknown>;
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

  // This route carries one kind of edit. Anything else on `resumeEdits` goes
  // through `/api/profile`, which is public-with-writes rather than owner-only.
  if (body.action !== "content") {
    return NextResponse.json(
      { error: "this route saves the resume wording and nothing else" },
      { status: 400 });
  }
  const planned = planResumeEdit(body) as {
    error?: string; status?: number; queue?: string; editId?: string;
    extra?: Record<string, unknown>;
  };
  if (planned.error) {
    return NextResponse.json({ error: planned.error }, { status: planned.status ?? 400 });
  }

  try {
    await order(planned.queue!, planned.editId!, planned.extra);
  } catch (err) {
    if (err instanceof QueueRefused) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    throw err;
  }

  const answer = { editId: planned.editId, queue: planned.queue,
                   action: "content", state: "sent" };
  await remember(account, key, body, 202, answer);
  return NextResponse.json(answer, { status: 202 });
}
