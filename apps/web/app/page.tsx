import Link from "next/link";
import { cookies } from "next/headers";
import { MORPH } from "./chrome.generated";
import { HeroIntro } from "./_ui/HeroIntro";
import { LANES, SORT_LABELS, type Lane, type Sort } from "./lanes.generated";
import { AppBar, raw } from "./_ui/Chrome";
import { SelectFace } from "./_ui/SelectFace";
import { ScanBanner, Filters } from "./_ui/FoundSurface";
import { DroppedDock } from "./_ui/Dropped";
import { DroppedOpen } from "./_ui/DroppedOpen";
import { LaneList } from "./_ui/LaneList";
import {
  accountId, boardMeta, counts, laneQuery, locationScope, page,
} from "./_ui/db";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "./_ui/theme";
import { RememberLane } from "./_ui/RememberLane";
import { writesEnabled } from "./_ui/writes";

/**
 * The board, reading Postgres.
 *
 * **The render is read-only, and the controls are dark until `live_board`
 * says otherwise.** The flag is read here, on the server, and travels down as
 * a prop. It decides whether a control is *offered*: the write route reads the
 * same flag against the same database before it touches anything, so a client
 * that ignores this prop gets a 503 and a card that goes back where it was.
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

  // **Match %, not Newest.** The old board leads with Okta 97, Axon 96,
  // Cohere 96; v2 led with whatever a sweep found in the last hour, which on a
  // corpus that ingests everything is warehouse shifts and accountancy. The
  // ordering by date is still one click away and still puts undated postings
  // last, which `nulls last` in the generated SQL already guarantees.
  const sort: Sort = (askedSort ?? "") in SORT_LABELS
    ? (askedSort as Sort) : "score";
  // The filters only mean anything on Found; the other lanes ignore them, and
  // carrying them into those counts would make a tab badge disagree with the
  // lane behind it.
  const filters = active === "found"
    ? { q, within, direct: direct === "1" } : undefined;
  const jar = await cookies();
  const theme = themeFrom(jar.get(THEME_COOKIE)?.value);
  const [tally, first, writes, meta, pool, scope] = await Promise.all([
    counts(filters), page(active, null, 50, sort, filters),
    accountId().then(writesEnabled), boardMeta(),
    // The unfiltered pool, so the count can say what it matched against.
    active === "found" && filters ? counts().then((t) => t.found) : undefined,
    active === "found" ? locationScope() : undefined,
  ]);

  /**
   * A tab's link, carrying everything the reader had set.
   *
   * **The filters travel or they are lost**, which is item 4: a 7-day window
   * on Found came back as 30 days after a trip through Submitted, because the
   * tab href was `/?lane=found` and nothing else. The sort travels for the
   * same reason. They are only meaningful on Found, and they are carried
   * anyway: dropping them on the way out means they cannot come back.
   */
  const tabHref = (lane: Lane) => {
    const p = new URLSearchParams({ lane });
    if (askedSort) p.set("sort", askedSort);
    if (q) p.set("q", q);
    if (within) p.set("within", within);
    if (direct === "1") p.set("direct", "1");
    return `/?${p}`;
  };

  return (
    <>
      {/* The logo returns to the board, and the board means the lane the
          reader was last on rather than whichever one is first. */}
      <AppBar here="home" theme={theme} home={tabHref(active)} />
      <RememberLane lane={active} />
      <div className="wrap">
        <div className="top">
          <div className="hero-copy">
            <h1>Jobby is a free agent.</h1>
            <p className="lede">
              {tally.act === 0 ? "Nothing blocked"
                               : `${tally.act} waiting on you`}
            </p>
            {/* **A board that is not live has to look different from one
                that is.** The failure mode of a silent live board is that it
                looks exactly like a working one and is simply wrong, which is
                this repository's oldest shape. */}
            <p className="stamp">
              {writes ? "Live" : "Reading Postgres directly"}
            </p>
          </div>
          <div className="jb-morph" role="button" tabIndex={0}
               aria-label="Replay the Jobby mark" {...raw(MORPH)} />
          <HeroIntro />
        </div>

        <div className="segwrap">
          <div className="seg" role="tablist">
            {TAB_ORDER.map((lane) => (
              // `Link`, not `<a>`: a tab was a full document load, so the
              // content went blank for a second, the hero re-rendered and the
              // mascot replayed its draw-on every time. A client navigation
              // swaps the panel and leaves the layout mounted.
              <Link
                key={lane}
                className={lane === active ? "tab on" : "tab"}
                href={tabHref(lane)}
                role="tab"
                aria-selected={lane === active}
                aria-controls={`p-${lane}`}
                scroll={false}
              >
                {LANES[lane].label}
                <span className="n">{tally[lane].toLocaleString()}</span>
              </Link>
            ))}
          </div>
        </div>

        <div className="panel on" id={`p-${active}`} role="tabpanel">
          {active === "found" ? (
            <>
              <ScanBanner meta={meta} />
              <Filters q={q} within={within} direct={direct === "1"}
                       sort={sort} shown={first.rows.length}
                       total={tally[active]} pool={pool} scope={scope ?? undefined} />
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
              query={laneQuery(active, sort, filters)}
              writesEnabled={writes}
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
