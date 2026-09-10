import { WINDOWS, SORT_LABELS, type Sort } from "../lanes.generated";
import { AddPosting, FindSources } from "./FoundActions";
// `ANY_WINDOW` is no longer imported: the "All time" chip is gone, and the
// value still round-trips for anyone who types `within=all` in the URL.
import { DEFAULT_WINDOW } from "./window";
import { anyFilterSet, laneQuery, type BoardMeta, type Filters } from "./db";
import { SubmitOnChange } from "./SubmitOnChange";
import { LocationPicker } from "./LocationPicker";
import {
  // DEGREE_OPTIONS, EMPLOYMENT_TYPES, JOB_TYPES and ROLE_CATEGORIES are
  // deliberately not imported: nothing populates the columns behind them. See
  // the note above the Max experience row.
  EXPERIENCE_OPTIONS,
  SCOPE_OPTIONS, UNKNOWN_TOGGLES, WORKPLACES, type Option,
} from "../search.generated";
import { READ_ONLY } from "./actions";

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
export function ScanBanner({ meta, writesEnabled = false }: {
  meta: BoardMeta | null;
  /** Whether the control is offered. The route reads the same flag
   *  against the same database, so this is never authorisation. */
  writesEnabled?: boolean;
}) {
  const ingest = (meta?.lastIngest ?? {}) as Record<string, unknown>;
  const n = (key: string) =>
    typeof ingest[key] === "number" ? (ingest[key] as number) : null;
  // **This list said 27 and meant 1.** It was built here, from
  // `c.status === "error" || c.why`, and every channel carries a `why` --
  // "353 boards", "1 of 1 countries x 1 terms". Measured 2026-09-07: 27
  // channels listed, 1 actually errored, 26 there for having a note. A
  // problems list containing everything is a list nobody reads, which is the
  // same fault as the error list that was 80% 304s.
  //
  // It is derived on the device now (`scripts/source_health.py`), for two
  // reasons. A rule living in the renderer is a rule the device cannot alarm
  // on, and this one is a reason on the dead-man ping. And the interesting
  // problems need the yield ledger and the board registry, which the renderer
  // does not have.
  //
  // A sweep from before that existed carries no `problems`, and that is said
  // rather than reconstructed: the old derivation is exactly what is being
  // removed, and falling back to it would put the 27 back on the first load
  // after a deploy.
  const raw = Array.isArray(ingest.problems) ? ingest.problems : null;
  const problems = (raw ?? []).map((p) => {
    const row = (p ?? {}) as Record<string, unknown>;
    return {
      key: [row.kind, row.source, row.board].filter(Boolean).join(":"),
      source: String(row.source ?? ""),
      what: String(row.what ?? ""),
      takes: String(row.takes ?? ""),
    };
  }).filter((p) => p.what);
  const version = typeof ingest.configVersion === "number"
    ? ingest.configVersion : null;

  return (
    <>
      <div className="jb-banner" {...(version ? { "data-version": version } : {})}>
        <div>
          {version ? <strong>{`Search v${version} is live.`}</strong> : null}{" "}
          <span>
            {/* Two different passes, so two different names. The connectors
                run themselves; Find new sources is the agent discovery step
                that finds boards the registry does not have. */}
            {meta?.nextScanAt
              ? <>Connectors run every 4 hours &middot; next {clock(meta.nextScanAt)}</>
              : <span className="muted">next connector run not recorded</span>}
          </span>
          <span className="jb-savestate" />
        </div>
        <FindSources enabled={writesEnabled} />
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
        {raw === null ? (
          <> &middot; <span className="muted">problems not recorded</span></>
        ) : problems.length ? (
          <>
            <br />
            <details className="jb-errored jb-probs">
              <summary>
                {problems.length === 1 ? "1 problem: " : `${problems.length} problems: `}
                {problems.slice(0, 2).map((p) => p.what).join(" \u00b7 ")}
                {problems.length > 2 ? ` \u00b7 and ${problems.length - 2} more` : ""}
              </summary>
              <ul className="jb-problist">
                {/* What it is, and what it would take. A line a reader cannot
                    act on is a log entry wearing a problem's clothes. */}
                {problems.map((p) => (
                  <li key={p.key}>
                    <b>{p.source}</b>
                    <span>{p.what}{p.takes ? ` \u2014 ${p.takes}` : ""}</span>
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
export function Filters({ q, within, direct, sort, shown, total, pool, scope,
                         locations = [], writesEnabled = false,
                         searchScope = "title", extraLocations = [],
                         not_, cos, conames, wp, exp,
                         unkposted, unkwp, unkexp, incremote }: {
  /** The keywords, as chips. One chip is one phrase; chips are OR'd. */
  q?: string[]; within?: string; direct?: boolean; sort?: Sort;
  /** Words to exclude. `not` is a reserved word, hence the underscore. */
  not_?: string;
  /** "all" | "only" | "hide" -- what to do with the company names beside it. */
  cos?: string;
  conames?: string;
  /** Workplace kinds still ticked. Undefined means untouched, so all of them. */
  wp?: string[];
  /** Max years of experience, as the option value. */
  exp?: string;
  shown?: number; total?: number;
  /** Every open posting, before any filter. */
  pool?: number;
  /** The saved search's location scope, in words. */
  scope?: string;
  /** Country codes off the saved search; empty is Worldwide by contract. */
  locations?: string[];
  writesEnabled?: boolean;
  /** The saved `searchScope`, so the select opens on what is stored. */
  searchScope?: string;
  /** Codes the pool holds that the taxonomy does not place. */
  extraLocations?: string[];
  /**
   * The four widening switches. Undefined is the default, which is on.
   *
   * Each rides with a hidden `=0` companion so that unticking one is
   * expressible at all; see `FILTER_KEYS`.
   */
  unkposted?: boolean; unkwp?: boolean; unkexp?: boolean; incremote?: boolean;
} = {}) {
  // Every advanced control carries the same reason when it is dark, so it is
  // named once.
  const dark = writesEnabled ? undefined : READ_ONLY;
  // **The chips are the keywords, not the words in them.** Splitting on
  // whitespace made "product manager" two chips and two filters, which is the
  // same mistake the tsquery made one layer down: a two-word phrase is one
  // thing a reader asked for.
  const terms = (q ?? []).map((t) => t.trim()).filter(Boolean);
  // **Every filter this surface was rendered with, as one object.** A URL built
  // from some of the props is the hand-listing that had page one filtered and
  // page two not, in a third place.
  const held: Filters = {
    q: terms, within, direct, not: not_, scope: searchScope, cos, conames,
    loc: locations, wp, exp, unkposted, unkwp, unkexp, incremote,
  };
  // Which parameter each unknown toggle carries, so the row is drawn from
  // `UNKNOWN_TOGGLES` rather than from a second list of three labels beside
  // it. `degree` is filtered out below and has no entry here, which is what
  // makes its absence a decision rather than an oversight.
  const UNKNOWN_PARAM: Record<string, string> = {
    experience: "unkexp", workplace: "unkwp", postedAt: "unkposted",
  };
  const unknownOn: Record<string, boolean | undefined> = {
    experience: unkexp, workplace: unkwp, postedAt: unkposted,
  };
  // **Read off `FILTER_KEYS`, not off three of them.** This was
  // `!!(q || within || direct)`, so a lane narrowed by locations, workplace,
  // an exclude term or a company list said it was unfiltered: Clear went dark
  // on a filtered board and the count line dropped its pool number. Third
  // place in this repository where a subset of a field list sat beside the
  // list, after `/api/lane` and `SaveFilters`.
  const filtered = anyFilterSet(held);
  return (
    // **`.jb-filters` is the surface, and it was missing.** The board wraps
    // this whole block -- the row, the keyword chips and Advanced search -- in
    // one card: `background:var(--card)`, the shared 1px hair border and the
    // shared shadow. v2 emitted the three as siblings of the page, so the
    // controls floated on the page ground with no card under them, which is
    // the one measured background difference between the two boards on Found.
    <div className="jb-filters">
      {/* **A plain GET form, no client JavaScript.** The board filters an
          embedded payload in the browser; v2 pages from Postgres, so a filter
          is a predicate on a query and the honest way to carry one is the URL.
          It also makes a filtered lane linkable and survivable across a reload,
          which the board's version is not. Submitting on Enter in the keyword
          box is what a form already does. */}
      {/* **One form around every control, because a control outside one is
          never submitted.** The advanced block used to sit after `</form>`,
          so none of it reached the server however it was named -- and none of
          it was named either. The form carries `display:contents`, so the
          rows lay out exactly as they did when it wrapped only the first one. */}
      <form className="jb-filter-form" method="get" action="/">
      <div className="jb-row jb-basic">
        <input type="hidden" name="lane" value="found" />
        {/* **The chips already set are hidden inputs; the box adds one more.**
            The placeholder has always said "Add a keyword and press Enter" and
            the box was a single field whose whole contents were the search, so
            a second keyword replaced the first. One `q` per chip, repeated,
            the way `loc` and `wp` already ride. */}
        {terms.map((term) => (
          <input type="hidden" name="q" value={term} key={term} />
        ))}
        <input type="search" name="q" defaultValue=""
               placeholder="Add a keyword and press Enter…"
               aria-label="Add a keyword" />
        {/* **The chips apply on click.** They were radios in a form that only
            acted on submit, so picking 7 days left the count at 18,300 until
            the reader found the Filter button; the old board applies instantly
            and the count moves. `SubmitOnChange` submits the form the control
            is in, so this stays one GET form and gains no state of its own. */}
        {/* **Five chips, and no "All time".** The board offers 6 hours through
            30 days and stops, so v2 does too.

            The cost is stated rather than hidden, because it is real: 2,506 of
            the 22,735 postings in the pool were posted more than 30 days ago
            and `within=all` is the only thing that reached them. Measured
            2026-09-09 -- `within=all` answers 22,735 and `within=30d` answers
            20,229. A reader who wants those types the parameter; there is no
            longer a control for it. */}
        <div className="jb-radios">
          {(Object.entries(WINDOWS) as [string, string][])
            .map(([v, label]) => (
            <label className="jb-radio" key={v}>
              <input type="radio" name="within" value={v}
                     defaultChecked={v === (within ?? DEFAULT_WINDOW)} />
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
        {/* **No Filter button.** Every control here applies on change, so a
            button that re-applies what is already applied is a control whose
            only job is to make the reader wonder whether the last click
            worked. `SubmitOnChange` covers the whole filter form; the keyword
            box submits on Enter, which is what a form does anyway.

            **Reset to default is a link rather than a form reset**, because a
            reset restores the form's defaults and leaves the URL filtered, so
            the count would not move and the reader would be looking at the old
            result under new controls. */}
        {/* **"Clear", which is what the board calls it.** "Reset to default"
            described the mechanism (a link back to the unfiltered URL) rather
            than the act, and a reader looking for the board's Clear did not
            find one. */}
        {filtered
          ? <a className="jb-btn" href="/?lane=found">Clear</a>
          : <button className="jb-btn" type="button" disabled
                    title="nothing is filtered, so there is nothing to clear">
              Clear
            </button>}
        {/* **The count says what it counted, and it compares the numbers
            rather than reading the URL.** v2 read "50 of 18,300 jobs", which
            is a page size against a pool and invites the reading that 18,300
            were searched and 50 matched. The filters are predicates on a
            Postgres query, so the whole pool really is filtered; what 50 is is
            the page. The old board's scoping sentence existed because it
            filtered an embedded slice and could not say more, and repeating
            its shape here would understate what this does.

            **The pool clause was gated on `filtered` and vanished on a lane
            that plainly was.** A bare `/?lane=found` reads 20,229 against a
            pool of 22,735 and said "20,229 jobs", because the 30-day default
            is a filter nobody typed. `total !== pool` is the fact; whether a
            reader supplied a parameter is a different question and not this
            one. */}
        <span className="jb-count">
          {total === undefined ? "" : (
            <>
              {total.toLocaleString()} {total === 1 ? "job" : "jobs"}
              {pool !== undefined && pool !== total
                ? <> match, of {pool.toLocaleString()} in the pool</>
                : null}
              {shown !== undefined && shown < total
                ? <> &middot; {shown.toLocaleString()} on this page</> : null}
            </>
          )}
        </span>
      </div>
      <SubmitOnChange />
      {/* The keyword chips the old board renders under the input. Each is a
          link that removes its own term, which is what a chip's × does. */}
      <div className="jb-pills">
        {terms.map((term) => {
          // **Through `laneQuery`.** This hand-listed four keys, so removing
          // one keyword from a two-keyword search silently cleared every
          // advanced filter with it. Fourth writer of a lane URL onto the one
          // derivation.
          const next = laneQuery("found", (sort ?? "score") as Sort, {
            ...held, q: terms.filter((t) => t !== term),
          });
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
      {/* **The board's advanced body, and it is not drawn while writes are
          gated.** It was built present-and-disabled, on the rule that a
          control absent from the page cannot be reasoned about while a dark
          one says what is coming. That rule was written for a handful of
          buttons and this block is not a handful: measured on the deployed
          board, 433 of the 497 controls in this card were disabled, around 9
          live ones, every one of them looking the same apart from the cursor.
          At that ratio "present and disabled" stops describing the feature and
          starts describing the card, and the working filters -- the date
          window, direct listings, the sort -- read as broken because they are
          nine controls inside four hundred that do nothing.

          So the summary names what is inside and when it arrives, and the
          controls themselves appear with the writes they need. The note about
          degree, role and job type stays either way, because it is a fact
          about the data rather than about the gate: all three are populated on
          0 of 22,762 postings, so a working filter on them would return
          nothing and look broken. */}
      <details className="jb-adv">
        <summary>Advanced search</summary>
        <div className="jb-advbody">
          {writesEnabled ? (
            <>
              <div className="jb-row">
                <input type="search" name="not" defaultValue={not_ ?? ""}
                       placeholder="Hide jobs mentioning…"
                       aria-label="Hide jobs mentioning" />
                <select name="scope" aria-label="Where to search"
                        defaultValue={searchScope}>
                  {SCOPE_OPTIONS.map(([v, label]) => (
                    <option value={v} key={v}>{label}</option>))}
                </select>
                <select name="cos" aria-label="Companies"
                        defaultValue={cos ?? "all"}>
                  <option value="all">All companies</option>
                  <option value="only">Only these companies</option>
                  <option value="hide">Hide these companies</option>
                </select>
                <input type="search" name="conames" defaultValue={conames ?? ""}
                       placeholder="Company names, comma separated"
                       aria-label="Company names" />
              </div>
              <div className="jb-row">
                <div className="jb-location-field">
                  <span className="jb-location-label">Locations</span>
                  <LocationPicker selected={locations}
                                  extra={extraLocations} />
                </div>
                <div className="jb-stack">
                  {/* **Wired now, and it was doing more harm decorative than
                      it looked.** v2 dropped every posting with no country
                      from a scoped search, full stop, so there was nothing for
                      this box to widen and it was rendered inert with a note
                      saying so. The board's rule is narrower and better:
                      "Only genuinely location-free remote work answers a
                      search for somewhere specific, and only when asked for."

                      Named for the unticked state, like the three below, so
                      the default puts nothing in the URL. */}
                  <label className="jb-check">
                    <input type="hidden" name="incremote" value="0" />
                    <input type="checkbox" name="incremote" value="1"
                           defaultChecked={incremote !== false} />
                    <span>include remote for this selection</span>
                  </label>
                  <div className="jb-checks">
                    {WORKPLACES.map(([v, label]) => (
                      <label className="jb-check" key={v}>
                        <input type="checkbox" name="wp" value={v}
                               defaultChecked={wp === undefined || wp.includes(v)} />
                        <span>{label}</span>
                      </label>))}
                  </div>
                </div>
              </div>
              {/* **Degree, Role, Employment and Job type are gone, and their
                  absence is the honest state.** Measured 2026-09-05 over all
                  25,204 pool records: `extracted.degree`,
                  `extracted.employmentType` and `extracted.roleCategory` are
                  populated in 0 of them, and Postgres has no column for any of
                  the three -- `postings` carries no `extracted` at all. Job
                  type (permanent, temporary, volunteer) has neither a column
                  nor an extracted key.

                  So all four could only ever match nothing. A control that
                  cannot match is the 433-disabled-controls problem wearing a
                  better costume: it looks like a filter, and it reads as
                  broken to the first person who tries it. The old board shows
                  the same emptiness behind a note saying so, which is better
                  than a live control and worse than no control.
                  `found_lane.py` says it plainly: "degree, role and job type
                  are not extracted yet".
                  
                  Bringing them back is an ingest change, not a filter change,
                  and it is logged as such in docs/plans/phase3-inputs.md.
                  Max experience stays: `years_min`/`years_max` are populated
                  in 9,826 of 25,204, 39.0%, so it has something to filter. */}
              <div className="jb-row">
                {([["Max experience", EXPERIENCE_OPTIONS]] as [string, Option[]][])
                  .map(([label, options]) => (
                    <label className="jb-lab" key={label}>{label}
                      <select name="exp" defaultValue={exp ?? ""}>
                        {options.map(([v, text]) => (
                          <option value={v} key={v}>{text}</option>))}
                      </select>
                    </label>))}
              </div>
              <div className="jb-row jb-checks">
                {/* **Three, wired, and two of them were changing results while
                    they sat here decorative.** Each widens the filter beside
                    it, which is why they waited for those filters -- and two
                    of the three were not "off", they were *stuck on the
                    narrow side*, which is worse than inert:

                      unknown posted date   v2 had `p.posted_at is not null`,
                                            so 2,433 undated postings were
                                            invisible under every window. The
                                            board keeps them.
                      unknown workplace     `p.workplace = any(...)` is NULL
                                            for a null workplace, so 17,084
                                            postings survived only by being
                                            flagged remote.
                      unknown experience    already permanently on. The box
                                            makes it a choice.

                    Clicked on the board 2026-09-09: unticking posted date
                    moved 3300 to 3211, workplace to 2191, and Max experience 3
                    reads 2062 with the box and 539 without it.

                    **`degree` is not offered, and that is the honest state.**
                    The board keeps it because it has a Degree select to modify
                    and v2 has none: `postings` carries no degree column and
                    `extracted.degree` was populated in 0 of 25,204 records. On
                    the board the pair does exactly what that implies -- I
                    clicked it: Degree = Bachelor's with the box ticked is
                    3300, unticked is **0**, because every posting is unknown.
                    A checkbox whose only reachable effect is emptying the lane
                    is not a filter. */}
                {UNKNOWN_TOGGLES.filter(([key]) => key !== "degree")
                  .map(([key, label]) => (
                  <label className="jb-check" key={key}>
                    <input type="hidden" name={UNKNOWN_PARAM[key]} value="0" />
                    <input type="checkbox" name={UNKNOWN_PARAM[key]} value="1"
                           defaultChecked={unknownOn[key] !== false} />
                    <span>unknown {label}</span>
                  </label>))}
                <span className="jb-note">postings that do not state a value
                  are kept; the filters beside these are what narrow them</span>
              </div>
            </>
          ) : (
            <div className="jb-row">
              <p className="jb-note">
                Keyword scope, company include and exclude lists, the
                199-country picker with its workplace and remote toggles,
                experience, degree, role, employment and job type, and the
                unknown-bucket switches. Every one of them narrows a saved
                search, which is a write, so they arrive when the parity gate
                closes and v2 stops being read-only. The date window, direct
                listings and the sort above work now, and they filter the whole
                pool rather than this page.
              </p>
              <p className="jb-note">degree, role and job type are not extracted
                yet, so those three will filter the unknown bucket honestly
                rather than return nothing</p>
            </div>
          )}
        </div>
      </details>
      </form>
      {/* **The actions row is gone, and both of its controls with it.**
          Save search was a button for a thing that should never have needed
          one: the filters you are looking at are the search, so they are saved
          when they change rather than when you remember to press something.
          `SaveFilters` does that. And the second Clear was the same control as
          the one in the filter row, three hundred pixels away, so a reader
          counting Clear buttons found two and could not tell which was which. */}
    </div>
  );
}

/**
 * Paste a posting the sweep did not find.
 *
 * The board's `found_lane.add_link_card()`, structure for structure, sitting
 * where it sits there: after the lane, not inside the filter card.
 *
 * **The control is a write, so it renders disabled carrying its reason.**
 * Adding a link enqueues a sweep, which is exactly the class `live_board`
 * gates. Absent, the reader would think the board cannot do it; dark, they can
 * see what is coming and why it is not here yet.
 */
export function AddLink({ writesEnabled }: { writesEnabled: boolean }) {
  return (
    <div className="jb-addlink">
      <h4>Found something yourself?</h4>
      <p>Paste a posting and the next sweep starts there.</p>
      {/* Not a filter and not in the filter form: it enqueues a posting onto
          the scan queue, which is what the old board did. */}
      <AddPosting enabled={writesEnabled} />
    </div>
  );
}
