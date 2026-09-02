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
import { COMPONENTS, WEIGHTS } from "../rubric.generated";
import { Act, ActionIcon } from "./actions";

type Row = Record<string, unknown> & { id: string };

const s = (v: unknown) => (v == null ? null : String(v));

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
function Posting({ url }: { url: unknown }) {
  const href = s(url);
  if (!href) return <span className="muted">no link</span>;
  return (
    <a className="go" href={href} target="_blank" rel="noopener">
      View posting &#8599;
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
  // Green, like the board: the source is where the posting was found and an
  // ATS-direct one is worth more than an aggregator, which the colour says.
  chips.push([s(row.source) ?? "Unknown source", "boards"]);
  for (const label of gateLabels(row.score_gates)) chips.push([label, "gate"]);

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
      </div>
      <Facts score={row.score} parts={row.score_parts} />
      <div className="jb-acts">
        {/* The subject is the posting: nothing has an application id yet, and
            the decision is what takes the card out of Found. */}
        <Act className="jb-btn" subject={row.id} rowId={row.id} icon="watch"
             title="Keep this one and stop showing it in Found"
             body={{ postingId: row.id, decision: "watch" }}>
          Watch
        </Act>
        <Act className="jb-btn primary" subject={row.id} rowId={row.id} icon="apply"
             title="Start work on this one"
             body={{ postingId: row.id, decision: "apply" }}>
          Apply
        </Act>
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
 * COMPONENTS and WEIGHTS are generated from `found_lane.COMPONENTS` and
 * `match_score.WEIGHTS` rather than retyped, because "possible" is the weight
 * and a board that disagrees about what a title match is worth disagrees
 * silently, in a bar that still renders.
 */
function Facts({ score, parts }: { score: unknown; parts: unknown }) {
  if (typeof score !== "number") return null;
  const earnedBy = (parts && typeof parts === "object" ? parts : {}) as
    Record<string, number>;
  return (
    <div className="jb-facts">
      {COMPONENTS.map((name) => {
        const possible = WEIGHTS[name] ?? 0;
        if (!possible) return null;
        const earned = Number(earnedBy[name] ?? 0);
        const width = Math.round((100 * earned) / possible);
        return (
          <div className="jb-fact" key={name}
               title={`${name}: ${earned} of ${possible}`}>
            <span className="jb-fact-name">{name}</span>
            <span className="jb-fact-track">
              <span className={earned === 0 ? "jb-fact-fill none" : "jb-fact-fill"}
                    style={{ width: `${width}%` }} />
            </span>
            <span className="jb-fact-num">{earned}/{possible}</span>
          </div>
        );
      })}
    </div>
  );
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
      <td><NameCell row={row} /></td>
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
            <Act className="btn apply" subject={row.id} rowId={row.id}
                 title="Start work on this one"
                 body={{ applicationId: s(row.legacy_id), status: "inflight" }}>
              Apply
            </Act>
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
    <tr data-id={row.id} data-state="completed">
      <td><NameCell row={row} plain /></td>
      <td>{s(row.role) ?? <Muted>posting not linked</Muted>}</td>
      {/* **The lane is the status here, and that is item 2.** This column used
          to draw applications.status, which for the newest submitted row reads
          "active" -- a session's last-known state, not an outcome. Membership
          of this lane already means a completed event with no later abandoned,
          so the cell says what the lane means. */}
      <td><span className="chip">submitted</span></td>
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
        return said ? <span className="chip ready" title={said}>confirmed</span>
                    : <span className="chip hold"
                            title="No confirmation was recorded for this application">
                        unverified
                      </span>;
      })()}</td>
      <td><DocLink label="Resume"
            value={(row.completed_payload as Record<string, unknown> | null)
                     ?.resumeVariant} /></td>
      <td>{(() => {
        const p = (row.completed_payload ?? {}) as Record<string, unknown>;
        const cover = s(p.coverLetter);
        if (!cover) return <Muted>not recorded</Muted>;
        // A real letter is a filename; the agent also writes a sentence here
        // when a form had no cover-letter field, and that sentence is a fact
        // worth showing rather than a link worth faking.
        return cover.toLowerCase().endsWith(".pdf")
          ? <DocLink label="Cover letter" value={cover} />
          : <span className="muted" title={cover}>not accepted</span>;
      })()}</td>
      <td><Muted>not captured</Muted></td>
    </tr>
  );
}

/* --------------------------------------------------------- Dropped (table) */

export function DroppedRow(row: Row) {
  return (
    <tr data-id={row.id} data-state={s(row.status) ?? ""}>
      <td><NameCell row={row} /></td>
      <td><span className="chip lane">
        {s(row.dropped_from) ?? s(row.status) ?? ""}</span></td>
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
  return (
    <article className="card hold" data-id={row.id} data-state="hold">
      <header>
        <CardTitle row={row} />
        {/* **What is left to do, not where the agent stopped.** The pill drew
            `phase`, which is the last stage the run reached -- "Filling the
            form" on a card whose whole point is that the form is filled and
            waiting on a person. The board writes "N to do", or "ready" when
            nothing is pending, and `parkClass: capability` overrides both
            because a park with no browser is Jobby's fault rather than work
            for the reader. */}
        {/* The board also draws a red "system fault" pill from
            `parkClass: capability`, and that is deliberately absent here:
            measured, 0 of 97 sessions carry `parkClass` at all, because the
            loop that writes it only started recording on a signal and none has
            fired since. A branch over a field nothing populates is a branch
            that can never run, which is worse than not having it. */}
        {(() => {
          const held = Array.isArray(row.blockers)
            ? (row.blockers as unknown[]) : [];
          return held.length
            ? <span className="chip hold">{held.length} to do</span>
            : <span className="chip ready">ready</span>;
        })()}
      </header>
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
        <Act className="btn retry" subject={row.id} rowId={row.id} keep
             body={{ applicationId: s(row.legacy_id), status: "inflight" }}>
          Retry
        </Act>
        {/* **Not a write, and not something v2 can do.** This opens the
            application in the device's own Chrome slot, which is a runner
            action; Phase 4 moves the runner and this control with it. Dark
            with a reason beats a control that posts something else. */}
        <button className="btn ghost finish" type="button" disabled
                title={ON_DEVICE}>
          Open &amp; finish
        </button>
        <Act className="btn mark" subject={row.id} rowId={row.id}
             title="Record that this one went in"
             body={{ applicationId: s(row.legacy_id), event: "completed" }}>
          Confirm submitted
        </Act>
        <Act className="btn drop" subject={row.id} rowId={row.id}
             body={{ applicationId: s(row.legacy_id), status: "dropped" }}>
          Drop
        </Act>
        <span className="ats"></span>
      </div>
    </article>
  );
}

export function FlightCard(row: Row) {
  const at = PHASES.indexOf(s(row.phase) ?? "");
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
      <p className="phase">{s(row.phase) ?? ""}</p>
      <div className="act">
        <Act className="btn retry" subject={row.id} rowId={row.id} keep
             body={{ applicationId: s(row.legacy_id), status: "inflight" }}>
          Retry
        </Act>
        <Act className="btn drop" subject={row.id} rowId={row.id}
             body={{ applicationId: s(row.legacy_id), status: "dropped" }}>
          Stop
        </Act>
        <span className="ats"></span>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ shared */

/** Controls that drive the device rather than the database. */
const ON_DEVICE =
  "This opens a browser on your Mac. The runner moves in Phase 4 and this " +
  "control moves with it.";

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


function NameCell({ row, plain }: { row: Row; plain?: boolean }) {
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
        </span>
      )}
    </div>
  );
}

/** A `/doc/` link for a filename the store recorded, or nothing. */
function DocLink({ value, label }: { value: unknown; label: string }) {
  const name = s(value);
  if (!name) return <Muted>not recorded</Muted>;
  // The store records some of these as a path under `applications/`. `/doc/`
  // resolves by filename through `jobby:doc-index`, so the basename is what it
  // wants; a path would 404 against an index keyed on names.
  const file = name.split("/").pop()!;
  // **"PDF", and the class the board gives it.** `doc_cell` labels every one
  // of these `PDF` under a column already headed Resume or Cover letter, and
  // renders it `.go`, which is what makes it the blue arrow link rather than a
  // default underlined anchor. The `label` is the accessible name instead, so
  // the two links in a row are still told apart by a screen reader.
  return (
    <a className="go" href={`/doc/${encodeURIComponent(file)}`} target="_blank"
       rel="noopener" title={name} aria-label={`${label}: ${file}`}>
      PDF &#8599;
    </a>
  );
}

/** Kept so an older import does not break; every lane now names its shape. */
export const ApplicationRow = ActCard;
