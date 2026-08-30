/**
 * The v2.jobby.fyi board. Read-only, and it stays read-only: step 3 gives
 * these lanes real data from Postgres, and no route under apps/web writes
 * anything while the Phase 1 parity clock is running. A new write path is a
 * third writer the parity check does not know about.
 *
 * Every request here is gated by middleware.ts before this renders. A custom
 * domain is exempt from the team's SSO, so that gate is the only one.
 */
export const dynamic = "force-dynamic";

const LANES = [
  { key: "found", label: "Found" },
  { key: "watching", label: "Watching" },
  { key: "inflight", label: "In flight" },
  { key: "needs_you", label: "Needs you" },
  { key: "submitted", label: "Submitted" },
] as const;

export default function Board() {
  return (
    <div className="jb-wrap">
      <header className="jb-appbar">
        <div className="jb-logo">Jobby</div>
        <nav className="jb-nav">
          <a className="jb-pill" href="/" aria-current="page">
            Home
          </a>
          <a className="jb-pill" href="/settings">
            Settings
          </a>
        </nav>
      </header>

      <div className="jb-tabs" role="tablist">
        {LANES.map((lane, i) => (
          <a
            key={lane.key}
            className="jb-tab"
            href={`/?lane=${lane.key}`}
            {...(i === 0 ? { "aria-current": "page" as const } : {})}
          >
            {lane.label}
          </a>
        ))}
      </div>

      <section className="jb-panel">
        <p className="jb-note">
          Scaffold only. This board reads nothing yet and writes nothing ever.
          Lane data arrives in step 3, from Postgres, a page at a time.
        </p>
        <div className="jb-empty">No rows yet.</div>
      </section>
    </div>
  );
}
