import { NextRequest, NextResponse } from "next/server";
import { page, type Cursor } from "../../_ui/db";
import { LANES, SORT_LABELS, type Lane, type Sort } from "../../lanes.generated";

/**
 * One page of a lane. **GET only, and it reads.**
 *
 * There is no POST here and there will not be one until the parity gate
 * closes: a new endpoint writing straight to Postgres is a third write path
 * parity does not know about, and the file store would fall behind on exactly
 * the records it touched.
 *
 * The middleware gates this like every other path, so an unauthenticated
 * request never reaches it.
 */
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const lane = params.get("lane") ?? "";
  if (!(lane in LANES)) {
    // Named rather than defaulted. A typo silently serving Found is how a
    // reader concludes a lane is empty when it was never asked for.
    return NextResponse.json(
      { error: `unknown lane ${lane}`, lanes: Object.keys(LANES) },
      { status: 400 });
  }
  const asked = params.get("sort") ?? "new";
  if (!(asked in SORT_LABELS)) {
    // Named rather than defaulted, for the same reason an unknown lane is: a
    // typo silently serving the other order is how a reader concludes the
    // ranking is broken.
    return NextResponse.json(
      { error: `unknown sort ${asked}`, sorts: Object.keys(SORT_LABELS) },
      { status: 400 });
  }
  const sort = asked as Sort;
  const afterId = params.get("afterId");
  const afterAt = params.get("afterAt");
  // **A cursor belongs to the ordering it was cut from.** A score carried into
  // the date query reaches Postgres as `'93'::timestamptz` and raises
  // `date/time field value out of range`; the client catches that, marks the
  // lane exhausted, and draws "and that is all of them" over 17,341 rows.
  // Refusing here turns a silent wrong answer into a named one.
  if (afterAt !== null) {
    const numeric = /^-?\d+$/.test(afterAt);
    if (sort === "score" && !numeric) {
      return NextResponse.json(
        { error: "afterAt must be a number when sorting by score" },
        { status: 400 });
    }
    if (sort !== "score" && (numeric || Number.isNaN(Date.parse(afterAt)))) {
      return NextResponse.json(
        { error: `afterAt is not a date; a cursor cut from one ordering ` +
                 `cannot page another` },
        { status: 400 });
    }
  }
  const after: Cursor = afterId
    ? { id: afterId, at: afterAt }
    : null;
  try {
    return NextResponse.json(await page(lane as Lane, after, 50, sort, {
      q: params.get("q") ?? undefined,
      within: params.get("within") ?? undefined,
      direct: params.get("direct") === "1",
    }));
  } catch (err) {
    console.error("lane page failed", err);
    return NextResponse.json({ error: "lane page failed" }, { status: 500 });
  }
}
