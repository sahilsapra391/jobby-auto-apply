/**
 * What a Profile row says about its last edit, as a function of what the board
 * sent and what the device has recorded.
 *
 * **Its own module, with no React in it, so it can be run rather than read.**
 * The rule it holds is not obvious -- a local hope outranks an older recorded
 * outcome, a recorded outcome outranks a local hope that has been answered,
 * and a hope past its deadline stops being one -- and a rule like that
 * inspected in a component is a rule nobody has executed.
 */

/** One edit this browser sent, remembered until an outcome for it arrives. */
export type Sent = {
  editId: string;
  /** `queue:target`, so a row asks about its own edit rather than the newest
   *  edit of anything. */
  slot: string;
  at: number;
  submitted: unknown;
};

/** What the device recorded, as `edit_outcomes` holds it. */
export type Recorded = {
  editId: string;
  queue: string;
  state: "pending" | "accepted" | "refused";
  target: string | null;
  submitted: unknown;
  held: unknown;
  reason: string | null;
  at: string | null;
};

export type EditState =
  | { kind: "none" }
  | { kind: "sent"; at: number }
  | { kind: "lost"; at: number }
  | { kind: "pending" }
  | { kind: "accepted"; at: string | null }
  | { kind: "refused"; reason: string | null; submitted: unknown; held: unknown;
      heldRecorded: boolean };

/** How long a local "sent" is believed before the row calls it unheard. */
export const SENT_TTL_MS = 20 * 60_000;

export const slotOf = (queue: string, target: string | null) =>
  `${queue}:${target ?? "*"}`;

/**
 * The state one slot is in.
 *
 * **A sent edit with no outcome wins over an older outcome for the same
 * slot**, because it is the newer fact: the reader edited the same answer
 * twice and the second edit has not been decided. Reading the older outcome
 * there would show "Saved" over an edit that has not been.
 *
 * **And a sent edit that has been decided is not consulted at all.** The
 * caller drops those, and this ignores them too rather than trusting it to,
 * because a stale local entry outranking the device's own answer is the one
 * way this function could assert something false.
 */
export function resolveEditState(
  slot: string, sent: Sent[], outcomes: Recorded[], now: number,
): EditState {
  const decided = new Set(outcomes.map((o) => o.editId));
  const mine = sent.filter((s) => s.slot === slot && !decided.has(s.editId));
  if (mine.length) {
    const newest = mine.reduce((a, b) => (a.at > b.at ? a : b));
    return now - newest.at > SENT_TTL_MS
      ? { kind: "lost", at: newest.at } : { kind: "sent", at: newest.at };
  }
  // Outcomes arrive newest first, so the first match is the last thing that
  // happened to this slot.
  const outcome = outcomes.find((o) => slotOf(o.queue, o.target) === slot);
  if (!outcome) return { kind: "none" };
  if (outcome.state === "pending") return { kind: "pending" };
  if (outcome.state === "accepted") return { kind: "accepted", at: outcome.at };
  return {
    kind: "refused",
    reason: outcome.reason,
    submitted: outcome.submitted,
    held: outcome.held,
    // **Null is two claims and only one is about the store.** `held` is null
    // both when the store said what it holds and it was nothing, and when the
    // refusal carried no held value at all -- a version conflict records none.
    // The row says which rather than drawing "nothing" over silence.
    heldRecorded: outcome.held !== null && outcome.held !== undefined,
  };
}
