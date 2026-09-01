import { WINDOWS, SORT_LABELS, type Sort } from "../lanes.generated";
import { SCAN, PROBLEMS } from "./fixture";

/** The scan banner, the ingest summary, and the problems disclosure. */
export function ScanBanner() {
  return (
    <>
      <div className="jb-banner" data-version={SCAN.version}>
        <div>
          <strong>Search v{SCAN.version} is live.</strong>{" "}
          <span>{SCAN.cadence} &middot; next run {SCAN.nextRun}</span>
          <span className="jb-savestate" />
        </div>
        <button className="jb-btn primary" type="button">Scan now</button>
      </div>
      <p className="jb-ingest">
        Last scan {SCAN.lastScan} &middot; {SCAN.seen.toLocaleString()} seen
        &middot; {SCAN.fresh} new &middot; {SCAN.merged.toLocaleString()} merged
        &middot; {SCAN.filtered.toLocaleString()} filtered out &middot;{" "}
        {SCAN.closed} closed by the employer
        <br />
        <details className="jb-errored jb-probs">
          <summary>
            {PROBLEMS.reduce((n, p) => n + p.times, 0)} problems:{" "}
            {PROBLEMS.slice(0, 3)
              .map((p) => `${p.source}${p.times > 1 ? ` ×${p.times}` : ""}: ${p.detail}`)
              .join(" · ")}
            {PROBLEMS.length > 3 ? ` · and ${PROBLEMS.length - 3} more` : ""}
          </summary>
          <ul className="jb-problist">
            {PROBLEMS.map((p) => (
              <li key={p.source}>
                <b>{p.source}</b>
                {p.times > 1 ? ` ×${p.times}` : ""}
                <span>{p.detail}</span>
              </li>
            ))}
          </ul>
        </details>
      </p>
    </>
  );
}

/**
 * The filter row.
 *
 * The `<select>` elements are plain and native, exactly as the board ships
 * them; `SelectFace` runs premium_ui.SCRIPT afterwards to put the styled face
 * over them. Nothing here draws a listbox.
 */
export function Filters({ q, within, direct, sort, shown, total }: {
  q?: string; within?: string; direct?: boolean; sort?: Sort;
  shown?: number; total?: number;
} = {}) {
  return (
    <>
      {/* **A plain GET form, no client JavaScript.** The board filters an
          embedded payload in the browser; v2 pages from Postgres, so a filter
          is a predicate on a query and the honest way to carry one is the URL.
          It also makes a filtered lane linkable and survivable across a reload,
          which the board's version is not. Submitting on Enter in the keyword
          box is what a form already does. */}
      <form className="jb-row jb-basic" method="get" action="/">
        <input type="hidden" name="lane" value="found" />
        <input type="search" name="q" defaultValue={q ?? ""}
               placeholder="Add a keyword and press Enter…"
               aria-label="Add a keyword" />
        <div className="jb-radios">
          {Object.entries(WINDOWS).map(([v, label]) => (
            <label className="jb-radio" key={v}>
              <input type="radio" name="within" value={v}
                     defaultChecked={v === (within ?? "30d")} />
              <span>{label}</span>
            </label>
          ))}
        </div>
        <label className="jb-check">
          <input type="checkbox" name="direct" value="1"
                 defaultChecked={!!direct} />
          <span>direct listings only</span>
        </label>
        <select name="sort" aria-label="Sort" defaultValue={sort ?? "new"}>
          {Object.entries(SORT_LABELS).map(([v, label]) => (
            <option value={v} key={v}>{label}</option>
          ))}
        </select>
        <button className="jb-btn primary" type="submit">Filter</button>
        <span className="jb-count">
          {shown === undefined || total === undefined ? ""
            : shown === total
              ? `${total.toLocaleString()} jobs`
              : `${shown.toLocaleString()} of ${total.toLocaleString()} jobs`}
        </span>
      </form>
      <div className="jb-pills" />
      <details className="jb-adv">
        <summary>Advanced search</summary>
        <div className="jb-advbody">
          <div className="jb-row">
            <input type="search" placeholder="Hide jobs mentioning…"
                   aria-label="Hide jobs mentioning" />
            <select aria-label="Where to search" defaultValue="title">
              <option value="title">Title only</option>
              <option value="title+desc">Title and description</option>
            </select>
          </div>
        </div>
      </details>
    </>
  );
}
