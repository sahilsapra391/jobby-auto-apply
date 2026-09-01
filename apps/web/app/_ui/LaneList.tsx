"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { Lane } from "../lanes.generated";
import {
  PostingRow, WatchRow, DoneRow, DroppedRow, ActCard, FlightCard,
} from "./Rows";

type Row = Record<string, unknown> & { id: string };
type Cursor = { at: string | null; id: string } | null;

/** How many rows are in the DOM at once, whatever the lane holds. */
const WINDOW = 60;
/** Rows kept rendered above and below the visible slice, so a fast scroll
 *  does not outrun the re-render and show a gap. */
const OVERSCAN = 12;
const ROW_PX = 132;

/** Which component draws one row of each lane. */
const ROWS: Record<Lane, (row: Row) => React.ReactElement> = {
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
  lane, initial, initialCursor, done: initiallyDone, shape,
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
}) {
  const render = ROWS[shape];
  const [rows, setRows] = useState<Row[]>(initial);
  const [cursor, setCursor] = useState<Cursor>(initialCursor);
  const [done, setDone] = useState(initiallyDone);
  const [loading, setLoading] = useState(false);
  const [first, setFirst] = useState(0);
  const holder = useRef<HTMLDivElement>(null);

  // Re-seed when the lane changes: the server sent a different lane's page and
  // keeping the old rows would show one lane's contents under another's tab.
  useEffect(() => {
    setRows(initial); setCursor(initialCursor);
    setDone(initiallyDone); setFirst(0);
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
    try {
      const params = new URLSearchParams({ lane });
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
    } catch (err) {
      // Never silent: an exhausted lane and a failed fetch look identical from
      // the reader's chair, and one of them is a bug.
      console.error("could not load more of this lane", err);
      setDone(true);
    } finally {
      setLoading(false);
    }
  }, [lane, cursor, done, loading]);

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
        {slice.map((row) => <Fragment key={row.id}>{render(row)}</Fragment>)}
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
            {slice.map((row) => <Fragment key={row.id}>{render(row)}</Fragment>)}
          </div>
        </div>
      ) : (
        // The card lanes put their articles straight into `.grid`, which is
        // what the board does for Needs you and In flight.
        <div className="grid">
          {slice.map((row) => <Fragment key={row.id}>{render(row)}</Fragment>)}
        </div>
      )}
      <div style={{ height: below }} aria-hidden="true" />
    </>
  );

  return (
    <div ref={holder}>
      {body}
      {done ? (
        <p className="hint">
          {rows.length.toLocaleString()} shown, and that is all of them.
        </p>
      ) : (
        <button className="btn" type="button" onClick={more} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
