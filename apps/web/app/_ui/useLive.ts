"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveSession, type Envelope } from "./live";
import { actionFor, applyRow, type Row } from "./project";
import { mark } from "./measure";
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

/** Failed handshakes before the board stops calling it a dropped connection. */
const UNREACHABLE_AFTER = 3;

export type LiveState = {
  /**
   * Four states, because three of them are things a reader would do something
   * different about.
   *
   *   null           no channel configured; the board updates on reload
   *   true           connected
   *   false          it was connected and dropped; reconnecting
   *   "unreachable"  it has never connected. The address is wrong, or the
   *                  channel is down, and no amount of waiting fixes it
   *
   * The last one exists because collapsing it into `false` cost an afternoon:
   * the board said "the live connection dropped" on every page while the URL
   * simply named no account, and the message sent the reader after a network
   * fault that was not there.
   */
  connected: boolean | null | "unreachable";
  /** Rows the lane gained that this list cannot place. */
  arrived: number;
  /** Refetch page one and clear the arrivals. */
  reload: () => Promise<void>;
  /**
   * Ask the lane about one subject and paint the answer.
   *
   * Exposed so the gate's stopwatch can drive the real path rather than a
   * reconstruction of it: applying an event is exactly this call, and it
   * writes nothing, so it can be repeated.
   */
  ask: (id: string) => Promise<void>;
};

export function useLive({
  lane, query, rows, setRows, absorbs,
}: {
  lane: Lane;
  /** The lane, its sort and its filters, as `laneQuery` built them. One
   *  string so a caller cannot pass some of them. */
  query: string;
  /**
   * What the lane is showing, read through a ref rather than passed by value.
   *
   * **A `setRows` updater has to be pure**, and the first version of `ask`
   * decided arrivals inside one and called `setArrived` from there. React is
   * allowed to invoke an updater more than once, so that is a counter that can
   * double, in a build nobody debugs. Reading the rows here keeps the decision
   * outside and the updater a plain return.
   */
  rows: () => Row[];
  setRows: (fn: (held: Row[]) => Row[]) => void;
  /** Subjects this tab has already drawn optimistically. */
  absorbs: (subject: string) => boolean;
}): LiveState {
  const [connected, setConnected] = useState<boolean | null | "unreachable">(null);
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
    const stop = mark("reload", lane);
    const res = await fetch(`/api/lane?${query}`);
    if (!res.ok) throw new Error(`lane reload ${res.status}`);
    const body = await res.json();
    setRows(() => body.rows);
    setArrived(0);
    stop();
  }, [query, lane, setRows]);

  /** Ask the lane whether it still holds one subject, and act on the answer. */
  const ask = useCallback(async (id: string) => {
    const stop = mark("apply-event", lane);
    const res = await fetch(`/api/lane/row?lane=${lane}&id=${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(`lane row ${res.status}`);
    const { row } = await res.json();
    const out = applyRow(rows(), id, row as Row | null);
    // **Only when something moved.** `applyRow` hands back the same array when
    // the lane never held this row, and re-setting it would still re-render
    // the list: measured at 50 Found cards, an event about a row not on screen
    // cost the same repaint as one about a row that changed.
    if (out.changed) setRows(() => out.rows);
    if (out.arrived) setArrived((n) => n + 1);
    // Stopped after the state update is queued, so the frame it waits for is
    // the one that paints it.
    stop();
  }, [lane, rows, setRows]);

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

    // **A socket that never opened is a different fact from one that dropped,
    // and collapsing them cost an afternoon.** The board drew "the live
    // connection dropped, reconnecting" on every page while the real cause was
    // `NEXT_PUBLIC_JOBBY_CHANNEL_WS` pointing at the channel's origin with no
    // account path. The message was true about the symptom, wrong about the
    // cause, and sent the reader hunting a network fault that did not exist.
    //
    // It cannot be fixed on the server. A refused upgrade arrives as close
    // 1006 with an empty reason -- closing after `accept()` was tried and
    // deployed and made no difference, because the close races the handshake.
    // And 1006 is equally a wrong host, a DNS failure or a TLS failure, none
    // of which any server sees. The one fact that separates "this address is
    // wrong forever" from "the network went away" is whether `open` ever
    // fired, and only this side knows it.
    let everOpened = false;
    const open = () => {
      socket = new WebSocket(url);
      socket.addEventListener("open", () => {
        attempt = 0;
        everOpened = true;
        setConnected(true);
        socket?.send(JSON.stringify(live.hello()));
      });
      socket.addEventListener("message", (e) => { void live.receive(e.data); });
      socket.addEventListener("close", () => {
        if (closed) return;
        // Three handshakes that never opened is a setting, not a blip. It
        // keeps retrying -- a tab that gives up silently looks exactly like one
        // with nothing to show, which is the rule this file already held -- but
        // it stops claiming the connection dropped, because it never connected.
        setConnected(everOpened || attempt < UNREACHABLE_AFTER ? false : "unreachable");
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

  return { connected, arrived, reload, ask };
}
