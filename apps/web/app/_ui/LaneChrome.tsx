import { LANE_EMPTY, LANE_HINTS, LANE_SEARCH_PLACEHOLDER } from "../lane_chrome.generated";
import type { Lane } from "../lanes.generated";

/**
 * A lane's subtitle, its search row and what it says when it holds nothing.
 *
 * All three come from `dashboard.py` through `lane_chrome.generated.ts`. They
 * are copy, and copy that names the control which fills a lane is data: "Press
 * Apply on something in Watching", "Press Scan now and it will go looking".
 * v2 shipped "Nothing in this lane." for all five, which reads as broken.
 */

export function LaneHint({ lane, count }: { lane: Lane; count: number }) {
  const hint = LANE_HINTS[lane];
  // The board only writes a hint when the lane has rows, because a subtitle
  // above an empty state is two explanations of one thing.
  if (!hint || !count) return null;
  return <p className="hint">{hint}</p>;
}

/**
 * The per-lane search.
 *
 * A GET form on the same route, like Found's: a filtered lane stays linkable
 * and survives a reload, which the board's in-browser version does not. The
 * board's `data-search` attributes drive a client-side filter over an embedded
 * payload; v2 pages from Postgres, so the honest equivalent is a predicate.
 */
export function LaneSearch({ lane, q, shown, total }: {
  lane: Lane; q?: string; shown: number; total: number;
}) {
  if (!total) return null;
  return (
    <form className="filters" method="get" action="/" data-scope={lane}>
      <input type="hidden" name="lane" value={lane} />
      <input type="search" name="q" defaultValue={q ?? ""}
             data-search={lane} placeholder={LANE_SEARCH_PLACEHOLDER}
             aria-label={`Search ${lane}`} />
      {q ? <a className="btn ghost" href={`/?lane=${lane}`}>Clear</a> : null}
      <span className="fcount" data-count={lane}>
        {shown === total ? `${total.toLocaleString()} shown`
                         : `${shown.toLocaleString()} of ${total.toLocaleString()} shown`}
      </span>
    </form>
  );
}

export function LaneEmpty({ lane }: { lane: Lane }) {
  const said = LANE_EMPTY[lane];
  if (!said) {
    // Named rather than defaulted. A lane with no empty state is a lane that
    // renders the blank panel this component exists to replace, and the
    // generator already refuses to emit fewer than five.
    return <p className="empty"><strong>Nothing here.</strong></p>;
  }
  const [head, sub] = said;
  return <p className="empty"><strong>{head}</strong><span>{sub}</span></p>;
}
