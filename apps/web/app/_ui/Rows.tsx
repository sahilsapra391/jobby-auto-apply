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
 * **No control here does anything.** Watch, Apply, Pass, Retry, Stop, Restore
 * and Confirm submitted are all writes, and v2 writes nothing until the parity
 * gate closes. They render disabled rather than absent so the card is the
 * right shape and the wiring is one step later, not two.
 */
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
        {/* The board puts a .jb-ring here, a conic-gradient of the match
            percentage. It is deliberately absent rather than drawn at 0%:
            postings carries no score column, job_scores does not exist, and
            jobs.score is a frozen snapshot covering 9,286 of 17,339 Found
            rows. A ring reading 0% on every card is not a missing feature, it
            is a wrong number, and a wrong number is worse than a gap. */}
      </div>
      {s(row.summary) ? <p className="jb-sum">{s(row.summary)}</p> : null}
      <div className="jb-chips">
        {chips.map(([text, kind], i) =>
          <span className={kind ? `chip ${kind}` : "chip"} key={i}>{text}</span>)}
      </div>
      <div className="jb-acts">
        <button className="jb-btn" type="button" disabled
                title="Watching is a write. v2 is read-only until the parity gate closes.">
          Watch
        </button>
        <button className="jb-btn primary" type="button" disabled
                title="Applying is a write. v2 is read-only until the parity gate closes.">
          Apply
        </button>
        <button className="jb-btn danger" type="button" disabled
                title="Passing is a write. v2 is read-only until the parity gate closes.">
          Pass
        </button>
        {s(row.apply_url) ? (
          <a className="jb-btn" href={s(row.apply_url)!} target="_blank" rel="noopener">
            <span>Posting</span>
          </a>
        ) : null}
      </div>
    </article>
  );
}

/* -------------------------------------------------------- Watching (table) */

export function WatchRow(row: Row) {
  return (
    <tr data-id={row.id}>
      <td><NameCell row={row} /></td>
      <td className="when">{day(row.updated_at) || <Muted>unknown</Muted>}</td>
      <td><Muted>not stated</Muted></td>
      <td className="loc"></td>
      <td className="skills"><Muted>not captured</Muted></td>
      <td className="note-cell"></td>
      <td>
        <div className="rowacts">
          <div className="rowacts-btns">
            <button className="btn apply" type="button" disabled title={READ_ONLY}>Apply</button>
            <button className="xbtn drop" type="button" disabled title={READ_ONLY}
                    aria-label="Drop this role">&#215;</button>
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
      <td>{<Muted>not recorded</Muted>}</td>
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
          <button className="btn ghost undrop" type="button" disabled title={READ_ONLY}>
            Restore
          </button>
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
        <div className="company-title">
          <CompanyMark name={s(row.legacy_id)} domain={null} />
          <div>
            <h3>{s(row.legacy_id)}</h3>
            <p className="role"><Muted>role not recorded</Muted></p>
          </div>
        </div>
        <span className="chip hold">{s(row.phase) ?? "waiting"}</span>
      </header>
      {/* The board lists the blockers here, one <li> per pendingField. Those
          live on the session and §3 does not mirror them, so the list renders
          with the reason rather than being dropped: an absent <ul class="todo">
          changes the card's shape, and the shape is the thing being matched. */}
      <ul className="todo"><li><Muted>blockers are not mirrored yet</Muted></li></ul>
      <div className="act">
        <button className="btn retry" type="button" disabled title={READ_ONLY}>Retry</button>
        <button className="btn ghost finish" type="button" disabled title={READ_ONLY}>
          Open &amp; finish
        </button>
        <button className="btn mark" type="button" disabled title={READ_ONLY}>
          Confirm submitted
        </button>
        <button className="btn drop" type="button" disabled title={READ_ONLY}>Drop</button>
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
        <div className="company-title">
          <CompanyMark name={s(row.legacy_id)} domain={null} />
          <div>
            <h3>{s(row.legacy_id)}</h3>
            <p className="role"><Muted>role not recorded</Muted></p>
          </div>
        </div>
        <span className="chip warm">in flight</span>
      </header>
      <div className="steps">
        {PHASES.map((name, i) =>
          <span className={i <= at ? "step on" : "step"} title={name} key={name} />)}
      </div>
      <p className="phase">{s(row.phase) ?? ""}</p>
      <div className="act">
        <button className="btn retry" type="button" disabled title={READ_ONLY}>Retry</button>
        <button className="btn drop" type="button" disabled title={READ_ONLY}>Stop</button>
        <span className="ats"></span>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ shared */

const READ_ONLY =
  "v2 is read-only until the parity gate closes. Use the board at jobby.fyi.";

/**
 * The company cell both table lanes share.
 *
 * **It draws the legacy id because there is nothing better to draw**, and that
 * is `applications.posting_id` being NULL for all 204 rows rather than an
 * oversight here. `roles` has a company and a role on the same key and is a
 * frozen snapshot nothing maintains, so joining it would show today's snapshot
 * as the record of what happened. `docs/plans/phase3-inputs.md` §5 measures
 * what a real backfill reaches (125 of 204) and what it would cost.
 */
function NameCell({ row, plain }: { row: Row; plain?: boolean }) {
  const name = s(row.legacy_id) ?? "";
  return (
    <div className="company-cell">
      <CompanyMark name={name} domain={null} />
      {plain ? <span>{name}</span>
             : <span><strong>{name}</strong><br /><Muted>role not recorded</Muted></span>}
    </div>
  );
}

/** Kept so an older import does not break; every lane now names its shape. */
export const ApplicationRow = ActCard;
