import { LOCKUP, DEFS, MORPH } from "./chrome.generated";

/**
 * The v2 board, structurally identical to the board it replaces.
 *
 * Class names are the board's own -- `appbar`, `wrap`, `top`, `segwrap`,
 * `seg`, `tab`, `panel` -- so `apps/web/app/board.css`, which is generated
 * from `dashboard.py`, styles this markup without a single rule written here.
 * Anything that needs a new class needs it in `premium_ui.py` first.
 *
 * **The three widths.** `--shell` (1800px) is the app bar and the wrap. The
 * hero and the tab strip stop at 1360. The lane fills the wrap. That
 * difference is most of why the board reads as a board rather than a document,
 * and flattening it to one width is what made the first v2 scaffold look like
 * a different product.
 *
 * Read-only. Nothing here writes, and nothing will while the parity clock runs.
 */
export const dynamic = "force-dynamic";

const LANES = [
  { key: "found", label: "Found" },
  { key: "watch", label: "Watching" },
  { key: "flight", label: "In flight" },
  { key: "act", label: "Needs you" },
  { key: "done", label: "Submitted" },
] as const;

const raw = (html: string) => ({ dangerouslySetInnerHTML: { __html: html } });

export default function Board() {
  const active = "found";
  return (
    <>
      <span {...raw(DEFS)} />

      <header className="appbar">
        <a className="brand" href="/" aria-label="Jobby home" {...raw(LOCKUP)} />
        <nav className="primary-nav">
          <a href="/" aria-current="page">Home</a>
          <a href="/profile">Profile</a>
          <a href="/settings">Settings</a>
        </nav>
      </header>

      <div className="wrap">
        <div className="top">
          <div className="hero-copy">
            <h1>Jobby is a free agent.</h1>
            <p className="lede">Nothing blocked</p>
            <p className="stamp">Reading from Postgres</p>
          </div>
          {/* The plate and the mascot together: the linework is one colour,
              so a dark theme inks it dark and needs the cream oval behind it.
              The SVG alone is a mascot that is present and invisible. */}
          <div
            className="jb-morph"
            role="button"
            tabIndex={0}
            aria-label="Replay the Jobby mark"
            {...raw(MORPH)}
          />
        </div>

        <div className="segwrap">
          <div className="seg" role="tablist">
            {LANES.map((lane) => (
              <button
                key={lane.key}
                className={lane.key === active ? "tab on" : "tab"}
                data-tab={lane.key}
                role="tab"
                aria-selected={lane.key === active}
                aria-controls={`p-${lane.key}`}
              >
                {lane.label}
                <span className="n">0</span>
              </button>
            ))}
          </div>
        </div>

        <div className="panel on" id={`p-${active}`} role="tabpanel">
          <p className="note">
            Structure and widths only. Lanes read Postgres in the next piece.
          </p>
          <div className="empty">No rows yet.</div>
        </div>
      </div>
    </>
  );
}
