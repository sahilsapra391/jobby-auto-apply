"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Lane } from "../lanes.generated";
import { PostingRow, ApplicationRow } from "./Rows";

type Row = Record<string, unknown> & { id: string };
type Cursor = { at: string | null; id: string } | null;

/** How many rows are in the DOM at once, whatever the lane holds. */
const WINDOW = 60;
/** Rows kept rendered above and below the visible slice, so a fast scroll
 *  does not outrun the re-render and show a gap. */
const OVERSCAN = 12;
const ROW_PX = 132;

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
  shape: "posting" | "application";
}) {
  const render = shape === "posting" ? PostingRow : ApplicationRow;
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

  return (
    <div ref={holder}>
      <div style={{ height: first * ROW_PX }} aria-hidden="true" />
      {slice.map((row) => (
        <div key={row.id}>{render(row)}</div>
      ))}
      <div style={{ height: Math.max(0, rows.length - last) * ROW_PX }}
           aria-hidden="true" />
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
