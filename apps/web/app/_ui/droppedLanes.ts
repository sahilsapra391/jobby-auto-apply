/**
 * What the Dropped archive searches and how it names where a role came from.
 *
 * A plain module, neither server nor client, because the row that draws the
 * chip and the dialog that filters on it both need the same answer. Two
 * spellings of "which lane was this dropped from" is how the chip and the
 * select would come to disagree.
 */

type Row = Record<string, unknown>;

const str = (v: unknown) => (v == null ? "" : String(v)).trim();

/**
 * The lane names a dropped role can carry, in every spelling the store has
 * written.
 *
 * **The board's own data had both "Watching" and "watch"**, because the
 * session recorded a lane key in one era and a lane label in another. Showing
 * both would split one lane into two entries in the select, and a reader
 * picking "Watching" would miss every role recorded as "watch".
 */
const LANE_LABELS: Record<string, string> = {
  found: "Found",
  watch: "Watching", watching: "Watching",
  flight: "In flight", inflight: "In flight", "in flight": "In flight",
  act: "Needs you", review: "Needs you", active: "Needs you",
  "needs you": "Needs you",
  done: "Submitted", submitted: "Submitted",
};

/** The lane a role was dropped from, as the board names it. */
export function droppedFromLabel(raw: unknown): string {
  const said = str(raw);
  if (!said) return "unknown";
  return LANE_LABELS[said.toLowerCase()] ?? said;
}

/**
 * The text a dialog search looks through, which is the board's `dropped_row`
 * haystack: company, role, the lane it was dropped from, the stage it reached
 * and the reason. The raw lane is kept beside its label so "watch" still finds
 * a role recorded as "watch" and labelled "Watching".
 */
export function droppedHaystack(row: Row): string {
  return [row.company, row.role, row.dropped_from, droppedFromLabel(row.dropped_from),
          row.dropped_stage, row.dropped_reason]
    .map(str).filter(Boolean).join(" ").toLowerCase();
}
