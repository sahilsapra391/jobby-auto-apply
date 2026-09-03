import { NextRequest, NextResponse } from "next/server";
import { pool } from "../../_ui/db";
// @ts-expect-error -- generated from deploy/r2.mjs, plain JS with no types.
import { configured, fetchObject } from "../../_ui/r2.generated.mjs";

/**
 * One application artifact. **GET only, and it reads.**
 *
 * This is the capability v2 did not have, and its absence was four broken
 * columns rather than four bugs: Resume, Cover letter and Answers all 404ed
 * because `/doc/<filename>` had no route behind it, and 684 objects in R2 were
 * unreachable from this board.
 *
 * **It keys on the application and the kind, never on the filename**, and that
 * is measured rather than preferred. R2 keys are
 * `u/{userId}/app/{applicationId}/{kind}.{ext}` where the id is the store's
 * own, which the mirror writes to `applications.legacy_id`. Across the 129
 * application ids in the bucket on 2026-09-03, **129 match `legacy_id` and 0
 * match `applications.id`** -- so a route built on the uuid, which is the
 * obvious thing to reach for here, resolves nothing at all and looks exactly
 * like the bug it was meant to fix.
 *
 * The filename becomes the download name and nothing else. That is all it was
 * ever for: the board needed `jobby:doc-index` in KV to turn one into a key,
 * and Postgres has no such mapping and now needs none.
 *
 * **A credential fault is a 503 that says so, never a 404.** Answering "no such
 * document" when the truth is "this deployment cannot reach the bucket"
 * reports a document that exists as one that does not, and sends whoever reads
 * it looking for a missing file. Same distinction the board's own
 * `handleDocument` makes, and the same one `deploy_verify` makes when it
 * cannot order a timestamp: I could not look is not it is not there.
 */
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Every kind the bucket holds, with the object it names and how it is served.
 *
 * Derived from the manifest rather than guessed: 120 `resume.pdf`, 113
 * `cover.pdf`, 54 `jd.txt`, 42+34 resume HTML, 34+30 cover HTML, 25
 * `posting.txt`, 22 `answers.json`, 17 `questions.json`.
 *
 * `disposition` is the half worth stating. A PDF and an HTML page are things to
 * look at, so they render inline; a JSON record is a thing to read, and served
 * inline with the right type a browser shows it rather than downloading it.
 * Nothing here is `attachment`, because every one of these is the reader's own
 * document and a download prompt is friction on a thing they are checking
 * before they apply to something twice.
 */
const KINDS: Record<string, { object: string; type: string }> = {
  resume: { object: "resume.pdf", type: "application/pdf" },
  cover: { object: "cover.pdf", type: "application/pdf" },
  answers: { object: "answers.json", type: "application/json" },
  questions: { object: "questions.json", type: "application/json" },
  jd: { object: "jd.txt", type: "text/plain; charset=utf-8" },
  posting: { object: "posting.txt", type: "text/plain; charset=utf-8" },
  "resume-html": { object: "resume.html", type: "text/html; charset=utf-8" },
  "cover-html": { object: "cover.html", type: "text/html; charset=utf-8" },
};

const USER_ID = process.env.JOBBY_USER_ID || "account-1";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const app = params.get("app") ?? "";
  const kind = params.get("kind") ?? "";

  if (!UUID.test(app)) {
    return NextResponse.json({ error: "app must be an application uuid" },
                             { status: 400 });
  }
  const spec = KINDS[kind];
  if (!spec) {
    // Named rather than defaulted, like the lane route's unknown sort: a typo
    // quietly serving the resume is how a reader concludes the cover letter is
    // missing.
    return NextResponse.json(
      { error: `unknown kind ${kind}`, kinds: Object.keys(KINDS) },
      { status: 400 });
  }

  const found = await pool().query(
    "select legacy_id, company from applications where id = $1", [app]);
  if (found.rowCount === 0) {
    return NextResponse.json({ error: "no such application" }, { status: 404 });
  }
  const legacy = found.rows[0].legacy_id as string | null;
  if (!legacy) {
    // 100% populated today, and this says which of the two is wrong rather
    // than reporting a missing file.
    return NextResponse.json(
      { error: "this application has no legacy id, so its artifacts cannot be "
               + "addressed" }, { status: 404 });
  }

  if (!configured(process.env)) {
    return NextResponse.json(
      { error: "artifact storage is not configured for this deployment; "
               + "R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID and "
               + "R2_SECRET_ACCESS_KEY must be set" },
      { status: 503 });
  }

  const key = `u/${USER_ID}/app/${legacy}/${spec.object}`;
  // Range is forwarded so a phone's PDF viewer can page a large file, which is
  // the reason the board's own proxy forwards it.
  const range = request.headers.get("range");
  let upstream: Response;
  try {
    upstream = await fetchObject(process.env, key, range);
  } catch (err) {
    // Never silent, and never a 404: a fetch that threw is a fault here, not a
    // document that does not exist.
    return NextResponse.json(
      { error: "artifact storage could not be reached",
        detail: String((err as Error)?.message ?? err).slice(0, 200) },
      { status: 502 });
  }

  if (upstream.status === 404) {
    return NextResponse.json(
      { error: "no such artifact", key }, { status: 404 });
  }
  if (!upstream.ok && upstream.status !== 206) {
    return NextResponse.json(
      { error: `artifact storage answered ${upstream.status}` },
      { status: 502 });
  }

  const headers = new Headers();
  headers.set("content-type", spec.type);
  // The recorded filename would be nicer here and is deliberately not used:
  // 32 of 107 cover-letter values are a sentence rather than a name, and
  // three are a name with a sentence glued to it. The company and kind are
  // always true.
  const company = String(found.rows[0].company ?? "application")
    .replace(/[^A-Za-z0-9._ -]/g, "").slice(0, 60) || "application";
  headers.set("content-disposition",
              `inline; filename="${company} ${spec.object}"`);
  headers.set("cache-control", "private, max-age=0, must-revalidate");
  for (const pass of ["content-length", "content-range", "accept-ranges",
                      "etag", "last-modified"]) {
    const value = upstream.headers.get(pass);
    if (value) headers.set(pass, value);
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}
