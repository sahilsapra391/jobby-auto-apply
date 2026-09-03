import { NextRequest, NextResponse } from "next/server";
import { pool } from "../../_ui/db";

/**
 * What one posting says, in its own two halves. **GET only, and it reads.**
 *
 * This is the board's `/api/posting`, and it exists in v2 because item 22
 * shipped without it. The card disclosure's whole stylesheet came across --
 * `.jb-det`, `.jb-slot`, `.jb-sections`, thirteen classes of it -- and neither
 * the markup nor this route did, so the feature was closed as "already
 * matching" on the evidence of the CSS. The reachability check that should
 * have caught it started from classes v2 emits, and a class v2 never emits was
 * outside its subject; it is inverted now.
 *
 * **Fetched rather than embedded, and that is measured rather than assumed.**
 * `scripts/posting_sections.py` splits a posting by matching heading lines
 * against the vocabulary employers draw them from, and the result averages
 * about 265 bytes a record. v2 pages 50 rows at a time, so embedding would be
 * cheap here in a way it never was on the board -- but it would be 50 records
 * of prose in every page load to serve the one card a reader opens, and the
 * board's own reasoning holds for the same reason: only an opened card pays.
 *
 * The middleware gates this like every other path, so an unauthenticated
 * request never reaches it.
 */
export const dynamic = "force-dynamic";

/** The board's own caps, kept identical so the two render the same thing. */
const MAX_LINES = 12;
const MAX_CHARS = 400;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function trim(lines: unknown): string[] {
  if (!Array.isArray(lines)) return [];
  return lines
    .map((line) => String(line ?? "").slice(0, MAX_CHARS))
    .filter(Boolean)
    .slice(0, MAX_LINES);
}

export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id") ?? "";
  // **Validated as a uuid, not as "some id-shaped string".** The board's
  // version takes `[A-Za-z0-9_-]{1,64}` because its ids are slugs; v2's
  // postings are uuids and anything else is a query that cannot match, so it
  // is refused by name rather than run.
  if (!UUID.test(id)) {
    return NextResponse.json({ error: "id must be a posting uuid" },
                             { status: 400 });
  }
  const result = await pool().query(
    "select sections from postings where id = $1", [id]);
  if (result.rowCount === 0) {
    // A posting that has been pruned is gone rather than empty, and saying so
    // is what lets the caller tell "no headings" from "no posting". The card
    // draws the same either way today; the distinction is for whoever reads
    // this next.
    return NextResponse.json({ error: "no such posting" }, { status: 404 });
  }
  const sections = (result.rows[0].sections ?? {}) as Record<string, unknown>;
  return NextResponse.json({
    ok: true,
    responsibilities: trim(sections.responsibilities),
    requirements: trim(sections.requirements),
  });
}
