import { MORPH } from "./chrome.generated";
import { HeroIntro } from "./_ui/HeroIntro";
import { LANES, SORT_LABELS, type Lane, type Sort } from "./lanes.generated";
import { AppBar, raw } from "./_ui/Chrome";
import { SelectFace } from "./_ui/SelectFace";
import { ScanBanner, Filters } from "./_ui/FoundSurface";
import { DroppedDock } from "./_ui/Dropped";
import { DroppedOpen } from "./_ui/DroppedOpen";
import { LaneList } from "./_ui/LaneList";
import { counts, page } from "./_ui/db";

/**
 * The board, reading Postgres.
 *
 * **Read-only.** Nothing on this route writes, and nothing will until the
 * parity gate closes: a new write path is one parity does not know about, and
 * the file store would fall behind on exactly the records it touched.
 *
 * The first page is server-rendered so the lane is there on first paint; the
 * rest arrives through /api/lane on the same keyset. `LaneList` then windows
 * what has arrived, so neither the wire nor the DOM ever carries the corpus.
 */
export const dynamic = "force-dynamic";

const TAB_ORDER: Lane[] = ["found", "watch", "flight", "act", "done"];

export default async function Board({
  searchParams,
}: {
  searchParams: Promise<{ lane?: string; sort?: string; q?: string;
                          within?: string; direct?: string }>;
}) {
  const { lane: asked, sort: askedSort, q, within, direct } =
    await searchParams;
  const active: Lane = (TAB_ORDER as string[]).includes(asked ?? "")
    ? (asked as Lane)
    : "found";

  const sort: Sort = (askedSort ?? "") in SORT_LABELS
    ? (askedSort as Sort) : "new";
  // The filters only mean anything on Found; the other lanes ignore them, and
  // carrying them into those counts would make a tab badge disagree with the
  // lane behind it.
  const filters = active === "found"
    ? { q, within, direct: direct === "1" } : undefined;
  const [tally, first] = await Promise.all([
    counts(filters), page(active, null, 50, sort, filters),
  ]);

  return (
    <>
      <AppBar here="home" />
      <div className="wrap">
        <div className="top">
          <div className="hero-copy">
            <h1>Jobby is a free agent.</h1>
            <p className="lede">
              {tally.act === 0 ? "Nothing blocked"
                               : `${tally.act} waiting on you`}
            </p>
            <p className="stamp">Reading Postgres directly</p>
          </div>
          <div className="jb-morph" role="button" tabIndex={0}
               aria-label="Replay the Jobby mark" {...raw(MORPH)} />
          <HeroIntro />
        </div>

        <div className="segwrap">
          <div className="seg" role="tablist">
            {TAB_ORDER.map((lane) => (
              <a
                key={lane}
                className={lane === active ? "tab on" : "tab"}
                href={`/?lane=${lane}`}
                role="tab"
                aria-selected={lane === active}
                aria-controls={`p-${lane}`}
              >
                {LANES[lane].label}
                <span className="n">{tally[lane].toLocaleString()}</span>
              </a>
            ))}
          </div>
        </div>

        <div className="panel on" id={`p-${active}`} role="tabpanel">
          {active === "found" ? (
            <>
              <ScanBanner />
              <Filters q={q} within={within} direct={direct === "1"}
                       sort={sort} shown={first.rows.length}
                       total={tally[active]} />
            </>
          ) : null}
          {first.rows.length === 0 ? (
            <div className="empty">Nothing in this lane.</div>
          ) : (
            <LaneList
              lane={active}
              initial={first.rows}
              initialCursor={first.next}
              done={first.done}
              shape={active}
            />
          )}
        </div>
      </div>

      <DroppedDock count={tally.dropped} />
      <SelectFace />
      <DroppedOpen />
    </>
  );
}
