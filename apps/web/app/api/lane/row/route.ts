import { NextRequest, NextResponse } from "next/server";
import { row } from "../../../_ui/db";
import { LANES, type Lane } from "../../../lanes.generated";

/**
 * Whether one subject is still in one lane, and what it looks like now.
 *
 * **GET only, and it reads.** This is what makes an event applicable: the
 * board hears that something changed, asks the lane whether it still holds it,
 * and either replaces the row or takes it out. The alternative is the board
 * deciding lane membership from the event's own payload, which is the lane
 * predicate written twice.
 *
 * `{ row: null }` with a 200 rather than a 404. A 404 means the route is not
 * there; an empty answer here means the query ran and the lane does not hold
 * it, which is a fact the board acts on and not a fault.
 */
export const dynamic = "force-dynamic";

/** A uuid, checked before it reaches a `::uuid` cast that would 500 on it. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const lane = params.get("lane") ?? "";
  if (!(lane in LANES)) {
    return NextResponse.json(
      { error: `unknown lane ${lane}`, lanes: Object.keys(LANES) },
      { status: 400 });
  }
  const id = params.get("id") ?? "";
  if (!UUID.test(id)) {
    // Named rather than passed through. `'x'::uuid` raises inside Postgres and
    // the board would read a 500 as "the server is broken" rather than "that
    // was not an id".
    return NextResponse.json(
      { error: "id must be a uuid" }, { status: 400 });
  }
  try {
    return NextResponse.json({ row: await row(lane as Lane, id) });
  } catch (err) {
    console.error("lane row failed", { lane, id, err });
    return NextResponse.json(
      { error: "the lane row could not be read" }, { status: 500 });
  }
}
