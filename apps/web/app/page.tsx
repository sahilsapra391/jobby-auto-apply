/**
 * The /v2/ scaffold. Read-only, and it stays read-only: step 3 gives these
 * lanes real data from Postgres, and no route under apps/web writes anything
 * while the Phase 1 parity clock is running. A new write path is a third
 * writer the parity check does not know about.
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
          <a className="jb-pill" href="/v2/" aria-current="page">
            Home
          </a>
          <a className="jb-pill" href="/v2/settings">
            Settings
          </a>
        </nav>
      </header>

      <div className="jb-tabs" role="tablist">
        {LANES.map((lane, i) => (
          <a
            key={lane.key}
            className="jb-tab"
            href={`/v2/?lane=${lane.key}`}
            {...(i === 0 ? { "aria-current": "page" as const } : {})}
          >
            {lane.label}
          </a>
        ))}
      </div>

      <section className="jb-panel">
        <p className="jb-note">
          Scaffold only. This route reads nothing yet and writes nothing ever.
          Lane data arrives in step 3, from Postgres, a page at a time.
        </p>
        <div className="jb-empty">No rows yet.</div>
      </section>
    </div>
  );
}
