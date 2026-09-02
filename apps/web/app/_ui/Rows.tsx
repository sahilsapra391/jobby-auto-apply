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
import { Act } from "./actions";

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
function CompanyMark({ name, domain }: { name: string | null; domain: string | null }) {
  return (
    <span className="company-mark" aria-hidden="true">
      <span>{initial(name)}</span>
      {domain ? (
        <img src={`/api/company-logo?domain=${encodeURIComponent(domain)}`}
             alt="" loading="lazy" data-company-logo />
      ) : null}
    </span>
  );
}

/** `posted <n> days ago`, or nothing. Calendar days in UTC, like the board:
 *  comparing against a local clock made a morning posting say "yesterday". */
function ago(posted: unknown) {
  const when = s(posted);
  if (!when) return null;
  const day = when.slice(0, 10);
  const days = Math.round(
    (Date.parse(new Date().toISOString().slice(0, 10) + "T00:00:00Z")
      - Date.parse(day + "T00:00:00Z")) / 86400000);
  if (Number.isNaN(days)) return null;
  return "posted " + (days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`);
}

function years(row: Row) {
  const lo = row.years_min as number | null, hi = row.years_max as number | null;
  if (lo == null) return null;
  return hi != null && hi !== lo ? `${lo}–${hi} years` : `${lo}+ years`;
}

const day = (v: unknown) => (s(v) ?? "").slice(0, 10);

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
  chips.push([s(row.source) ?? "Unknown source", ""]);
  for (const label of gateLabels(row.score_gates)) chips.push([label, "gate"]);

  return (
    <article className="jb-card" data-id={row.id}>
      <div className="jb-head">
        <div className="jb-identity">
          <CompanyMark name={employer} domain={s(row.employer_domain)} />
          <div>
            <p className="jb-co">{employer ?? "Unknown employer"}</p>
            <h3 className="jb-ti">{s(row.title)}</h3>
          </div>
        </div>
        <Ring score={row.score} />
      </div>
      {s(row.summary) ? <p className="jb-sum">{s(row.summary)}</p> : null}
      <div className="jb-chips">
        {chips.map(([text, kind], i) =>
          <span className={kind ? `chip ${kind}` : "chip"} key={i}>{text}</span>)}
      </div>
      <Facts score={row.score} parts={row.score_parts} />
      <div className="jb-acts">
        {/* The subject is the posting: nothing has an application id yet, and
            the decision is what takes the card out of Found. */}
        <Act className="jb-btn" subject={row.id} rowId={row.id}
             title="Keep this one and stop showing it in Found"
             body={{ postingId: row.id, decision: "watch" }}>
          Watch
        </Act>
        <Act className="jb-btn primary" subject={row.id} rowId={row.id}
             title="Start work on this one"
             body={{ postingId: row.id, decision: "apply" }}>
          Apply
        </Act>
        <Act className="jb-btn danger" subject={row.id} rowId={row.id}
             title="Never offer this one again"
             body={{ postingId: row.id, decision: "pass" }}>
          Pass
        </Act>
        {s(row.apply_url) ? (
          <a className="jb-btn" href={s(row.apply_url)!} target="_blank" rel="noopener">
            <span>Posting</span>
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
      <td className="when">{day(row.posted_at) || <Muted>unknown</Muted>}</td>
      <td>{years(row) ?? <Muted>not stated</Muted>}</td>
      <td className="loc">{s(row.location_raw) ?? ""}</td>
      <td className="skills"><Muted>not captured</Muted></td>
      <td className="note-cell"></td>
      <td>
        <div className="rowacts">
          <div className="rowacts-btns">
            <Act className="btn apply" subject={row.id} rowId={row.id}
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
      <td className="when">{day(row.updated_at)}</td>
      <td><Muted>not recorded</Muted></td>
      <td><Muted>not recorded</Muted></td>
      <td><Muted>not recorded</Muted></td>
      <td><Muted>not recorded</Muted></td>
      <td><Muted>not recorded</Muted></td>
    </tr>
  );
}

/* --------------------------------------------------------- Dropped (table) */

export function DroppedRow(row: Row) {
  return (
    <tr data-id={row.id} data-state={s(row.status) ?? ""}>
      <td><NameCell row={row} /></td>
      <td><span className="chip lane">{s(row.status) ?? ""}</span></td>
      <td className="note-cell"></td>
      <td className="when">{day(row.updated_at)}</td>
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
        <span className="chip hold">{s(row.phase) ?? "waiting"}</span>
      </header>
      {/* The board lists the blockers here, one <li> per pendingField. Those
          live on the session and §3 does not mirror them, so the list renders
          with the reason rather than being dropped: an absent <ul class="todo">
          changes the card's shape, and the shape is the thing being matched. */}
      <ul className="todo"><li><Muted>blockers are not mirrored yet</Muted></li></ul>
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
  const company = s(row.company);
  const role = s(row.role);
  const linked = row.posting_id != null && company != null;
  const heading = linked ? company! : (s(row.legacy_id) ?? "");
  return (
    <div className="company-title">
      <CompanyMark name={heading} domain={linked ? s(row.employer_domain) : null} />
      <div>
        <h3>{heading}</h3>
        <p className="role">
          {linked && role ? role : <Muted>posting not linked</Muted>}
        </p>
      </div>
    </div>
  );
}


function NameCell({ row, plain }: { row: Row; plain?: boolean }) {
  const company = s(row.company);
  const role = s(row.role);
  const linked = row.posting_id != null && company != null;
  const heading = linked ? company! : (s(row.legacy_id) ?? "");
  return (
    <div className="company-cell">
      <CompanyMark name={heading} domain={linked ? s(row.employer_domain) : null} />
      {plain ? <span>{heading}</span> : (
        <span>
          <strong>{heading}</strong><br />
          {linked && role ? <span className="muted">{role}</span>
                          : <Muted>posting not linked</Muted>}
        </span>
      )}
    </div>
  );
}

/** Kept so an older import does not break; every lane now names its shape. */
export const ApplicationRow = ActCard;
