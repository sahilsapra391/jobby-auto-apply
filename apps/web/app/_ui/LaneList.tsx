"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Lane } from "../lanes.generated";
import {
  PostingRow, WatchRow, DoneRow, DroppedRow, ActCard, FlightCard,
} from "./Rows";
import { ActionsContext, type Actions } from "./actions";
import { useLive } from "./useLive";
import { useWrites } from "./useWrite";
import { drive, forget, mark, measuring, summarise, taken } from "./measure";

type Row = Record<string, unknown> & { id: string };
type Cursor = { at: string | null; id: string } | null;

/** How many rows are in the DOM at once, whatever the lane holds. */
const WINDOW = 60;
/** Rows kept rendered above and below the visible slice, so a fast scroll
 *  does not outrun the re-render and show a gap. */
const OVERSCAN = 12;
const ROW_PX = 132;

/**
 * Which component draws one row of each lane.
 *
 * **Not memoised, and that is a measurement rather than an oversight.** The
 * rig first read Found at p95 244 ms against a six-row lane's 34 ms, which
 * looked exactly like fifty cards re-rendering for one changed row. `memo` on
 * every row component moved it to 296 ms, which is noise. The per-iteration
 * series then showed why: both lanes start high and settle to a flat 33 ms,
 * Found by its fourteenth call and Watching by its twenty-second. It was page
 * warm-up being measured as steady state, and the row count had nothing to do
 * with it. Rendering as elements would be the prerequisite for memoising, so
 * it is left as it is until something measured asks for it.
 */
const ROWS: Record<Lane, React.ComponentType<Row>> = {
  found: PostingRow, watch: WatchRow, done: DoneRow,
  dropped: DroppedRow, act: ActCard, flight: FlightCard,
};

/**
 * The three lanes the board draws as tables, with their own columns.
 *
 * Taken from a real `dashboard.build` render rather than retyped; the widths
 * are the board's own colgroup, which is load-bearing because CLAUDE.md notes
 * the table lanes are the ones that used to break out of the shell width.
 */
const TABLES: Partial<Record<Lane, { head: string[]; cols?: string[] }>> = {
  done: {
    head: ["Company", "Role", "Status", "When", "Posting", "Proof", "Resume",
           "Cover letter", "Answers"],
  },
  watch: {
    head: ["Role", "Posted", "Experience", "Location", "Core skills",
           "How you line up", ""],
    cols: ["17%", "7%", "8%", "12%", "18%", "26%", "12%"],
  },
  dropped: {
    head: ["Role", "Dropped from", "Last stage", "When", ""],
    cols: ["25%", "13%", "31%", "11%", "20%"],
  },
};

/**
 * A lane, windowed.
 *
 * **500k rows never reach the browser and the ones that do never all render.**
 * Two separate bounds: the server sends a page at a time through the keyset,
 * and this keeps only a slice of what has arrived in the DOM. Either alone is
 * not enough -- fetching 50 at a time still ends with 14,000 nodes if the
 * reader keeps scrolling, and windowing an unbounded fetch still ships the
 * whole corpus over the wire.
 *
 * Spacers rather than absolute positioning, so the scrollbar reflects the real
 * height and the browser's own find-in-page still lands somewhere sensible.
 */
export function LaneList({
  lane, initial, initialCursor, done: initiallyDone, shape, query,
  writesEnabled = false,
}: {
  lane: Lane;
  initial: Row[];
  initialCursor: Cursor;
  done: boolean;
  // A name, not a function. A server component cannot hand a client one a
  // renderer -- React refuses to serialise it -- and the build accepts it
  // happily, so the whole board 500s at request time with a green typecheck
  // behind it.
  //
  // It is the lane now rather than "posting or application", because the board
  // draws five shapes across six lanes and collapsing them to two is what made
  // Watching and Submitted render as cards where the board renders tables.
  shape: Lane;
  /**
   * Everything identifying this lane view, as one string: the lane, the sort
   * and any filters.
   *
   * **One prop, because three were forgettable and were forgotten.** This
   * component has always taken `sort` and `filters` and has always documented
   * why they must travel with the cursor; the page passed neither, so page two
   * of a Match %-sorted Found lane asked the date query for rows before the
   * timestamp "93" and the reader was told 17,341 rows were 50.
   */
  query: string;
  /**
   * The account's `live_board` flag, read on the server.
   *
   * **Offered, not authorised.** The route reads the same flag against the
   * same database before it touches anything, so a client that ignores this
   * gets a 503 and a card that goes back where it was.
   */
  writesEnabled?: boolean;
}) {
  const Row = ROWS[shape];
  const [rows, setRows] = useState<Row[]>(initial);
  const [cursor, setCursor] = useState<Cursor>(initialCursor);
  const [done, setDone] = useState(initiallyDone);
  const [loading, setLoading] = useState(false);
  // **Exhausted and failed are different facts.** They were one before: a
  // failed page set `done`, and the lane drew "and that is all of them" over
  // whatever it had not fetched. The comment above that line already said
  // "Never silent"; it was, because the two states shared a variable.
  const [failed, setFailed] = useState<string | null>(null);
  const [first, setFirst] = useState(0);
  const holder = useRef<HTMLDivElement>(null);

  const writes = useWrites(lane);
  // A ref that trails the state, so `useLive` can read the rows without
  // depending on them and without deciding anything inside an updater.
  const held = useRef(rows);
  held.current = rows;
  const readRows = useCallback(() => held.current, []);
  const live = useLive({
    lane, query, rows: readRows, setRows, absorbs: writes.absorbs,
  });

  /**
   * Take a row out now, and hand back how to put it back.
   *
   * The whole row and its index, not just the id: a card put back at the
   * bottom of a lane it was at the top of is a different wrong answer from the
   * one the revert exists to prevent.
   */
  const draw = useCallback((id: string) => {
    let removed: { row: Row; at: number } | null = null;
    setRows((held) => {
      const at = held.findIndex((r) => r.id === id);
      if (at < 0) return held;
      removed = { row: held[at], at };
      return held.filter((r) => r.id !== id);
    });
    return () => {
      if (!removed) return;
      const { row, at } = removed;
      setRows((held) => {
        if (held.some((r) => r.id === row.id)) return held;
        const back = held.slice();
        back.splice(Math.min(at, back.length), 0, row);
        return back;
      });
    };
  }, []);

  // **The gate's stopwatch, reachable from the console on the deployed
  // board.** Behind `?measure`, because instrumentation nobody asked for is
  // overhead on every reader. `drive` runs the real apply path over the real
  // network and writes nothing, so it is repeatable.
  useEffect(() => {
    if (!measuring()) return;
    window.__jobbyMeasure = {
      samples: taken,
      summary: () => summarise(),
      forget,
      drive: ({ n = 20 } = {}) =>
        drive({ lane, n, ids: rows.slice(0, 10).map((r) => r.id), apply: live.ask }),
      reload: live.reload,
    };
    return () => { delete window.__jobbyMeasure; };
  }, [lane, rows, live.ask, live.reload]);

  const actions: Actions = useMemo(() => ({
    enabled: writesEnabled,
    act: writes.act,
    busy: writes.pending,
    draw,
  }), [writesEnabled, writes.act, writes.pending, draw]);

  // Re-seed when the lane changes: the server sent a different lane's page and
  // keeping the old rows would show one lane's contents under another's tab.
  useEffect(() => {
    setRows(initial); setCursor(initialCursor);
    setDone(initiallyDone); setFirst(0); setFailed(null);
  }, [lane, initial, initialCursor, initiallyDone]);

  const onScroll = useCallback(() => {
    const node = holder.current;
    if (!node) return;
    const top = window.scrollY - node.offsetTop;
    setFirst(Math.max(0, Math.floor(top / ROW_PX) - OVERSCAN));
  }, []);

  useEffect(() => {
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [onScroll]);

  const more = useCallback(async () => {
    if (loading || done) return;
    setLoading(true);
    const stop = mark("load-more", lane);
    try {
      // The sort and the filters travel with the cursor: the next page is a
      // keyset on whichever column this lane is ordered by, and page two of a
      // different ordering or a different predicate is a different lane. They
      // arrive together in `query` so a caller cannot supply one of them.
      const params = new URLSearchParams(query);
      if (cursor) {
        params.set("afterId", cursor.id);
        if (cursor.at) params.set("afterAt", cursor.at);
      }
      const res = await fetch(`/api/lane?${params}`);
      if (!res.ok) throw new Error(`lane page ${res.status}`);
      const body = await res.json();
      setRows((held) => held.concat(body.rows));
      setCursor(body.next);
      setDone(body.done);
      setFailed(null);
      stop();
    } catch (err) {
      console.error("could not load more of this lane", err);
      // Not `done`. An exhausted lane and a failed fetch look identical from
      // the reader's chair, and one of them is a bug.
      setFailed("That page did not load.");
    } finally {
      setLoading(false);
    }
  }, [query, lane, cursor, done, loading]);

  const last = Math.min(rows.length, first + WINDOW + OVERSCAN * 2);
  const slice = rows.slice(first, last);
  const table = TABLES[shape];
  const above = first * ROW_PX;
  const below = Math.max(0, rows.length - last) * ROW_PX;

  // A table lane cannot take the spacer divs: a <div> between <tbody> and <tr>
  // is dropped by the parser, the rows reparent, and the windowing silently
  // stops working. Same two spacers, as rows.
  const body = table ? (
    <div className="sheet"><div className="tablewrap"><table>
      {table.cols ? (
        <colgroup>{table.cols.map((w, i) => <col style={{ width: w }} key={i} />)}</colgroup>
      ) : null}
      <thead><tr>{table.head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
      <tbody>
        <tr aria-hidden="true" style={{ height: above }}><td colSpan={table.head.length} /></tr>
        {slice.map((row) => <Row key={row.id} {...row} />)}
        <tr aria-hidden="true" style={{ height: below }}><td colSpan={table.head.length} /></tr>
      </tbody>
    </table></div></div>
  ) : (
    <>
      <div style={{ height: above }} aria-hidden="true" />
      {shape === "found" ? (
        // **`.jb-grid` is a flex row of `.jb-col`, not of cards.** The board's
        // own script appends columns to it and puts the cards inside those;
        // dropping 50 cards in directly made every one a flex item in a single
        // row, squeezed to 37px wide and overlapping. Measured at 1600px
        // before this: --cols resolves to 1 at every width, because
        // premium_ui's base `.jb-grid { --cols:1 }` is the last declaration
        // and beats the two media queries above it. So one column is not a
        // simplification of the board's round-robin, it is what the board
        // itself builds here.
        <div className="jb-grid">
          <div className="jb-col">
            {slice.map((row) => <Row key={row.id} {...row} />)}
          </div>
        </div>
      ) : (
        // The card lanes put their articles straight into `.grid`, which is
        // what the board does for Needs you and In flight.
        <div className="grid">
          {slice.map((row) => <Row key={row.id} {...row} />)}
        </div>
      )}
      <div style={{ height: below }} aria-hidden="true" />
    </>
  );

  return (
    <ActionsContext.Provider value={actions}>
    <div ref={holder}>
      {live.connected === false ? (
        <p className="hint" role="status">
          The live connection dropped. Reconnecting; this lane is as of the
          last update it received.
        </p>
      ) : null}
      {writes.refused ? (
        <p className="hint" role="alert">
          {writes.refused}{" "}
          <button className="btn ghost" type="button" onClick={writes.clear}>
            Dismiss
          </button>
        </p>
      ) : null}
      {live.arrived > 0 ? (
        <button className="btn" type="button" onClick={() => void live.reload()}>
          {live.arrived === 1 ? "1 new row" : `${live.arrived} new rows`}
        </button>
      ) : null}
      {body}
      {failed ? (
        <p className="hint" role="alert">
          {failed}{" "}
          <button className="btn" type="button" onClick={more} disabled={loading}>
            {loading ? "Loading…" : "Try again"}
          </button>
        </p>
      ) : done ? (
        <p className="hint">
          {rows.length.toLocaleString()} shown, and that is all of them.
        </p>
      ) : (
        <button className="btn" type="button" onClick={more} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
    </ActionsContext.Provider>
  );
}
