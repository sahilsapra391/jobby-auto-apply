"use client";

import { useCallback, useMemo, useState } from "react";
import { LANE_SEARCH_PLACEHOLDER } from "../lane_chrome";
import { LaneList } from "./LaneList";
import { droppedFromLabel, droppedHaystack } from "./droppedLanes";
import type { Cursor, Row } from "./db";

/**
 * The inside of the Dropped dialog: the board's hint, its filter bar, and the
 * archive, filtered in place.
 *
 * **The search was a GET form to `/` carrying `lane=dropped`, and `dropped` is
 * not a tab.** `page.tsx` read that as an unknown lane, served Found, applied
 * the term to Found's keyword filter and closed the dialog. So a search typed
 * into the archive emptied the board behind it and showed nothing of the
 * archive. The board filters this list in place (`applyFilter` in
 * `dashboard.py`), and so does this: no navigation, no URL, Found untouched.
 *
 * Same haystack as the board's, company, role, the lane it came from, the
 * stage and the reason, and the same select over where each role was dropped
 * from. The count reads "N shown" or "N of M", as the board's does.
 */
export function DroppedArchive({ rows, cursor, done, query, writesEnabled }: {
  rows: Row[];
  cursor: Cursor;
  done: boolean;
  query: string;
  writesEnabled?: boolean;
}) {
  const [term, setTerm] = useState("");
  const [from, setFrom] = useState("");
  const [counted, setCounted] = useState({ shown: rows.length, held: rows.length });

  // The board's `dropped_lanes`, with the spellings folded together so
  // "watch" and "Watching" are one entry rather than two.
  const lanes = useMemo(
    () => [...new Set(rows.map((r) => droppedFromLabel(r.dropped_from)))].sort(),
    [rows]);

  const needle = term.trim().toLowerCase();
  const keep = useCallback((row: Row) => {
    if (needle && !droppedHaystack(row).includes(needle)) return false;
    if (from && droppedFromLabel(row.dropped_from) !== from) return false;
    return true;
  }, [needle, from]);
  const onShown = useCallback(
    (shown: number, held: number) => setCounted({ shown, held }), []);

  if (!rows.length) return null;
  const { shown, held } = counted;
  return (
    <>
      <p className="hint">
        Roles you decided against. They stay here so Jobby does not offer them again.
      </p>
      <div className="filters" data-scope="dropped">
        <input type="search" data-search="dropped" value={term}
               onChange={(e) => setTerm(e.target.value)}
               placeholder={LANE_SEARCH_PLACEHOLDER}
               aria-label="Search dropped applications" />
        {lanes.length ? (
          // Controlled, like Profile's copy-from select. The select face
          // redefines `.value` to keep its own face in step with an
          // assignment, and dispatches a bubbling `change` when the reader
          // picks, which is what `onChange` listens for.
          <select data-filter="dropped" value={from}
                  aria-label="Dropped from"
                  onChange={(e) => setFrom(e.target.value)}>
            <option value="">All</option>
            {lanes.map((l) => <option value={l} key={l}>{l}</option>)}
          </select>
        ) : null}
        <span className="fcount" data-count="dropped" aria-live="polite">
          {shown === held ? `${held.toLocaleString()} shown`
                          : `${shown.toLocaleString()} of ${held.toLocaleString()}`}
        </span>
      </div>
      {shown === 0 ? (
        <p className="empty">
          <strong>No dropped roles match.</strong>
          <span>Nothing in the archive matches that search. Clear it to see all {held.toLocaleString()}.</span>
        </p>
      ) : null}
      <LaneList lane="dropped" shape="dropped" initial={rows}
                initialCursor={cursor} done={done} query={query}
                writesEnabled={writesEnabled}
                keep={keep} onShown={onShown} whole />
    </>
  );
}
