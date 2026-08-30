import { MORPH } from "./chrome.generated";
import { AppBar, raw } from "./_ui/Chrome";
import { SelectFace } from "./_ui/SelectFace";
import { ScanBanner, Filters } from "./_ui/FoundSurface";
import { DroppedDock } from "./_ui/Dropped";
import { DroppedOpen } from "./_ui/DroppedOpen";
import { LANES } from "./_ui/fixture";

/**
 * The board. Structure and classes are the board's own; the numbers come from
 * a fixture until (d) reads them from Postgres.
 *
 * Read-only. Nothing here writes.
 */
export const dynamic = "force-dynamic";

export default async function Board({
  searchParams,
}: {
  // A promise since Next 15. Awaited rather than read directly, which the
  // build rejects outright rather than letting it be wrong at runtime.
  searchParams: Promise<{ lane?: string }>;
}) {
  const { lane } = await searchParams;
  const active = LANES.some((l) => l.key === lane) ? (lane as string) : "found";

  return (
    <>
      <AppBar here="home" />
      <div className="wrap">
        <div className="top">
          <div className="hero-copy">
            <h1>Jobby is a free agent.</h1>
            <p className="lede">
              {LANES.find((l) => l.key === "act")!.count} waiting on you
            </p>
            <p className="stamp">Sahil &middot; a fixture, until the next piece</p>
          </div>
          <div className="jb-morph" role="button" tabIndex={0}
               aria-label="Replay the Jobby mark" {...raw(MORPH)} />
        </div>

        <div className="segwrap">
          <div className="seg" role="tablist">
            {LANES.map((lane) => (
              <a
                key={lane.key}
                className={lane.key === active ? "tab on" : "tab"}
                href={`/?lane=${lane.key}`}
                role="tab"
                aria-selected={lane.key === active}
                aria-controls={`p-${lane.key}`}
              >
                {lane.label}
                <span className="n">{lane.count.toLocaleString()}</span>
              </a>
            ))}
          </div>
        </div>

        <div className="panel on" id={`p-${active}`} role="tabpanel">
          {active === "found" ? (
            <>
              <ScanBanner />
              <Filters />
            </>
          ) : null}
          <div className="empty">
            Rows arrive with Postgres in the next piece.
          </div>
        </div>
      </div>

      <DroppedDock />
      <SelectFace />
      <DroppedOpen />
    </>
  );
}
