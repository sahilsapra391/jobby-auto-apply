import { NextRequest, NextResponse } from "next/server";
import {
  accountId, remember, replay, requireWritesEnabled, WriteRefused,
} from "../../_ui/writes";
// @ts-expect-error -- generated from deploy/profile_edits.mjs, plain JS.
import { planResumeUpload } from "../../_ui/profileEdits.generated.mjs";
// @ts-expect-error -- generated from deploy/kv.mjs, plain JS.
import { kvPut, configured } from "../../_ui/kv.generated.mjs";
// @ts-expect-error -- generated from deploy/device_keys.mjs, plain JS.
import { UPLOAD_KEY_PREFIX } from "../../_ui/deviceKeys.generated.mjs";

/**
 * A base resume PDF, parked for the device.
 *
 * The retired board's `/api/resume-upload`, ported rather than redesigned,
 * because the device half already exists and has been claiming it all along:
 * the Profile page uploads here first, then sends the `add` resume edit
 * through `/api/profile` carrying only this upload's id, and
 * `watch._resume_command` calls `install_uploaded_resume`, which runs
 * `queue.mjs upload-take` to read and delete `jobby:upload:<id>`.
 *
 * **Its own KV key, never the queue.** `jobby:queue` is read whole on every
 * watcher poll and read-modify-written by every click; an 87 KB resume is
 * 116 KB of base64 in each of those reads until it is claimed.
 *
 * **Behind `live_board` like every other v2 write**, and the cross-site guard
 * in `middleware.ts` before that. The checks on the file itself are
 * `planResumeUpload`'s, shared with the old board through
 * `deploy/profile_edits.mjs`, and the device checks size and magic again when
 * it takes the key.
 *
 * It writes no row. A base resume belongs to the device's registry and reaches
 * `profiles` through the class B mirror once the device has installed it.
 */
export const dynamic = "force-dynamic";

function env() {
  return {
    CF_ACCOUNT_ID: process.env.CF_ACCOUNT_ID,
    CF_KV_NAMESPACE_ID: process.env.CF_KV_NAMESPACE_ID,
    CF_API_TOKEN: process.env.CF_API_TOKEN,
  };
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

  const planned = planResumeUpload(body) as {
    error?: string; status?: number; uploadId?: string; encoded?: string; bytes?: number;
  };
  if (planned.error) {
    return NextResponse.json({ error: planned.error }, { status: planned.status ?? 400 });
  }

  const e = env();
  if (!configured(e)) {
    const missing = Object.entries(e).filter(([, v]) => !v).map(([k]) => k);
    return NextResponse.json(
      { error: `KV is not configured on this deployment, so the document `
             + `would reach nowhere the device reads. Set ${missing.join(", ")}.` },
      { status: 503 });
  }
  try {
    await kvPut(e, `${UPLOAD_KEY_PREFIX}${planned.uploadId}`, planned.encoded);
  } catch (err) {
    return NextResponse.json(
      { error: `the document could not be stored for the device: `
             + `${err instanceof Error ? err.message : String(err)}` },
      { status: 502 });
  }

  // 202: parked, not installed. The base it belongs to does not exist until
  // the device takes this key and `store.py resume-add` accepts it.
  const answer = { uploadId: planned.uploadId, bytes: planned.bytes, state: "parked" };
  await remember(account, key, body, 202, answer);
  return NextResponse.json(answer, { status: 202 });
}
