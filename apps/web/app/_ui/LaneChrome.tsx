import { LANE_EMPTY, LANE_HINTS, LANE_SEARCH_PLACEHOLDER } from "../lane_chrome";
import type { Lane } from "../lanes.generated";
import { SubmitOnChange } from "./SubmitOnChange";

/**
 * A lane's subtitle, its search row and what it says when it holds nothing.
 *
 * All three come from `dashboard.py` through `lane_chrome.ts`. They
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
export function LaneSearch({ lane, q, facet, options = [], shown, total, whole }: {
  lane: Lane; q?: string;
  /** The select's current value, when one is chosen. */
  facet?: string;
  /**
   * The select's options, `[value, label]`, from `laneFacets`.
   *
   * **The board's `filter_bar` select, as a parameter.** Watching filters to
   * fresh postings, a region or a discipline; Needs you to ready or still
   * blocked; Submitted to an outcome. The board does it in the browser over
   * `data-` attributes, so it costs nothing and needs no URL; here each option
   * is a clause in the generated lane SQL and the URL carries which, so a
   * filtered lane is linkable and pages correctly. Absent when a lane has
   * nothing to select, which is the board's rule too.
   */
  options?: [string, string][];
  shown: number; total: number;
  /**
   * The lane's size with no search applied, which decides whether this row is
   * drawn at all.
   *
   * `total` is the searched count, so hiding on it took the search box away
   * the moment a search matched nothing, and the term and Clear went with it:
   * the only way back was editing the URL.
   */
  whole?: number;
}) {
  const laneSize = whole ?? total;
  if (!laneSize) return null;
  const narrowed = !!q || !!facet;
  return (
    <form className="filters" method="get" action="/" data-scope={lane}
          data-lane-filter="">
      <input type="hidden" name="lane" value={lane} />
      <input type="search" name="q" defaultValue={q ?? ""}
             data-search={lane} placeholder={LANE_SEARCH_PLACEHOLDER}
             aria-label={`Search ${lane}`} />
      {options.length ? (
        // `SubmitOnChange` applies it, like every select on Found: a select
        // that waits for Enter in a different box is a control that does not
        // look like it works.
        <select name="facet" data-filter={lane} defaultValue={facet ?? ""}
                aria-label={`Filter ${lane}`}>
          <option value="">All</option>
          {options.map(([value, label]) => (
            <option value={value} key={value}>{label}</option>
          ))}
        </select>
      ) : null}
      {narrowed ? <a className="btn ghost" href={`/?lane=${lane}`}>Clear</a> : null}
      <span className="fcount" data-count={lane}>
        {narrowed && total !== laneSize
          ? `${total.toLocaleString()} of ${laneSize.toLocaleString()} match`
          : shown === total ? `${total.toLocaleString()} shown`
          : `${shown.toLocaleString()} of ${total.toLocaleString()} shown`}
      </span>
      {options.length ? <SubmitOnChange /> : null}
    </form>
  );
}

export function LaneEmpty({ lane, searched }: {
  lane: Lane;
  /**
   * The search the reader typed, when there is one.
   *
   * **An empty result under a search is not an empty lane.** The lane's own
   * copy says "Nothing on the watch list" and names the button that fills it,
   * which is false here and sends the reader off to press Scan now while the
   * rows sit behind a search that matched none of them.
   */
  searched?: string;
}) {
  if (searched) {
    return (
      <p className="empty">
        <strong>No rows match “{searched}”.</strong>
        <span>The lane is not empty; nothing in it matched the search. Clear the search to see all of it.</span>
      </p>
    );
  }
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
