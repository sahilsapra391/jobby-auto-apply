import { WINDOWS, SORT_LABELS, type Sort } from "../lanes.generated";
import type { BoardMeta } from "./db";
import { SubmitOnChange } from "./SubmitOnChange";

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
export function Filters({ q, within, direct, sort, shown, total, pool, scope }: {
  q?: string; within?: string; direct?: boolean; sort?: Sort;
  shown?: number; total?: number;
  /** Every open posting, before any filter. */
  pool?: number;
  /** The saved search's location scope, in words. */
  scope?: string;
} = {}) {
  const terms = (q ?? "").split(/\s+/).filter(Boolean);
  const filtered = !!(q || within || direct);
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
        {/* **The chips apply on click.** They were radios in a form that only
            acted on submit, so picking 7 days left the count at 18,300 until
            the reader found the Filter button; the old board applies instantly
            and the count moves. `SubmitOnChange` submits the form the control
            is in, so this stays one GET form and gains no state of its own. */}
        <div className="jb-radios">
          {Object.entries(WINDOWS).map(([v, label]) => (
            <label className="jb-radio" key={v}>
              <input type="radio" name="within" value={v}
                     defaultChecked={v === (within ?? "")} />
              <span>{label}</span>
            </label>
          ))}
        </div>
        <label className="jb-check">
          <input type="checkbox" name="direct" value="1"
                 defaultChecked={!!direct} />
          <span>direct listings only</span>
        </label>
        <select name="sort" aria-label="Sort" defaultValue={sort ?? "score"}>
          {Object.entries(SORT_LABELS).map(([v, label]) => (
            <option value={v} key={v}>{label}</option>
          ))}
        </select>
        <button className="jb-btn primary" type="submit">Filter</button>
        {filtered ? (
          // Clear is a link rather than a reset: reset restores the form's
          // defaults and leaves the URL filtered, so the count would not move.
          <a className="jb-btn" href="/?lane=found">Clear</a>
        ) : null}
        {/* **The count says what it counted.** v2 read "50 of 18,300 jobs",
            which is a page size against a pool and invites the reading that
            18,300 were searched and 50 matched. The filters are predicates on
            a Postgres query, so the whole pool really is filtered; what 50 is
            is the page. The old board's scoping sentence existed because it
            filtered an embedded slice and could not say more, and repeating
            its shape here would understate what this does. */}
        <span className="jb-count">
          {total === undefined ? "" : (
            <>
              {total.toLocaleString()} {total === 1 ? "job" : "jobs"}
              {filtered && pool !== undefined && pool !== total
                ? <> match, of {pool.toLocaleString()} in the pool</>
                : null}
              {shown !== undefined && shown < total
                ? <> &middot; {shown.toLocaleString()} on this page</> : null}
            </>
          )}
        </span>
      </form>
      <SubmitOnChange />
      {/* The keyword chips the old board renders under the input. Each is a
          link that removes its own term, which is what a chip's × does. */}
      <div className="jb-pills">
        {terms.map((term) => {
          const rest = terms.filter((t) => t !== term).join(" ");
          const next = new URLSearchParams({ lane: "found" });
          if (rest) next.set("q", rest);
          if (within) next.set("within", within);
          if (direct) next.set("direct", "1");
          if (sort) next.set("sort", sort);
          return (
            <a className="chip" key={term} href={`/?${next}`}
               aria-label={`Remove ${term}`}>{term} &#215;</a>
          );
        })}
        {scope ? (
          // **The location scope, said out loud.** The saved search stores an
          // empty `locations`, which the storage contract defines as
          // Worldwide, and v2 showed nothing at all -- so UK listings leading
          // the page looked like a fault rather than the scope working. The
          // picker that changes it is item 21.
          <span className="chip" title="From your saved search">{scope}</span>
        ) : null}
      </div>
      {/* `open` is what the stylesheet's rotation rule keys on; the triangle
          did not turn because nothing toggled a class and the rule was written
          for the attribute. Nothing to add here beyond letting the element be
          what it is -- the marker rotates on `details[open] > summary`. */}
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
