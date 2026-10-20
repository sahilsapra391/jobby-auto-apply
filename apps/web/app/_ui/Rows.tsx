/**
 * How one row of each lane draws.
 *
 * **The board draws five shapes, not one.** Found and In flight and Needs you
 * are cards, and three different cards at that; Watching, Submitted and
 * Dropped are tables with their own columns. v2 shipped a single generic card
 * for all six, which is most of why it did not look like Jobby. Every class
 * here is taken from a real `dashboard.build` render, and
 * `tests/test_v2_lane_shapes.py` compares the two rather than restating them.
 *
 * **Every control here goes through `<Act>`, and `<Act>` is dark while
 * `live_board` is off.** The flag is read on the server and travels down; it
 * decides whether a control is *offered*. It is not authorisation, because the
 * route reads the same flag against the same database before it touches
 * anything, so a client that ignores this gets a 503 and a card that goes back
 * where it was.
 */
import { COMPONENTS, OUTCOME_CHIPS, WEIGHTS } from "../rubric";
import { CardDetails } from "./CardDetails";
import { Act, ActionIcon, ApplyWithConfirm } from "./actions";
import { droppedFromLabel } from "./droppedLanes";
import { cardTags, sourceLabel, tidyDays } from "./cardWords";

type Row = Record<string, unknown> & { id: string };

const s = (v: unknown) => (v == null ? null : String(v));

/**
 * `clock.clock_label`: `16:00:07 EDT`, or nothing at all.
 *
 * Empty rather than a guess for an unreadable stamp, because the span it fills
 * sits at the end of a row of buttons and a wrong time there is a reader
 * deciding a run is stuck when it is not.
 */
const clockLabel = (v: unknown) => {
  const raw = s(v);
  if (!raw) return "";
  const when = new Date(raw);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hour12: false, timeZoneName: "short",
  });
};

/**
 * Which of the five steps is lit, and the wording under them.
 *
 * **A null phase is "Queued" with step one on**, which is `dashboard.py:739`
 * (`phase = entry.get("phase") or "Queued"`) and not what v2 did. v2 asked
 * `PHASES.indexOf(phase ?? "")`, got -1, and lit none of the five. Measured
 * 2026-09-09: 180 of 208 applications carry no phase at all, and `decide()`
 * inserts `status: "inflight"` with none, so the first card this lane ever
 * draws is guaranteed to hit it. The lane has never held a row, which is the
 * only reason nobody has seen it.
 *
 * The match is on a prefix, like v1's, so an agent's wording does not have to
 * be exact, and **an unrecognised phase falls to step one rather than to -1**
 * -- again v1's rule, `next(..., 0)`. Two stored values are outside the list,
 * `Needs you` (4 rows) and `Failed` (1), and both now render their own word
 * under a strip that shows something.
 */
function phaseStep(phase: unknown): { label: string; at: number } {
  const label = s(phase) || "Queued";
  const key = label.toLowerCase();
  const at = PHASES.findIndex((st) => key.startsWith(st.toLowerCase().slice(0, 8)));
  return { label, at: at === -1 ? 0 : at };
}

const WORKPLACE: Record<string, string> = {
  remote: "Remote", hybrid: "Hybrid", onsite: "On-site",
};

/** The board's own absence idiom: say the posting said nothing, never guess. */
const Muted = ({ children }: { children: React.ReactNode }) =>
  <span className="muted">{children}</span>;

function initial(name: string | null) {
  const c = (name ?? "").trim()[0];
  return (c ?? "?").toUpperCase();
}

/**
 * The company mark. A monogram with the logo layered over it, exactly as the
 * board does it: the image is the enhancement and the letter is what shows
 * when it 404s, which is about one company in six.
 *
 * Only ever a same-origin proxy URL. CLAUDE.md's rule is that an ATS or
 * aggregator favicon is never an employer mark, and the proxy is what enforces
 * it server-side.
 */
function CompanyMark(
  { name, domain, logo, alt }:
  { name: string | null; domain: string | null; logo?: string | null;
    alt?: boolean },
) {
  // **The mirror decided this, not this component.** `company_logos` owns the
  // policy -- which hosts are infrastructure, whether an ATS slug corroborates
  // a company name -- and migration 0013 stores its answer per posting.
  // Deriving it here would be a third implementation after the Python one and
  // the board's own JavaScript half, which `LogoContractTests` already pins
  // against each other.
  //
  // Measured before this: `employers.domain` covers 46.6% of open Found rows,
  // so half the lane drew a monogram where the board it replaces resolves 891
  // of 906. `alt` is the derived case, and it tells the route to try the
  // label's other TLDs.
  const q = new URLSearchParams();
  if (logo) q.set("src", logo);
  else if (domain) {
    q.set("domain", domain);
    if (alt) q.set("alt", "1");
  }
  return (
    <span className="company-mark" aria-hidden="true">
      <span>{initial(name)}</span>
      {q.size ? (
        <img src={`/api/company-logo?${q}`} alt="" loading="lazy"
             data-company-logo />
      ) : null}
    </span>
  );
}

/** `posted <n> days ago`, or nothing. Calendar days in UTC, like the board:
 *  comparing against a local clock made a morning posting say "yesterday". */
/**
 * A calendar date, whatever shape the value arrived in.
 *
 * **`node-postgres` returns a `timestamptz` as a `Date`, and `String(date)` is
 * a locale string.** `String(d).slice(0, 10)` on one is `"Wed Sep 02"`, which
 * then fails to parse, so `ago()` returned null and every Found card said
 * "Posted date unknown" over a posting dated that morning. The Submitted
 * column had the same fault wearing a plausible face: it drew `"Wed Aug 19"`
 * and looked like a date because a truncated locale string does.
 *
 * UTC on purpose. The Found date contract compares calendar days, and reading
 * a timestamp through a local clock made a morning posting say "yesterday".
 */
function isoDay(value: unknown): string | null {
  if (value == null) return null;
  const when = value instanceof Date ? value : new Date(String(value));
  if (Number.isNaN(when.getTime())) {
    // A string that is already a plain date, which the store also produces.
    const text = String(value).trim();
    return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : null;
  }
  return when.toISOString().slice(0, 10);
}

function ago(posted: unknown) {
  const days = daysOld(posted);
  if (days === null) return null;
  return "posted " + (days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`);
}

function years(row: Row) {
  const lo = row.years_min as number | null, hi = row.years_max as number | null;
  if (lo == null) return null;
  return hi != null && hi !== lo ? `${lo}–${hi} years` : `${lo}+ years`;
}

/** How many days ago, or null when there is no date to count from. */
function daysOld(posted: unknown): number | null {
  const on = isoDay(posted);
  if (!on) return null;
  const days = Math.round(
    (Date.parse(new Date().toISOString().slice(0, 10) + "T00:00:00Z")
      - Date.parse(on + "T00:00:00Z")) / 86400000);
  return Number.isNaN(days) ? null : days;
}

/**
 * The Watching table's date cell, which is `dashboard.py`'s `posted_cell`.
 *
 * **Three days is fresh and it is green.** `.posted.fresh` has been in the
 * stylesheet the whole time -- `color:var(--green); font-weight:590` -- and
 * nothing in v2 ever put the class on, so the freshest role in the lane looked
 * exactly like the oldest. The threshold is the board's; its `data-fresh`
 * filter uses seven, and those are deliberately different numbers there too.
 *
 * **The date alone, which is what the board actually renders here.** Its
 * `posted_cell` puts a second line under the date only when the sweep left a
 * free-text note ("~23 hours", "22 days (released)"), and v2 does not mirror
 * that field, so all 18 rows on the board show the bare date. A derived
 * "posted 15 days ago" was tried and is worse than not having it twice over:
 * it repeats the column heading, and it is long enough to force the cell to
 * wrap, which broke the ISO date across two lines.
 */
function PostedCell({ posted }: { posted: unknown }) {
  const on = isoDay(posted);
  if (!on) return <Muted>unknown</Muted>;
  const days = daysOld(posted);
  const fresh = days !== null && days <= 3;
  return <span className={fresh ? "posted fresh" : "posted"}>{on}</span>;
}

/**
 * The experience cell, which is `dashboard.py`'s `years_cell`.
 *
 * **Above five years is worth seeing at a glance**, which is what `.yrs.over`
 * is for and what nothing was setting. The board reads `yearsMin` from the
 * session; v2 reads the posting's own `years_min`, which is the fuller of the
 * two -- measured 2026-09-02, CoreWeave and Turing both carry `5+ years` on
 * the posting and nothing at all on the session, so the board says "not
 * stated" for two roles whose descriptions state it.
 */
function YearsCell({ row }: { row: Row }) {
  const text = years(row);
  if (!text) return <Muted>not stated</Muted>;
  const lo = row.years_min as number | null;
  return <span className={lo != null && lo > 5 ? "yrs over" : "yrs"}>{text}</span>;
}

/** The When column. Same normaliser, same reason. */
const day = (v: unknown) => isoDay(v) ?? "";

/**
 * The board's `link()`: a posting, or the words for not having one.
 *
 * `.go` and the north-east arrow are the board's own, and the Watching column
 * is sized for exactly that string.
 */
function Posting({ url, label = "View posting" }: { url: unknown; label?: string }) {
  const href = s(url);
  if (!href) return <span className="muted">no link</span>;
  return (
    <a className="go" href={href} target="_blank" rel="noopener">
      {label} &#8599;
    </a>
  );
}

/* ------------------------------------------------------------------ Found */

export function PostingRow(row: Row) {
  const employer = s(row.employer);
  const chips: Array<[string, string]> = [];
  const place = s(row.location_raw);
  if (place) chips.push([place, "place"]);
  const wp = s(row.workplace);
  if (wp) chips.push([WORKPLACE[wp] ?? wp, ""]);
  const y = years(row);
  chips.push(y ? [y, "years"] : ["Experience not stated", "unknown"]);
  const when = ago(row.posted_at);
  chips.push(when ? [when, ""] : ["Posted date unknown", "unknown"]);
  const pay = s(row.salary_raw);
  if (pay) chips.push([pay, "pay"]);
  // **How many boards, and whether the source is the employer's own form, are
  // two chips and two colours.** v2 drew the source with `boards`, the green
  // "On N boards" treatment, on every card, so an aggregator copy and an
  // ATS-direct listing looked the same and the count never appeared. The
  // board (`found_lane.py`, the card's chip row) draws "On N boards" green
  // above one board, and the source purple when the best sighting is rank 0
  // or 1 -- ATS-direct or the employer's careers site -- and plain otherwise.
  const boards = typeof row.boards === "number" ? row.boards : 0;
  const rank = typeof row.direct_rank === "number" ? row.direct_rank : 3;
  const source = s(row.source);
  const gates = gateLabels(row.score_gates);

  return (
    <article className="jb-card" data-id={row.id}>
      <div className="jb-head">
        <div className="jb-identity">
          <CompanyMark name={employer}
                       domain={s(row.logo_domain) ?? s(row.employer_domain)}
                       logo={s(row.logo_src) ?? s(row.employer_logo)}
                       alt={row.logo_alt === true} />
          <div>
            <p className="jb-co">{employer ?? "Unknown employer"}</p>
            <h3 className="jb-ti">{s(row.title)}</h3>
          </div>
        </div>
        <Ring score={row.score} />
      </div>
      {s(row.summary) ? <p className="jb-sum">{s(row.summary)}</p> : null}
      {/* **`jb-chip`, not `chip`.** The stylesheet colours `.jb-chip.place`
          lavender, `.jb-chip.years` orange, `.jb-chip.pay` green,
          `.jb-chip.gate` red and `.jb-chip.unknown` a dashed outline; `.chip`
          is the lane pill and carries none of that. The cards rendered plain
          bold text for every tag, which is item 23, and the fix is the class
          name the board already emits. */}
      <div className="jb-chips">
        {chips.map(([text, kind], i) =>
          <span className={kind ? `jb-chip ${kind}` : "jb-chip"} key={i}>{text}</span>)}
        {/* Whole literals rather than through the array above, so the two
            classes this card decides on are classes the reachability check
            can see it set. */}
        {boards > 1
          // One text node, so the words are one string in the HTML.
          ? <span className="jb-chip boards">{"On " + boards + " boards"}</span>
          : null}
        <span className={rank <= 1 ? "jb-chip direct" : "jb-chip"}>
          {source ? sourceLabel(source) : "Unknown source"}
        </span>
        {gates.map((label, i) =>
          <span className="jb-chip gate" key={`g${i}`}>{label}</span>)}
      </div>
      <Facts score={row.score} parts={row.score_parts} why={row.score_why}
             tags={row.tags} />
      <div className="jb-acts">
        {/* The subject is the posting: nothing has an application id yet, and
            the decision is what takes the card out of Found. */}
        <Act className="jb-btn" subject={row.id} rowId={row.id} icon="watch"
             title="Keep this one and stop showing it in Found"
             body={{ postingId: row.id, decision: "watch" }}>
          Watch
        </Act>
        {/* **Apply asks first, and the question names the job.** It is the one
            decision on this card that cannot be taken back: Watch and Pass are
            reversible from Watching and Dropped, and Apply hands a real
            application to an agent that fills a real employer's form under a
            real name. See ApplyWithConfirm for why it moves the target rather
            than relabelling in place. */}
        {/* **The Found select has no `company` and no `role`.** It produces
            `p.title` and `e.canonical_name as employer`; the watch lane is the
            one that aliases both, and this was copied from it. So both props
            were `undefined` on every Found card and the prompt fell back to
            "Apply to this posting?" -- the bare are-you-sure that
            `ApplyWithConfirm`'s own docstring rejects as catching nothing, on
            the one control that cannot be taken back. */}
        <ApplyWithConfirm subject={row.id} rowId={row.id}
                          company={s(row.employer) ?? undefined}
                          role={s(row.title) ?? undefined}
                          body={{ postingId: row.id, decision: "apply" }} />
        <Act className="jb-btn danger" subject={row.id} rowId={row.id} icon="pass"
             title="Never offer this one again"
             body={{ postingId: row.id, decision: "pass" }}>
          Pass
        </Act>
        {s(row.apply_url) ? (
          <a className="jb-btn" href={s(row.apply_url)!} target="_blank" rel="noopener">
            <ActionIcon name="posting" /><span>Posting</span>
          </a>
        ) : null}
      </div>
      {/* **Item 22, and it goes after the actions rather than inside them.**
          The board puts `details` in the action row as a sixth pill, which is
          what made it read as an afterthought there; here the grid already
          gives `.jb-det` its own row and `.jb-slot` the full width beneath it
          (`board.css` rows 3 and 4 of the card grid), so the markup lands
          where the stylesheet has been expecting it since the day it arrived. */}
      <CardDetails id={row.id} unknowns={row.score_unknowns}
                   sightings={sightings(row.sightings)} />
    </article>
  );
}

/**
 * The match percentage, as the board draws it: a conic gradient behind the
 * number, green at 75, blue at 50, grey below.
 *
 * **Absent rather than zero when there is no score.** 22 of 21,890 pool
 * records carry none because the scorer has not reached them, and a ring
 * reading 0% is a wrong number rather than a missing one.
 */
function Ring({ score }: { score: unknown }) {
  if (typeof score !== "number") return null;
  const colour = score >= 75 ? "var(--green)"
               : score >= 50 ? "var(--blue)" : "var(--ink-3)";
  return (
    <div className="jb-ring"
         style={{ background: `conic-gradient(${colour} ${score}%, var(--sunk) 0)` }}>
      <i>{score}%</i>
    </div>
  );
}

/**
 * Why that percentage: one bar per rubric component, earned over possible.
 *
 * COMPONENTS and WEIGHTS live in `rubric.ts` and must equal
 * `match_score.WEIGHTS` (`tests/test_v2_rubric_matches_the_score.py`), because
 * "possible" is the weight and a board that disagrees about what a title match
 * is worth disagrees silently, in a bar that still renders.
 */
function Facts({ score, parts, why, tags }: {
  score: unknown; parts: unknown;
  /** `job_scores.why`: the rubric's own sentence for each component. */
  why?: unknown;
  /** `postings.tags`, which the board draws after the bars. */
  tags?: unknown;
}) {
  const labels = cardTags(tags);
  const scored = typeof score === "number";
  if (!scored && !labels.length) return null;
  const earnedBy = (parts && typeof parts === "object" ? parts : {}) as
    Record<string, number>;
  const reasons = (why && typeof why === "object" ? why : {}) as
    Record<string, unknown>;
  return (
    <div className="jb-facts">
      {scored ? COMPONENTS.map((name) => {
        const possible = WEIGHTS[name] ?? 0;
        if (!possible) return null;
        const earned = Number(earnedBy[name] ?? 0);
        const width = Math.round((100 * earned) / possible);
        // **The reason is the point of the tooltip.** "title: 0 of 35" says
        // a component scored nothing; "title: 0 of 35 - no titles in the
        // saved search" says why, which is what the board's title carries
        // (`row.title = name + ': ' + earned + ' of ' + possible + (why ? ' - '
        // + why : '')`). `job_scores.why` has been mirrored the whole time.
        const reason = tidyDays(reasons[name]);
        return (
          <div className="jb-fact" key={name}
               title={`${name}: ${earned} of ${possible}${reason ? ` - ${reason}` : ""}`}>
            <span className="jb-fact-name">{name}</span>
            <span className="jb-fact-track">
              <span className={earned === 0 ? "jb-fact-fill none" : "jb-fact-fill"}
                    style={{ width: `${width}%` }} />
            </span>
            <span className="jb-fact-num">{earned}/{possible}</span>
          </div>
        );
      }) : null}
      {/* The posting's own tags, up to four, after the bars: the board's
          `(job.tg || []).slice(0, 4)`, tidied the way `tidy_tag` tidies
          them. They were selected by nothing until now. */}
      {labels.map((tag, i) => <span className="jb-chip" key={`t${i}`}>{tag}</span>)}
    </div>
  );
}

/**
 * The sightings the details panel links to, from the generated column.
 *
 * Anything that is not a `{s, u}` pair with an http(s) link is dropped here as
 * well as in the SQL, because this is the last thing between a stored string
 * and an `href`.
 */
function sightings(value: unknown): { label: string; url: string }[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((v) => {
    const entry = (v ?? {}) as Record<string, unknown>;
    const url = String(entry.u ?? "").trim();
    if (!/^https?:\/\//.test(url)) return [];
    return [{ label: sourceLabel(entry.s), url }];
  });
}

/** A gate is a disqualifier the rubric found, and the board chips it. */
function gateLabels(gates: unknown): string[] {
  if (!Array.isArray(gates)) return [];
  return gates
    .map((g) => (g && typeof g === "object" ? (g as Record<string, unknown>).label : null))
    .filter((label): label is string => typeof label === "string" && !!label);
}

/* -------------------------------------------------------- Watching (table) */

export function WatchRow(row: Row) {
  return (
    <tr data-id={row.id}>
      {/* The discipline under the role, as the board draws it
          (`discipline_tag`): it is also one of this lane's filter options, so
          a reader who filters on it can see which rows carry it. */}
      <td><NameCell row={row} discipline={s(row.discipline)} /></td>
      <td className="when"><PostedCell posted={row.posted_at} /></td>
      <td><YearsCell row={row} /></td>
      <td className="loc">{s(row.location_raw) ?? ""}</td>
      <td className="skills">{(() => {
        const skills = Array.isArray(row.core_skills)
          ? (row.core_skills as string[]) : [];
        if (!skills.length) return <Muted>not captured</Muted>;
        return skills.slice(0, 4).map((k, i) =>
          <span className="chip" key={i}>{k.replace(/-/g, " ")}</span>);
      })()}</td>
      {/* HOW YOU LINE UP. The whole rubric sentence, which names which
          components were unknown: "Match 89% -- title 35/35, experience
          20/20 ... Rubric only; no fit read written yet." Migration 0012 has
          it as its own column because it is not derivable from the score. */}
      <td className="note-cell">{s(row.alignment) ?? <Muted>not scored</Muted>}</td>
      <td>
        <div className="rowacts">
          {/* The board's own row action: the posting first, then Apply and
              the drop. `link()` renders it as `.go` with a north-east arrow,
              and the column is sized for exactly that. */}
          <Posting url={row.apply_url} />
          <div className="rowacts-btns">
            {/* An application parked in Watching with a submit nobody has
                answered gets the answers rather than Apply, which the device
                refuses in that state for the same reason it refuses Retry. */}
            {row.unresolved_submit === true ? <SubmitAnswers row={row} /> : (
              <Act className="btn apply" subject={row.id} rowId={row.id}
                   title="Start work on this one"
                   body={{ applicationId: s(row.legacy_id), status: "inflight" }}>
                Apply
              </Act>
            )}
            <Act className="xbtn drop" subject={row.id} rowId={row.id}
                 label="Drop this role"
                 body={{ applicationId: s(row.legacy_id), status: "dropped" }}>
              &#215;
            </Act>
          </div>
        </div>
      </td>
    </tr>
  );
}

/* ------------------------------------------------------- Submitted (table) */

export function DoneRow(row: Row) {
  return (
    <tr data-id={row.id} data-state={s(row.outcome) ?? "completed"}>
      <td><NameCell row={row} plain /></td>
      <td>{s(row.role) ?? <Muted>posting not linked</Muted>}</td>
      {/* **The lane is the status here, and that is item 2.** This column used
          to draw applications.status, which for the newest submitted row reads
          "active" -- a session's last-known state, not an outcome. Membership
          of this lane already means a completed event with no later abandoned,
          so the cell says what the lane means: "submitted", until the
          employer answers.

          **And then it says what they answered.** A response, an interview,
          an offer or a rejection is a history event the store records and the
          mirror writes into `application_events`; `outcome` is the latest of
          them. The chip and its colour are `dashboard.py`'s `outcome_chip`,
          generated rather than retyped. Whole literals per colour so the
          reachability check can see each one set. */}
      <td><OutcomeChip outcome={row.outcome} /></td>
      {/* **`completed_at`, never `updated_at`.** The mirror writes
          `updated_at` as `now()` on every pass, so it is the backfill time:
          measured over all 104 submitted applications it differs from the
          completion on every one, by three days to two weeks. */}
      <td className="when">{day(row.completed_at ?? row.updated_at)}</td>
      <td>{s(row.apply_url)
        ? <a href={s(row.apply_url)!} target="_blank" rel="noopener">Posting &#8599;</a>
        : <Muted>not recorded</Muted>}</td>
      <td>{(() => {
        const p = (row.completed_payload ?? {}) as Record<string, unknown>;
        const said = s(p.confirmation);
        // The employer's own words, which is what makes this proof rather
        // than a status. Truncated in the cell and whole in the title.
        // `.chip.ready` is the green one and is what `proof_cell` uses;
        // `.chip.ok` is not a class the stylesheet defines, so this drew as a
        // plain grey chip. `unverified` is the board's `.chip.hold`, and it is
        // a chip rather than muted text because "no confirmation was recorded"
        // is a finding about the application, not an empty cell.
        // **`confirmationUrl` is on 4 rows and nothing read it.** A column
        // nobody reads is how several of the last things hid, and a page the
        // employer served saying the application arrived is the strongest
        // proof this lane can carry -- stronger than the text, which is the
        // agent's transcription of it. So when there is a URL the chip is a
        // link to it, and the employer's own words stay in the title.
        const proofUrl = s(p.confirmationUrl);
        if (said && proofUrl) {
          return (
            <a className="chip ready" href={proofUrl} target="_blank"
               rel="noopener" title={`${said}\n\nOpens the page the employer served`}>
              confirmed &#8599;
            </a>
          );
        }
        if (said) {
          return <span className="chip ready" title={said}>confirmed</span>;
        }
        // **Three states, because "a person says so" is neither of the other
        // two.** `confirmation` is the employer's own words and this cell
        // renders it as proof. `apply_marks.py` used to put the sentence
        // "marked submitted by the user on the board" in that field, so 11 of
        // the 104 completed events drew a green `confirmed` chip whose proof
        // text was a claim about a button press. It writes `confirmedBy` now
        // and no `confirmation`, and the 11 legacy rows are read by the
        // sentence they carry, because that is what they actually say.
        //
        // A fill-only handoff ends here every time: the run cannot submit, the
        // person does, and this is the honest chip for it. Green would claim
        // an employer said something. Grey would claim nobody did.
        const sentinel = "marked submitted by the user on the board";
        if (s(p.confirmedBy) === "user" || s(p.confirmation) === sentinel) {
          return <span className="chip warm"
                       title="Submitted by hand and marked on the board. No page text was captured.">
                   marked
                 </span>;
        }
        return <span className="chip hold"
                    title="No confirmation was recorded for this application">
                 unverified
               </span>;
      })()}</td>
      <td><DocLink row={row} kind="resume" label="Resume"
            value={(row.completed_payload as Record<string, unknown> | null)
                     ?.resumeVariant} /></td>
      <td>{(() => {
        const p = (row.completed_payload ?? {}) as Record<string, unknown>;
        const cover = s(p.coverLetter);
        // **`never recorded`, which is what the Resume column beside it says
        // for the identical condition.** This said "not recorded" and `DocLink`
        // said "never recorded", so one row could carry both words for one
        // fact, three cells apart. Rendered on production, page one: 3 "not
        // recorded" in this column against 2 "never recorded" in that one.
        //
        // `DocLink`'s wording wins because it is the one the four-state
        // comment defines, and because "never" is the honest half: no filename
        // was written, so the document was not made and nothing is missing.
        if (!cover) {
          return <span className="muted"
                       title="No document filename was recorded for this application, so nothing was made and nothing is missing">
                   never recorded
                 </span>;
        }
        // A real letter is a filename; the agent also writes a sentence here
        // when a form had no cover-letter field, and that sentence is a fact
        // worth showing rather than a link worth faking.
        return cover.toLowerCase().endsWith(".pdf")
          ? <DocLink row={row} kind="cover" label="Cover letter" value={cover} />
          : <span className="muted" title={cover}>not accepted</span>;
      })()}</td>
      {/* **Items 36/37, still live on the column beside the ones they fixed.**
          This was a hardcoded `not captured` for all 104, which is true of 87
          of them and a lie about the other 17. The record exists in R2 at
          `app/<legacy_id>/answers.json`; whether it exists is what the one
          prefix listing already answered for the two columns to the left.

          87 of 104 genuinely have none, and that is the documented state
          rather than a gap: no answers survive from before capture existed and
          none are invented, so the salvage recovered 16 of 102 and nothing
          else has a record. Measured 2026-09-03, 17 local record files and 0
          of them missing from the bucket -- the number is low because few
          applications have one, not because the upload is unreliable. */}
      <td>{(() => {
        const list = row.stored_artifacts as string[] | null | undefined;
        const lid = s(row.legacy_id);
        if (list === null || list === undefined) {
          return <span className="muted"
                       title="Artifact storage could not be read, so whether a record exists is unknown">
                   unknown
                 </span>;
        }
        const has = lid && list.includes(`u/account-1/app/${lid}/answers.json`);
        // **A button only where there is a record.** The other four states
        // stay exactly what they were: making every row a button would put an
        // affordance on 87 applications that have nothing behind it, which is
        // the hardcoded "not captured" again wearing a nicer control.
        // **A link, not a button, and its href is the record itself.** Two
        // checks caught the button and both were right. `answers-open` was a
        // class the board's stylesheet does not define, which is a second
        // design system starting; and every `<button>` in a lane must carry
        // `disabled` while writes are gated, which is a rule about controls
        // that a modal-opening button has no honest way to satisfy.
        //
        // An anchor answers both and is better than either fix: the hook is a
        // `data-` attribute rather than a class, `go` is the board's own link
        // treatment, and with no JavaScript the href still opens the record.
        return has ? (
          <a className="go" data-answers=""
             href={`/api/artifact?app=${encodeURIComponent(row.id)}&kind=answers`}
             data-app={row.id} data-company={s(row.company) ?? ""}
             data-role={s(row.role) ?? ""}
             aria-haspopup="dialog" aria-controls="p-answers"
             aria-label="Answers: what this form asked and what went into it">
            record &#8599;
          </a>
        ) : (
          <Muted>not captured</Muted>
        );
      })()}</td>
    </tr>
  );
}

/** The Submitted Status chip: `submitted`, or what the employer answered. */
function OutcomeChip({ outcome }: { outcome: unknown }) {
  const kind = s(outcome) ?? "completed";
  const [tone, label] = OUTCOME_CHIPS[kind] ?? ["", kind];
  return (
    <span className={tone === "warm" ? "chip warm" : tone === "ready" ? "chip ready" : "chip"}>
      {label}
    </span>
  );
}

/* --------------------------------------------------------- Dropped (table) */

export function DroppedRow(row: Row) {
  return (
    <tr data-id={row.id} data-state={s(row.status) ?? ""}>
      <td><NameCell row={row} /></td>
      {/* Named the way the board names lanes, whichever spelling the store
          recorded: the board's own archive held both "Watching" and "watch". */}
      <td><span className="chip lane">{droppedFromLabel(row.dropped_from)}</span></td>
      {/* The stage, and under it the reason when one was given -- which is
          how the board draws this cell. Only 2 of 58 dropped roles carry a
          reason, and those two are the ones worth being able to look up. */}
      <td className="note-cell">
        {s(row.dropped_stage) ?? <Muted>not recorded</Muted>}
        {s(row.dropped_reason) ? (
          // The board puts a `<br>` here. Without it the two run together as
          // "not startedposting closed by the employer".
          <><br /><span className="muted">{s(row.dropped_reason)}</span></>
        ) : null}
      </td>
      {/* **`dropped_at`, not `updated_at`.** The mirror stamps `updated_at`
          `now()` on every pass, so this column read as today on all 58 rows
          whatever the real date was. Migration 0015 carries the store's own
          `droppedAt`, which it has for every one of them. */}
      <td className="when">{day(row.dropped_at) || <Muted>not recorded</Muted>}</td>
      <td>
        <div className="rowacts-inline">
          {/* The board's `link(posting_url(entry), "Posting")`, beside
              Restore: deciding whether to bring a role back usually starts
              with looking at it again. */}
          <Posting url={row.apply_url} label="Posting" />
          <Act className="btn ghost undrop" subject={row.id} rowId={row.id}
               body={{ applicationId: s(row.legacy_id), status: "watch" }}>
            Restore
          </Act>
        </div>
      </td>
    </tr>
  );
}

/* ------------------------------------------------ Needs you / In flight */

const PHASES = ["Queued", "Building documents", "Filling the form",
                "Submitting", "Verifying"];

export function ActCard(row: Row) {
  const pending = Array.isArray(row.blockers) ? (row.blockers as unknown[]) : [];
  // **A park the agent never started is Jobby's fault, and the card says so.**
  // `run_apply.py` tags a park that made no browser call at all
  // `parkClass: capability`, and clears the tag when a later run reaches the
  // form. The board draws that card red, chipped "system fault", with a note
  // saying the step is not the reader's (`dashboard.py`, the Needs you
  // cards).
  //
  // v2 left this out on the grounds that 0 of 97 sessions carried the key, and
  // that was a true measurement of the wrong thing. The key exists only while
  // the fault is live -- the same run that would clear it is the one that
  // proves the browser works -- so zero is the healthy reading, and the
  // writer is four call sites in `run_apply.py`. The branch runs the next time
  // the browser server dies, which is the one moment a reader needs the card
  // to say whose problem it is. Migration 0025 mirrors it.
  const fault = row.park_class === "capability";
  // `ready` and `hold` are the board's card classes as well as its chips: a
  // card with nothing pending is green, not orange.
  const state = fault ? "fault" : pending.length ? "hold" : "ready";
  return (
    <article className={fault ? "card fault" : pending.length ? "card hold" : "card ready"}
             data-id={row.id} data-state={state}>
      <header>
        <CardTitle row={row} />
        {/* **What is left to do, not where the agent stopped.** The pill drew
            `phase`, which is the last stage the run reached -- "Filling the
            form" on a card whose whole point is that the form is filled and
            waiting on a person. The board writes "N to do", or "ready" when
            nothing is pending, and `parkClass: capability` overrides both
            because a park with no browser is Jobby's fault rather than work
            for the reader. */}
        {fault ? <span className="chip fault">system fault</span>
          : pending.length
            ? <span className="chip hold">{pending.length} to do</span>
            : <span className="chip ready">ready</span>}
      </header>
      {fault ? (
        <p className="fault-note">Nothing was filled: the agent started
          without browser tools. This is a fault in Jobby, not a step for
          you. Retry once the browser server is healthy.</p>
      ) : null}
      {/* One <li> per pending field, the board's own shape. Migration 0009
          mirrors them; before it this list said "blockers are not mirrored
          yet" on every card, which is the lane's entire purpose missing.

          **These are not questions**, whatever key they arrive under: measured
          across 29 of them, none was question-shaped and every one was a note
          to the reader about what is in the way. So they render as prose and
          never as something to answer. */}
      <ul className="todo">
        {(() => {
          const held = Array.isArray(row.blockers)
            ? (row.blockers as Record<string, unknown>[]) : [];
          if (!held.length) return <li><Muted>nothing recorded</Muted></li>;
          return held.map((b, i) => (
            <li key={i}>
              {s(b.question) ?? s(b.field) ?? s(b.blocker) ?? ""}
              {s(b.reason) ?? s(b.why) ?? s(b.action) ? (
                <span className="muted"> {s(b.reason) ?? s(b.why) ?? s(b.action)}</span>
              ) : null}
            </li>
          ));
        })()}
      </ul>
      <div className="act">
        {/* **A submit nobody has answered gets the two answers and no Retry.**
            The device refuses Retry while the latest event is `submitting`, so
            an enabled Retry here is a button that does nothing. Open & finish
            goes too: its re-fill is fill-only, which `do_not_start` lets past
            a `submitting`, and putting the same form back in front of a person
            who may already have sent it is how it goes in twice. */}
        {row.unresolved_submit === true ? <SubmitAnswers row={row} /> : (<>
        <Act className="btn retry" subject={row.id} rowId={row.id} keep
             body={{ applicationId: s(row.legacy_id), status: "inflight", retry: true }}>
          Retry
        </Act>
        {/* **The lane's primary action, and it was rendered disabled.** A
            Needs You card exists to hand a blocked application back to the
            person; a card whose blocker is "enter the emailed code" with no
            way to reach the form is a lane that cannot do its one job. It sat
            dark saying this was "not something v2 can do", which was never
            true: `watch.py:2432` has always claimed the `focus` queue, and it
            relaunches the slot with a window (`browser.show`) before opening
            the page, because the slots run `--headless=new`.

            Found by a real block rather than by review: a Chainguard run
            parked on a Greenhouse verification code on 2026-09-07 and there
            was no way to reach the form. */}
        <Act className="btn ghost finish" subject={row.id} rowId={row.id} keep
             title="Bring up the browser on your Mac showing this form"
             body={{ applicationId: s(row.legacy_id), action: "focus" }}>
          Open &amp; finish
        </Act>
        <Act className="btn mark" subject={row.id} rowId={row.id}
             title="Record that this one went in"
             body={{ applicationId: s(row.legacy_id), event: "completed" }}>
          Confirm submitted
        </Act>
        </>)}
        <Act className="btn drop" subject={row.id} rowId={row.id}
             body={{ applicationId: s(row.legacy_id), status: "dropped" }}>
          Drop
        </Act>
        {/* **One class, two facts, and v2 drew neither.** Here it is the ATS
            the application went through (`dashboard.py:734`), which tells a
            reader which form is waiting for them. The lane has always selected
            `a.ats`; nothing put it on the page. 8 of 24 parked applications
            carry one, and store-wide the values are greenhouse 67, ashby 40,
            lever 11, linkedin 7, workday 9. */}
        <span className="ats">{s(row.ats) ?? ""}</span>
      </div>
    </article>
  );
}

/**
 * The two honest answers to an unresolved submit: it went in, or it did not.
 *
 * Confirm submitted writes `completed` through `apply_marks.py`, the path
 * Needs you has always used. "It did not go through" writes `abandoned` with
 * `confirmedBy: user` through the same script's `--unsent`, and hands the
 * application back to Needs you, where Retry starts a fresh run because the
 * latest event is no longer `submitting`. Stop is not one of them: it is a
 * drop, it writes no event, and the submit stays unresolved for ever.
 */
function SubmitAnswers({ row }: { row: Row }) {
  return (
    <>
      <Act className="btn mark" subject={row.id} rowId={row.id}
           title="Record that this one went in"
           body={{ applicationId: s(row.legacy_id), event: "completed" }}>
        Confirm submitted
      </Act>
      <Act className="btn ghost" subject={row.id} rowId={row.id} keep
           title="Nothing arrived, so nothing was sent; Retry can start a fresh run"
           body={{ applicationId: s(row.legacy_id), event: "abandoned" }}>
        It did not go through
      </Act>
    </>
  );
}

export function FlightCard(row: Row) {
  const { label, at } = phaseStep(row.phase);
  return (
    <article className="card flight" data-id={row.id}>
      <header>
        <CardTitle row={row} />
        <span className="chip warm">in flight</span>
      </header>
      <div className="steps">
        {PHASES.map((name, i) =>
          <span className={i <= at ? "step on" : "step"} title={name} key={name} />)}
      </div>
      {/* **The phase and the sentence saying why, which is `dashboard.py:764`.**
          Three of the document stage's states share the phase "Building
          documents on Your Device" and are told apart only by the detail:
          sending the job, waiting for the worker, or the worker generating.
          A reader watching a run cannot otherwise tell a worker that has not
          picked the job up from one that is working.

          It could not be drawn before migration 0024, because there was no
          `phase_detail` column, `class_b.session_row` did not carry it and
          nothing selected it. Adding the markup without those would have drawn
          an empty span on every card, which is what `.ats` did twice. */}
      <p className="phase">{label}
        {s(row.phase_detail)
          ? <span className="why">{s(row.phase_detail)}</span> : null}</p>
      <div className="act">
        {/* The posting first, as the board's card has it
            (`dashboard.py`'s `flight_card`): a reader watching a run usually
            wants to see what it is applying to. Absent rather than a dead
            link when nothing recorded one. */}
        {s(row.apply_url) ? (
          <a className="btn ghost" href={s(row.apply_url)!} target="_blank"
             rel="noopener">Posting &#8599;</a>
        ) : null}
        {/* **No Retry while a submit is unanswered**, because the device
            refuses it. Nor the two answers: an In flight card with a latest
            `submitting` is usually a run between `pre_submit` and
            `record_completed`, and an `abandoned` written under it would make
            the agent's own confirmation fail its nonce check. The device moves
            the card to Needs you, where the answers are, the moment no runner
            holds it (`run_apply.hand_back_unconfirmed_submit`, and
            `watch.sweep_stuck` for a runner that died). */}
        {row.unresolved_submit === true ? null : (
          <Act className="btn retry" subject={row.id} rowId={row.id} keep
               body={{ applicationId: s(row.legacy_id), status: "inflight", retry: true }}>
            Retry
          </Act>
        )}
        <Act className="btn drop" subject={row.id} rowId={row.id}
             body={{ applicationId: s(row.legacy_id), status: "dropped" }}>
          Stop
        </Act>
        {/* The other fact: when this run last moved, not which ATS it is on.
            **v1 rendered nothing here, and that was a v1 bug rather than a
            decision.** `dashboard.py:774` passes `clock_label` the slice
            `(entry.get("phaseAt") or "")[11:19]`, a bare `"16:00:07"` that
            `clock.parse` cannot read, so the label was always "".

            **`phase_at`, never `updated_at`.** Migration 0025 mirrors the
            stamp `phase.py` writes on every `set_phase`. `updated_at` is the
            mirror's own write time, stamped `now()` on every pass, so a card
            showing it says a run moved when only the mirror did. A run with
            no stamp yet shows nothing, which is what an unread stamp has
            always drawn here. */}
        <span className="ats">{clockLabel(row.phase_at)}</span>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ shared */

/**
 * The company cell both table lanes share.
 *
 * **A linked application shows its posting's company and role. An unlinked one
 * says so, and never shows a blank where a name belongs.**
 *
 * `applications.posting_id` is filled by `scripts/link_applications.py` for
 * the 59% that can be resolved honestly; the rest point at postings that have
 * been pruned, or at aggregator redirects and short links carrying no board
 * identity. Those keep their legacy id, which is what the store actually knows
 * about them, with an explicit "posting not linked" underneath rather than an
 * empty cell that reads as a bug.
 *
 * Still never `roles`: it would name 196 of 204 and is a snapshot nothing
 * maintains. See `docs/plans/phase3-inputs.md` §5.
 */
/** The card lanes' header, on the same rule as the table lanes' cell. */
function CardTitle({ row }: { row: Row }) {
  // Same split as `NameCell`: the name exists whether or not a posting is
  // linked, and `linked` decides only whether there is a posting to open.
  const company = s(row.company);
  const role = s(row.role);
  const heading = company ?? s(row.legacy_id) ?? "";
  return (
    <div className="company-title">
      <CompanyMark name={heading}
                   domain={s(row.logo_domain) ?? s(row.employer_domain)}
                   logo={s(row.logo_src) ?? s(row.employer_logo)}
                   alt={row.logo_alt === true} />
      <div>
        <h3>{heading}</h3>
        <p className="role">
          {role ?? <Muted>posting not linked</Muted>}
        </p>
      </div>
    </div>
  );
}


function NameCell({ row, plain, discipline }: {
  row: Row; plain?: boolean; discipline?: string | null;
}) {
  // **The name and the link are two different facts, and conflating them is
  // why rows 3 and beyond rendered their session id.** `company` and `role`
  // come from the posting when one is linked and from `applications` when it
  // is not; both are real, and migration 0011 explains why the second is now
  // trustworthy. `linked` says whether there is a posting to open, which is
  // the only thing it decides.
  const company = s(row.company);
  const role = s(row.role);
  const linked = row.linked === true || row.posting_id != null;
  const heading = company ?? s(row.legacy_id) ?? "";
  return (
    <div className="company-cell">
      <CompanyMark name={heading}
                   domain={s(row.logo_domain) ?? s(row.employer_domain)}
                   logo={s(row.logo_src) ?? s(row.employer_logo)}
                   alt={row.logo_alt === true} />
      {plain ? <span>{heading}</span> : (
        <span>
          <strong>{heading}</strong><br />
          {role ? <span className="muted">{role}</span>
                : <Muted>posting not linked</Muted>}
          {discipline ? <><br /><span className="chip-sm">{discipline}</span></> : null}
        </span>
      )}
    </div>
  );
}

/**
 * The key `documentSiblings` builds its map with, in the same words.
 *
 * **The map is keyed by the basename and this was looking it up by the whole
 * recorded value.** `documentSiblings` groups on
 * `lower(split_part(payload->>'resumeVariant', '/', -1))`, and 18 of the 100
 * recorded `resumeVariant` values are paths -- `applications/Ramp/
 * Alex_Example_Ramp_Resume.pdf` rather than the bare name. Every one of those
 * missed, `shared` fell back to 1, and the row dropped through to "not
 * stored".
 *
 * Measured on production 2026-09-10: **3** rows are keyed wrongly (Cohere,
 * Datadog, Ramp) and **2** of them visibly change state, because the
 * `stored.has(key)` branch above catches Cohere before this is reached. Both
 * counts are true and they answer different questions: how many lookups are
 * wrong, and how many readers see a wrong word.
 *
 * The word matters more than the count. "not stored" says the document is not
 * in the bucket; "not attributable" says it **is** there and more than one
 * application claims that name. Those are the two facts CLAUDE.md is most
 * careful to keep apart, and the board was saying a document is missing when
 * the truth is that it exists and cannot be tied to one application.
 *
 * `split_part(x, '/', -1)` returns the whole string when there is no
 * separator, which is what `slice(lastIndexOf + 1)` does for -1, so the two
 * agree on a bare filename as well as on a path.
 * `tests/the_sibling_key_is_the_key_it_was_built_with.test.mjs` compares them
 * rather than restating the rule, because this is one contract in two
 * languages and that is how the other two in this repository are held.
 */
function basename(value: string): string {
  return value.slice(value.lastIndexOf("/") + 1).toLowerCase();
}

/** A `/doc/` link for a filename the store recorded, or nothing. */
function DocLink({ row, kind, label, value }: {
  row: Row; kind: "resume" | "cover"; label: string; value: unknown;
}) {
  const app = row.id;
  const legacy = row.legacy_id;
  const list = row.stored_artifacts as string[] | null | undefined;
  const stored = list === null || list === undefined ? null : new Set(list);
  const siblings = new Map<string, number>(
    Object.entries((row.document_siblings ?? {}) as Record<string, number>));
  const name = s(value);
  // **Four states, and they are four different facts.** The column used to
  // have two, one of which was a link that 404ed and the other a grey "not
  // recorded" doing duty for all of it.
  //
  //   never recorded    the agent wrote no filename. 4 of 104. The document
  //                     was not made, so nothing was lost.
  //   unknown           this deployment cannot read the bucket. Says nothing
  //                     about the document.
  //   not attributable  the filename is real and so is the file, and more than
  //                     one application claims that name, so which of them
  //                     received it is not knowable. 14 of 104.
  //   the link          the object is there under this application's own key.
  if (!name) {
    return <span className="muted"
                 title="No document filename was recorded for this application, so nothing was made and nothing is missing">
             never recorded
           </span>;
  }

  const object = kind === "resume" ? "resume.pdf" : "cover.pdf";
  const lid = s(legacy);
  const key = lid ? `u/account-1/app/${lid}/${object}` : null;

  if (stored === null) {
    return <span className="muted" title={`${name} — artifact storage could not be read, so whether this document is stored is unknown`}>unknown</span>;
  }
  if (key && stored.has(key)) {
    // Keyed on the application and the kind, never the filename. Measured
    // 2026-09-03: 129 of the 129 application ids in the bucket match
    // `applications.legacy_id` and 0 match `applications.id`.
    return (
      <a className="go" href={`/api/artifact?app=${encodeURIComponent(app)}&kind=${kind}`}
         target="_blank" rel="noopener" title={name}
         aria-label={`${label}: ${name}`}>
        PDF &#8599;
      </a>
    );
  }
  const shared = siblings.get(basename(name)) ?? 1;
  // **Not at its own key is not the same as not openable.** The documents
  // written before the id went into the filename were indexed by name, and v1
  // opened every one of them through `jobby:doc-index` or its KV copy.
  // `/api/artifact` falls back to that same lookup when the fixed key is
  // absent, so a name `servable_names` lists gets the link. Measured on
  // production 2026-10-20: 14 cells drew "not attributable" over a document
  // the index resolves.
  //
  // A shared name still says it is shared. One file exists under it and the
  // link opens that file, which is what v1 opened; the label and the title are
  // what keep "this is the file under that name" from reading as "this is
  // exactly what this application sent".
  const byName = row.servable_names as string[] | null | undefined;
  const servable = Array.isArray(byName) && byName.includes(name.slice(name.lastIndexOf("/") + 1));
  if (servable) {
    return (
      <a className="go" href={`/api/artifact?app=${encodeURIComponent(app)}&kind=${kind}`}
         target="_blank" rel="noopener"
         title={shared > 1
           ? `${name} is recorded by ${shared} applications to different roles at this company. One file is stored under that name and this opens it; nothing records which application received it.`
           : `${name}: stored under its filename from before documents were keyed by application`}
         aria-label={`${label}: ${name}`}>
        {shared > 1 ? <>shared PDF &#8599;</> : <>PDF &#8599;</>}
      </a>
    );
  }
  if (byName === null) {
    return <span className="muted" title={`${name}: the document index could not be read, so whether this document can be opened is unknown`}>unknown</span>;
  }
  if (shared > 1) {
    return (
      <span className="chip hold"
            title={`${name} is recorded by ${shared} applications to different roles at this company. One file exists under that name and nothing records which application received it, so it is not shown against any of them.`}>
        not attributable
      </span>
    );
  }
  return <span className="muted" title={`${name} — recorded, but no stored copy`}>not stored</span>;
}

/** Kept so an older import does not break; every lane now names its shape. */
export const ApplicationRow = ActCard;
