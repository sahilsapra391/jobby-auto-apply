import { NextRequest, NextResponse } from "next/server";
import { pool } from "../../_ui/db";
// @ts-expect-error -- generated from deploy/r2.mjs, plain JS with no types.
import { configured, fetchObject } from "../../_ui/r2.generated.mjs";
// @ts-expect-error -- plain JS with no types, so node can test it directly.
import { basename, readByName } from "../../_ui/documents.mjs";

/**
 * One application artifact. **GET only, and it reads.**
 *
 * This is the capability v2 did not have, and its absence was four broken
 * columns rather than four bugs: Resume, Cover letter and Answers all 404ed
 * because `/doc/<filename>` had no route behind it, and 684 objects in R2 were
 * unreachable from this board.
 *
 * **It keys on the application and the kind first**, and that is measured
 * rather than preferred. R2 keys are `u/{userId}/app/{applicationId}/{kind}.{ext}`
 * where the id is the store's own, which the mirror writes to
 * `applications.legacy_id`. Across the 129 application ids in the bucket on
 * 2026-09-03, **129 match `legacy_id` and 0 match `applications.id`** -- so a
 * route built on the uuid, which is the obvious thing to reach for here,
 * resolves nothing at all and looks exactly like the bug it was meant to fix.
 *
 * **The recorded filename is asked second, and only when the fixed key is
 * absent.** A document written before `document_contract` put the application
 * id in the name may not be at its own key at all: a filename three
 * applications recorded is stored once, under whichever one `jobby:doc-index`
 * attributed it to. v1 served those by name; v2 drew "not attributable" with
 * nothing to open. So the resume and cover-letter kinds fall back to the
 * filename this application recorded, through the same index and KV copy v1
 * read, in `_ui/documents.mjs`. `/doc/<name>` calls the same function for the
 * links a v1 board printed, so there is one serving path with two doors.
 *
 * **A credential fault is a 503 that says so, never a 404.** Answering "no such
 * document" when the truth is "this deployment cannot reach the bucket"
 * reports a document that exists as one that does not, and sends whoever reads
 * it looking for a missing file. I could not look is not it is not there.
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
 * `field` is the completed event's payload field holding the filename the
 * application recorded, for the two kinds that have one.
 *
 * Nothing here is `attachment`, because every one of these is the reader's own
 * document and a download prompt is friction on a thing they are checking
 * before they apply to something twice.
 */
const KINDS: Record<string, { object: string; type: string; field?: string }> = {
  resume: { object: "resume.pdf", type: "application/pdf", field: "resumeVariant" },
  cover: { object: "cover.pdf", type: "application/pdf", field: "coverLetter" },
  answers: { object: "answers.json", type: "application/json" },
  questions: { object: "questions.json", type: "application/json" },
  jd: { object: "jd.txt", type: "text/plain; charset=utf-8" },
  posting: { object: "posting.txt", type: "text/plain; charset=utf-8" },
  "resume-html": { object: "resume.html", type: "text/html; charset=utf-8" },
  "cover-html": { object: "cover.html", type: "text/html; charset=utf-8" },
};

const USER_ID = process.env.JOBBY_USER_ID || "account-1";

const UNCONFIGURED = "artifact storage is not configured for this deployment; "
  + "R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY must "
  + "be set";

function headersFor(type: string, filename: string, from: Headers | Record<string, string>) {
  const headers = new Headers();
  headers.set("content-type", type);
  headers.set("content-disposition",
              `inline; filename="${filename.replace(/"/g, "")}"`);
  headers.set("cache-control", "private, max-age=0, must-revalidate");
  for (const pass of ["content-length", "content-range", "accept-ranges",
                      "etag", "last-modified"]) {
    const value = from instanceof Headers ? from.get(pass) : from[pass];
    if (value) headers.set(pass, value);
  }
  return headers;
}

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

  // The recorded filename would be nicer as a download name for the fixed key
  // and is deliberately not used there: 32 of 107 cover-letter values are a
  // sentence rather than a name. The company and kind are always true.
  const company = String(found.rows[0].company ?? "application")
    .replace(/[^A-Za-z0-9._ -]/g, "").slice(0, 60) || "application";
  const key = `u/${USER_ID}/app/${legacy}/${spec.object}`;
  // Range is forwarded so a phone's PDF viewer can page a large file.
  const range = request.headers.get("range");

  // First: the fixed key. `fault` records why it did not answer when the
  // reason was anything but a plain 404.
  let fault: { status: number; error: string } | null = null;
  if (!configured(process.env)) {
    fault = { status: 503, error: UNCONFIGURED };
  } else {
    try {
      const upstream: Response = await fetchObject(process.env, key, range);
      if (upstream.ok || upstream.status === 206) {
        return new NextResponse(upstream.body, {
          status: upstream.status,
          headers: headersFor(spec.type, `${company} ${spec.object}`, upstream.headers),
        });
      }
      if (upstream.status !== 404) {
        fault = { status: 502, error: `artifact storage answered ${upstream.status}` };
      }
    } catch (err) {
      // Never silent, and never a 404: a fetch that threw is a fault here,
      // not a document that does not exist.
      fault = { status: 502,
                error: "artifact storage could not be reached: "
                  + String((err as Error)?.message ?? err).slice(0, 200) };
    }
  }

  // Second: the filename this application recorded, the way v1 served it.
  let name = "";
  if (spec.field) {
    const event = await pool().query(
      `select payload->>$2 as name from application_events
        where application_id = $1 and kind = 'completed'
        order by at desc limit 1`, [app, spec.field]);
    name = basename(event.rows[0]?.name ?? "");
  }
  if (name.toLowerCase().endsWith(".pdf")) {
    const read = await readByName(process.env, name, range);
    if (read.ok) {
      return new NextResponse(read.body, {
        status: read.status,
        headers: headersFor(spec.type, name, read.headers),
      });
    }
    if (read.status !== 404) {
      // Both faults are said. The first thing to go wrong is rarely the
      // important one, and dropping either is how a cause stops being named.
      return NextResponse.json(
        { error: read.error, ...(fault ? { fixedKey: fault.error } : {}) },
        { status: read.status });
    }
  }

  if (fault) {
    return NextResponse.json({ error: fault.error }, { status: fault.status });
  }
  return NextResponse.json(
    { error: "no such artifact", key, ...(name ? { name } : {}) },
    { status: 404 });
}
