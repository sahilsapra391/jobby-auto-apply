import Link from "next/link";
import { cookies } from "next/headers";
import { MORPH } from "./chrome.generated";
import { HeroIntro } from "./_ui/HeroIntro";
import { LANES, SORT_LABELS, type Lane, type Sort } from "./lanes.generated";
import { AppBar, boardStamp, raw } from "./_ui/Chrome";
import { SelectFace } from "./_ui/SelectFace";
import { AddLink, ScanBanner, Filters } from "./_ui/FoundSurface";
import { DroppedDock } from "./_ui/Dropped";
import { DroppedOpen } from "./_ui/DroppedOpen";
import { AnswersModal } from "./_ui/AnswersModal";
import { LaneList } from "./_ui/LaneList";
import { LaneEmpty, LaneHint, LaneSearch } from "./_ui/LaneChrome";
import {
  accountId, boardMeta, counts, filtersFrom, laneQuery, openQuestions, page,
  readerName, savedSearch,
} from "./_ui/db";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "./_ui/theme";
import { RememberLane } from "./_ui/RememberLane";
import { writesEnabled } from "./_ui/writes";
import { ANY_WINDOW, DEFAULT_WINDOW } from "./_ui/window";
import { READ_ONLY } from "./_ui/actions";

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

/** How much of the Dropped archive the page carries for the dialog to filter.
 *  A ceiling, not a page: past it the dialog still pages with Load more. */
const ARCHIVE_LIMIT = 500;

export default async function Board({
  searchParams,
}: {
  searchParams: Promise<{ lane?: string; sort?: string; q?: string | string[];
                          within?: string; direct?: string; not?: string;
                          scope?: string; cos?: string; conames?: string;
                          exp?: string;
                          // Checkbox groups: one name, many values, so Next
                          // hands back a string when one is ticked and an
                          // array when several are. Treating the string case
                          // as an array would filter on its characters.
                          loc?: string | string[]; wp?: string | string[] }>;
}) {
  const { lane: asked, sort: askedSort, q, within, direct,
          not: not_, scope: searchIn, cos, conames, exp, loc, wp } =
    await searchParams;
  const many = (v?: string | string[]) =>
    v === undefined ? undefined : (Array.isArray(v) ? v : [v]);
  /** The keywords, as a list, whether one arrived or several. */
  const words = many(q);
  /** What the search box shows, and undefined when nothing real was typed:
   *  an empty box still submits `q=`. */
  const searched = words?.map((w) => w.trim()).filter(Boolean).join(" ")
    || undefined;
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
  // Found takes all three; every other lane takes the keyword search only,
  // which is what its own search row offers.
  // **Found defaults to the last 30 days.** An unfiltered Found is 20,330
  // postings deep and the reader meets the oldest of them as readily as
  // today's; the board's own default was never "everything ever ingested".
  // `all` is the explicit way to ask for that, so the default is a default
  // rather than a ceiling.
  const window = active === "found" ? (within ?? DEFAULT_WINDOW) : within;
  // **Built by `filtersFrom`, not by hand.** This was the fourth place that
  // listed the filter keys, after `laneQuery`, `tabHref` and the keyword chip,
  // and a page that reads a different set from the one the route reads is the
  // shape that had page one filtered and page two not.
  const asParams = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams)) {
    if (value === undefined) continue;
    for (const v of Array.isArray(value) ? value : [value]) asParams.append(key, v);
  }
  const carried = filtersFrom(asParams);
  const wide = window === ANY_WINDOW;
  const filters = active === "found"
    ? { ...carried, within: wide ? undefined : window }
    // Every other lane offers the keyword search only, which is what its own
    // search row draws.
    : (words ? { q: words } : undefined);
  const jar = await cookies();
  const theme = themeFrom(jar.get(THEME_COOKIE)?.value);
  const [tally, first, writes, meta, pool, scope, archive, reader, open] =
    await Promise.all([
    counts(active, filters), page(active, null, 50, sort, filters),
    accountId().then(writesEnabled), boardMeta(),
    // The unfiltered lane, so the count can say what it matched against. On
    // an application lane it is also what tells "the search matched nothing"
    // apart from "the lane is empty".
    filters ? counts(active).then((t) => t[active]) : undefined,
    active === "found" ? savedSearch() : undefined,
    // The archive. It is a dialog on this document rather than a route, so it
    // renders with the board or not at all.
    //
    // **Unfiltered, and the whole of it.** It took the active lane's `q`, so a
    // search typed into Watching also narrowed a dialog that was not open,
    // and the dialog's own search could only reach it by navigating. The
    // dialog filters what it holds in place now, like the board, so it holds
    // the archive rather than its first 50: 58 rows on the board, and
    // `ARCHIVE_LIMIT` is a ceiling far above that rather than a page size.
    page("dropped", null, ARCHIVE_LIMIT, "new"),
    readerName(),
    // The Profile nav badge. A failed count draws no badge rather than a
    // failed board: it is a pointer to another page, not this page's data.
    openQuestions().catch(() => null),
  ]);
  const stamp = boardStamp(meta?.updatedAt ?? null);

  /**
   * A tab's link, carrying everything the reader had set.
   *
   * **The filters travel or they are lost**, which is item 4: a 7-day window
   * on Found came back as 30 days after a trip through Submitted, because the
   * tab href was `/?lane=found` and nothing else. The sort travels for the
   * same reason. They are only meaningful on Found, and they are carried
   * anyway: dropping them on the way out means they cannot come back.
   */
  //
  // **Through `laneQuery`, because this hand-listed four of ten.** `not`,
  // `scope`, `cos`, `conames`, `loc`, `wp` and `exp` were dropped on the way
  // out, and this is `home` on the logo as well as the tabs, so it is the
  // return path from Settings and Profile too: filter Found to "up to 2 years",
  // click Watching, click Found, and the filter is gone with the count moved.
  // Third writer of this URL to be brought onto one derivation.
  const tabHref = (lane: Lane) =>
    `/?${laneQuery(lane, sort, active === "found" ? carried : undefined)}`;

  return (
    <>
      {/* The logo returns to the board, and the board means the lane the
          reader was last on rather than whichever one is first. */}
      <AppBar here="home" theme={theme} home={tabHref(active)}
              openQuestions={open} />
      <RememberLane lane={active} />
      <div className="wrap">
        <div className="top">
          <div className="hero-copy">
            <h1>Jobby is a free agent.</h1>
            <p className="lede">
              {tally.act === 0 ? "Nothing blocked"
                               : `${tally.act} waiting on you`}
            </p>
            {/* **The board's own stamp, which is `dashboard.py:1482`.** It
                said "Reading Postgres directly", which is a note about the
                implementation on the line where the reader's name and the age
                of the data belong.

                **A board that is not live still has to look different from
                one that is**, and that has not been dropped, only moved off
                the identity line: every control on a read-only board already
                renders disabled carrying its reason, which is a stronger
                signal than a word in the hero, and this keeps a plain one
                beside the stamp. The failure mode being guarded against is a
                silent read-only board that looks exactly like a working one,
                which is this repository's oldest shape. */}
            <p className="stamp">
              {reader}
              {stamp ? <> &middot; Updated {stamp}</> : null}
              {writes ? null : (
                <span className="chip" title={READ_ONLY}>read only</span>
              )}
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

        {/* **`watch` on the panel, and only `watch`.** Six rules in the
            stylesheet are scoped to it -- the Watching table's cell padding,
            `white-space:normal` on its date column, and the two columns it
            drops on a narrow screen -- and with no lane class on the panel
            none of them matched: `td.when` kept `nowrap` from the shared rule
            and "posted 15 days ago" ran out of its column and across the one
            beside it.

            The board names this lane and no other (`dashboard.py:1519`), which
            looks like an oversight and is not. `.act`, `.found` and `.done`
            are already taken by other things -- `.act` is the row of buttons
            on a card, `display:flex` -- so adding the lane name to every panel
            would apply a card's layout to the Needs you panel. */}
        <div className={active === "watch" ? "panel on watch" : "panel on"}
             id={`p-${active}`} role="tabpanel">
          {active === "found" ? (
            <>
              <ScanBanner meta={meta} writesEnabled={writes} />
              <Filters q={words} within={within} direct={direct === "1"}
                       sort={sort} shown={first.rows.length}
                       total={tally[active]} pool={pool}
                       scope={scope?.words ?? undefined}
                       // The URL wins over the saved search, so the picker shows what is
                       // being filtered rather than what was stored.
                       locations={many(loc) ?? scope?.locations ?? []}
                       searchScope={searchIn ?? scope?.scope ?? "title"}
                       extraLocations={scope?.extra ?? []}
                       not_={not_} cos={cos} conames={conames}
                       wp={many(wp)} exp={exp}
                       // **Off `carried`, not off the raw params.** These are
                       // three-state -- on, off, and never touched -- and
                       // `filtersFrom` is the one place that reads the hidden
                       // companion's `=0` and the box's `=1` in the right
                       // order. A second reader here would be the fifth
                       // hand-listed filter set in this file's history.
                       unkposted={carried.unkposted} unkwp={carried.unkwp}
                       unkexp={carried.unkexp} incremote={carried.incremote}
                       writesEnabled={writes} />
            </>
          ) : null}
          {active !== "found" ? (
            <>
              <LaneHint lane={active} count={tally[active]} />
              <LaneSearch lane={active} q={searched}
                          shown={first.rows.length} total={tally[active]}
                          whole={pool ?? tally[active]} />
            </>
          ) : null}
          {first.rows.length === 0 ? (
            // **"Nothing on the watch list. Press Scan now" is false under a
            // search that matched nothing**, and it was what every application
            // lane said while #235 had every search matching nothing.
            <LaneEmpty lane={active}
                       searched={active === "found" ? undefined : searched} />
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
          {/* After the lane, where the board puts it, so it is the last thing
              on Found rather than a control competing with the filters. */}
          {active === "found" ? <AddLink writesEnabled={writes} /> : null}
        </div>
      </div>

      {/* `query` is built by `laneQuery` like every other lane URL, and
          carries no filter: the dialog searches what it holds in place, so a
          page past the first is the next slice of the whole archive. A JSX
          comment cannot sit between attributes, which is the second time
          today that construct has compiled in my head and not in the build. */}
      <DroppedDock count={tally.dropped} rows={archive.rows}
                   cursor={archive.next} done={archive.done}
                   query={laneQuery("dropped", "new")}
                   writesEnabled={writes} />
      <SelectFace />
      <DroppedOpen />
      {/* One dialog for the page, opened by delegation, so it survives
          LaneList replacing the rows when it pages. */}
      <AnswersModal />
    </>
  );
}
