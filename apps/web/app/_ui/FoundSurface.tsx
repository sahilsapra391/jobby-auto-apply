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
export function Filters() {
  return (
    <>
      <div className="jb-row jb-basic">
        <input type="search" placeholder="Add a keyword and press Enter…"
               aria-label="Add a keyword" />
        <div className="jb-radios">
          {[["6h", "6 hours"], ["24h", "24 hours"], ["7d", "7 days"],
            ["14d", "14 days"], ["30d", "30 days"]].map(([v, label]) => (
            <label className="jb-radio" key={v}>
              <input type="radio" name="jb-date" value={v}
                     defaultChecked={v === "30d"} />
              <span>{label}</span>
            </label>
          ))}
        </div>
        <label className="jb-check">
          <input type="checkbox" />
          <span>direct listings only</span>
        </label>
        <select aria-label="Sort" defaultValue="score">
          <option value="new">Newest</option>
          <option value="score">Match %</option>
        </select>
        <span className="jb-count">reads Postgres in the next piece</span>
      </div>
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
