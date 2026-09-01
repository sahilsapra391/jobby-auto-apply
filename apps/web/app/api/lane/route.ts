import { NextRequest, NextResponse } from "next/server";
import { page, type Cursor } from "../../_ui/db";
import { LANES, type Lane } from "../../lanes.generated";

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
  const afterId = params.get("afterId");
  const after: Cursor = afterId
    ? { id: afterId, at: params.get("afterAt") }
    : null;
  try {
    return NextResponse.json(await page(lane as Lane, after));
  } catch (err) {
    console.error("lane page failed", err);
    return NextResponse.json({ error: "lane page failed" }, { status: 500 });
  }
}
