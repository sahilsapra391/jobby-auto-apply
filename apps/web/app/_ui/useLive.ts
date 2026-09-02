"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveSession, type Envelope } from "./live";
import { actionFor, applyRow, type Row } from "./project";
import type { Lane } from "../lanes.generated";

/**
 * The lane's live connection: subscribe, apply, and say when it is not live.
 *
 * **A board that is not connected has to look different from one that is.**
 * The failure mode of a silent live board is that it looks exactly like a
 * working one and is simply wrong, which is the same shape as the alarm that
 * never fired and the counter that measured the branch tests take.
 *
 * The socket URL comes from `NEXT_PUBLIC_JOBBY_CHANNEL_WS`. **Unset means the
 * board says it is not live**, rather than pretending; there is no channel
 * deployed yet and inventing a reconnect loop against nothing would be a
 * feature nobody consumes.
 */

/** Row asks in flight before a burst is answered with one reload instead. */
export const BURST = 8;

/** Reconnect backoff, capped. A tab left open overnight must not hammer. */
const BACKOFF = [500, 1000, 2000, 5000, 10_000, 30_000];

export type LiveState = {
  /** null when no channel is configured, so "off" and "down" are different. */
  connected: boolean | null;
  /** Rows the lane gained that this list cannot place. */
  arrived: number;
  /** Refetch page one and clear the arrivals. */
  reload: () => Promise<void>;
};

export function useLive({
  lane, query, setRows, absorbs,
}: {
  lane: Lane;
  /** The lane, its sort and its filters, as `laneQuery` built them. One
   *  string so a caller cannot pass some of them. */
  query: string;
  setRows: (fn: (held: Row[]) => Row[]) => void;
  /** Subjects this tab has already drawn optimistically. */
  absorbs: (subject: string) => boolean;
}): LiveState {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [arrived, setArrived] = useState(0);
  // Held in refs, not state: the session outlives a render and re-creating it
  // would reset the seq, which is the one number a reconnect depends on.
  const session = useRef<LiveSession | null>(null);
  const asks = useRef(0);

  // **A string, not an object.** An effect keyed on a `filters` object
  // re-runs every render, because the page builds a fresh one each time, and
  // the effect below opens a WebSocket. The symptom is a board that reports
  // itself connected while reconnecting continuously, losing its seq at every
  // reconnect, which looks exactly like a working live board until it misses
  // an event.
  const reload = useCallback(async () => {
    const res = await fetch(`/api/lane?${query}`);
    if (!res.ok) throw new Error(`lane reload ${res.status}`);
    const body = await res.json();
    setRows(() => body.rows);
    setArrived(0);
  }, [query, setRows]);

  /** Ask the lane whether it still holds one subject, and act on the answer. */
  const ask = useCallback(async (id: string) => {
    const res = await fetch(`/api/lane/row?lane=${lane}&id=${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(`lane row ${res.status}`);
    const { row } = await res.json();
    setRows((held) => {
      const out = applyRow(held, id, row as Row | null);
      if (out.arrived) setArrived((n) => n + 1);
      return out.rows;
    });
  }, [lane, setRows]);

  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_JOBBY_CHANNEL_WS;
    if (!url) {
      // Said once, plainly. A board with no channel is a board that updates
      // when you reload it, and the reader should be told which one they have.
      setConnected(null);
      return;
    }

    let socket: WebSocket | null = null;
    let attempt = 0;
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const report = (err: unknown) =>
      console.error("the live board could not apply an event", err);

    const live = new LiveSession({
      seq: session.current?.seq ?? 0,
      apply: (event: Envelope) => {
        const action = actionFor(lane, event);
        if (action.do === "ignore") return;
        if (action.do === "reload") { void reload().catch(report); return; }
        // This tab drew this one itself a moment ago. Applying the event that
        // announces it would draw it twice; the ledger counts what it held so
        // the settle can go back for anything else that happened meanwhile.
        if (absorbs(action.id)) return;
        if (asks.current >= BURST) {
          // A burst is one fetch, not one each. The lane is behind either way
          // and page one is cheaper than eight row reads.
          void reload().catch(report);
          return;
        }
        asks.current += 1;
        void ask(action.id).catch(report).finally(() => { asks.current -= 1; });
      },
      refetch: reload,
    });
    session.current = live;

    const open = () => {
      socket = new WebSocket(url);
      socket.addEventListener("open", () => {
        attempt = 0;
        setConnected(true);
        socket?.send(JSON.stringify(live.hello()));
      });
      socket.addEventListener("message", (e) => { void live.receive(e.data); });
      socket.addEventListener("close", () => {
        setConnected(false);
        if (closed) return;
        // Never give up silently: a tab that stops reconnecting looks exactly
        // like one with nothing to show.
        timer = setTimeout(open, BACKOFF[Math.min(attempt++, BACKOFF.length - 1)]);
      });
      socket.addEventListener("error", () => socket?.close());
    };
    open();

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [lane, ask, reload, absorbs]);

  return { connected, arrived, reload };
}
