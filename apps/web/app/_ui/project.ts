/**
 * What one event does to one lane.
 *
 * **The board replaces rows and removes them. It does not insert.** Placing a
 * new row means sorting it into an order the database chose, and a comparator
 * here would be `order by <col> desc nulls last, id desc` written a second
 * time in another language: the exact failure `scripts/v2_lanes.py` is
 * generated to prevent, and the one that would be wrong first about nulls,
 * which Postgres sorts *first* under `desc` and every lane here sorts last.
 *
 * So an arrival is counted and announced, and one click fetches it in the
 * order the database picked. A removal needs no ordering and happens live.
 *
 * **Membership is never inferred from the event's payload either.** An event
 * says something changed; `/api/lane/row` says whether the lane still holds
 * it. `application.changed {to: "watch"}` looks like it settles the question
 * and does not: Submitted's membership is two correlated subqueries over the
 * event log, and no `to` field can answer it.
 */

import type { Envelope } from "./live";
import type { Lane } from "../lanes.generated";

/** What the lane should do about an event. */
export type Action =
  | { do: "ignore" }
  | { do: "ask"; id: string }
  | { do: "reload" };

/**
 * The lanes drawn from `applications`, and the one drawn from `postings`.
 *
 * `tests/test_lane_projection.test.mjs` derives this from the generated SQL
 * rather than accepting it, because a lane moving between the two tables and
 * this list not moving with it is a lane that silently stops updating.
 */
export const POSTING_LANES: Lane[] = ["found"];

const APPLICATION_KINDS = new Set([
  "application.changed", "application.phase",
  "application.completed", "application.needs_you",
]);

export function actionFor(lane: Lane, event: Envelope): Action {
  const posting = POSTING_LANES.includes(lane);
  const payload = (event.payload ?? {}) as Record<string, unknown>;

  if (APPLICATION_KINDS.has(event.kind)) {
    if (posting) return { do: "ignore" };
    const id = payload.application_id;
    return typeof id === "string" ? { do: "ask", id } : { do: "ignore" };
  }
  if (event.kind === "posting.decided") {
    // Found is postings minus this account's decisions, so a decision is the
    // only thing that moves its membership. No application lane cares: a
    // decision row and an application are different objects.
    if (!posting) return { do: "ignore" };
    const id = payload.posting_id;
    return typeof id === "string" ? { do: "ask", id } : { do: "ignore" };
  }
  if (event.kind === "pool.delta") {
    // The pool moved and the event names nothing, because it is a sweep and
    // not a row. **It carries no count to add**: see the idempotence rule in
    // live.ts, which a resync's replay window would break.
    return posting ? { do: "reload" } : { do: "ignore" };
  }
  // device.status and runner.update_required drive a banner, not a lane.
  return { do: "ignore" };
}

export type Row = Record<string, unknown> & { id: string };

/**
 * Put the lane's answer about one row back into the list.
 *
 * `null` takes it out. A row already shown is replaced where it stands, which
 * leaves it in a position the database might now order differently -- that is
 * the trade named at the top, and it is also the kinder half of it, since a
 * card that jumps while being read is worse than one a moment out of order.
 *
 * A row the lane holds and the list does not is an **arrival**, reported and
 * not inserted.
 */
export function applyRow(rows: Row[], id: string, row: Row | null): {
  rows: Row[]; changed: boolean; arrived: boolean;
} {
  const at = rows.findIndex((r) => r.id === id);
  if (row === null) {
    if (at < 0) return { rows, changed: false, arrived: false };
    return { rows: rows.filter((r) => r.id !== id), changed: true, arrived: false };
  }
  if (at < 0) return { rows, changed: false, arrived: true };
  const next = rows.slice();
  next[at] = row;
  return { rows: next, changed: true, arrived: false };
}
