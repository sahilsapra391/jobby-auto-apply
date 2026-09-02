import { WINDOWS, SORT_LABELS, type Sort } from "../lanes.generated";
import type { BoardMeta } from "./db";

/** Eastern, like the board: the reader is in Charlotte and so is the sweep. */
const clock = (iso: string | null) => {
  if (!iso) return null;
  return new Date(iso).toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit",
    hour12: false,
  }) + " EDT";
};

/**
 * The scan banner, the ingest summary, and the problems disclosure.
 *
 * **Every number here was invented until 2026-09-02.** `_ui/fixture.ts` held
 * `nextRun: "19:47 EDT"`, `lastScan: "15:51 EDT"` and five problem entries
 * summing to 11, and a reader at 21:28 saw exactly those three values with a
 * next run nearly two hours in the past. The fixture is deleted rather than
 * corrected, so nothing else can quietly read invented numbers from it.
 *
 * **Absent is said, never substituted.** A null column renders as "not
 * recorded"; there is no default to fall back to, because a default here is
 * the fixture again with better manners.
 */
export function ScanBanner({ meta }: { meta: BoardMeta | null }) {
  const ingest = (meta?.lastIngest ?? {}) as Record<string, unknown>;
  const n = (key: string) =>
    typeof ingest[key] === "number" ? (ingest[key] as number) : null;
  const channels = (ingest.byChannel ?? {}) as Record<string, Record<string, unknown>>;
  const problems = Object.entries(channels)
    .filter(([, c]) => c && (c.status === "error" || c.why))
    .map(([source, c]) => ({ source, detail: String(c.why ?? c.status ?? "") }));
  const version = typeof ingest.configVersion === "number"
    ? ingest.configVersion : null;

  return (
    <>
      <div className="jb-banner" {...(version ? { "data-version": version } : {})}>
        <div>
          {version ? <strong>{`Search v${version} is live.`}</strong> : null}{" "}
          <span>
            {meta?.nextScanAt
              ? <>Every 4 hours &middot; next run {clock(meta.nextScanAt)}</>
              : <span className="muted">next run not recorded</span>}
          </span>
          <span className="jb-savestate" />
        </div>
        <button className="jb-btn primary" type="button">Scan now</button>
      </div>
      <p className="jb-ingest">
        {ingest.at ? <>Last scan {clock(String(ingest.at))}</>
                   : <span className="muted">No scan recorded</span>}
        {n("found") != null ? <> &middot; {n("found")!.toLocaleString()} seen</> : null}
        {n("kept") != null ? <> &middot; {n("kept")} new</> : null}
        {n("merged") != null ? <> &middot; {n("merged")!.toLocaleString()} merged</> : null}
        {n("gatedOut") != null
          ? <> &middot; {n("gatedOut")!.toLocaleString()} filtered out</> : null}
        {n("pruned") != null ? <> &middot; {n("pruned")} closed by the employer</> : null}
        {meta?.blocked ? <> &middot; <b>{meta.blocked}</b></> : null}
        {meta?.slotsParked ? <> &middot; <b>{meta.slotsParked}</b></> : null}
        {problems.length ? (
          <>
            <br />
            <details className="jb-errored jb-probs">
              <summary>
                {problems.length} problems:{" "}
                {problems.slice(0, 3)
                  .map((p) => `${p.source}: ${p.detail}`).join(" \u00b7 ")}
                {problems.length > 3 ? ` \u00b7 and ${problems.length - 3} more` : ""}
              </summary>
              <ul className="jb-problist">
                {problems.map((p) => (
                  <li key={p.source}>
                    <b>{p.source}</b><span>{p.detail}</span>
                  </li>
                ))}
              </ul>
            </details>
          </>
        ) : null}
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
