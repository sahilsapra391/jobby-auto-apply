import { NextRequest, NextResponse } from "next/server";
import { filtersFrom, page, type Cursor } from "../../_ui/db";
import { LANES, SORT_LABELS, type Lane, type Sort } from "../../lanes.generated";

/**
 * One page of a lane. **GET only, and it reads.**
 *
 * There is no POST here and there will not be one until the parity gate
 * closes: a new endpoint writing straight to Postgres is a third write path
 * parity does not know about, and the file store would fall behind on exactly
 * the records it touched.
 *
 * Public, like every read on this board since 2026-09-09. The middleware
 * refuses only cross-site mutations, and this route has no POST.
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
  //
  // **What it checks against is the query's own cursor type**, not the sort's
  // name. This asked `sort === "score"`, which was the same question while
  // score was the only numeric ordering and stopped being the same question
  // when Needs you began walking a blocker rank under the sort called `new`:
  // the guard would have refused that lane's own cursor with a 400. The
  // ordering, the cursor key and its type are one fact, emitted together by
  // `v2_lanes`.
  const query = LANES[lane as Lane].sorts[sort]
    ?? { cursorType: LANES[lane as Lane].cursorType };
  if (afterAt !== null) {
    const numeric = /^-?\d+$/.test(afterAt);
    if (query.cursorType === "number" && !numeric) {
      return NextResponse.json(
        { error: `afterAt must be a number for this lane's ordering` },
        { status: 400 });
    }
    if (query.cursorType === "date"
        && (numeric || Number.isNaN(Date.parse(afterAt)))) {
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
    // **Read through `filtersFrom`, not listed here.** This block named three
    // filters while the page read seven, so page one filtered and Load more
    // did not -- the same URL, two answers, and a header count agreeing with
    // neither.
    return NextResponse.json(
      await page(lane as Lane, after, 50, sort, filtersFrom(params)));
  } catch (err) {
    console.error("lane page failed", err);
    return NextResponse.json({ error: "lane page failed" }, { status: 500 });
  }
}
