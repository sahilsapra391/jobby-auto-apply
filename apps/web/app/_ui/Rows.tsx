/**
 * How one row of each lane shape draws.
 *
 * The board's own classes: `.jb-card` for a Found posting, `.card` for an
 * application. No action buttons -- Watch, Apply and Pass are writes, and
 * v2 writes nothing until the parity gate closes.
 */
type Row = Record<string, unknown> & { id: string };

const s = (v: unknown) => (v == null ? null : String(v));

function monogram(name: string | null) {
  if (!name) return "?";
  const words = name.split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

function when(value: unknown) {
  const raw = s(value);
  if (!raw) return "Posted date unknown";   // never substitute firstSeenAt
  return new Date(raw).toISOString().slice(0, 10);
}

export function PostingRow(row: Row) {
  const employer = s(row.employer);
  const years = row.years_min != null
    ? `${row.years_min}${row.years_max != null ? `–${row.years_max}` : "+"} years`
    : null;
  return (
    <div className="jb-card">
      <div className="jb-head">
        <div className="jb-identity">
          <span className="company-mark">{monogram(employer)}</span>
          <div>
            <strong>{employer ?? "Unknown employer"}</strong>
            <div className="jb-meta">{s(row.title)}</div>
          </div>
        </div>
      </div>
      <div className="jb-sum">{s(row.summary) ?? ""}</div>
      <div className="jb-chips">
        {[s(row.location_raw), years, when(row.posted_at), s(row.source)]
          .filter(Boolean)
          .map((chip) => <span className="chip" key={String(chip)}>{chip}</span>)}
      </div>
    </div>
  );
}

export function ApplicationRow(row: Row) {
  // §3 carries no company or role for an application, and there is nothing
  // to join for one: `roles` has both and is a pre-§3 table no migration
  // creates and nothing maintains. So the row shows its legacy id, which is
  // honest, until `applications.posting_id` exists. See phase3-inputs §10.
  const company = s(row.company);
  const role = s(row.role);
  return (
    <div className="card">
      <div className="jb-identity">
        <span className="company-mark">{monogram(company ?? s(row.legacy_id))}</span>
        <div>
          <strong>{company ?? s(row.legacy_id)}</strong>
          <div className="jb-meta">{role ?? s(row.status)}</div>
        </div>
      </div>
      <div className="jb-chips">
        <span className="chip">{s(row.status)}</span>
        {row.phase ? <span className="chip">{s(row.phase)}</span> : null}
        <span className="chip">updated {when(row.updated_at)}</span>
      </div>
    </div>
  );
}
