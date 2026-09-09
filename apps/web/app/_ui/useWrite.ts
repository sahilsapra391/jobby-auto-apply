"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Mutations, type Outcome } from "./optimistic";
import { mark } from "./measure";

/**
 * A click that changes something, drawn before the server agrees and put back
 * if it never does.
 *
 * **The acknowledgement is the POST's own answer, not the channel event.** The
 * route commits and then publishes, and a publish can fail after a commit that
 * worked; a board waiting for the event would put back a change that actually
 * happened, under a real name, having shown the reader it worked.
 *
 * **The `enabled` flag decides whether a control is offered. It is not
 * authorisation.** The server refuses on its own read of `live_board`, so a
 * client that ignores this and posts anyway gets a 503 and a reverted card.
 * Two checks answering the same question in two places is the point: the one
 * that matters is the one holding the credential.
 */

export type WriteState = {
  /** Subjects with a change in flight, for the ledger's absorb rule. This
   *  one counts what it held; it is not a render-time read. */
  absorbs: (subject: string) => boolean;
  /** The same question with no side effect, for greying a control. */
  pending: (subject: string) => boolean;
  /** Draw the change, send it, and put it back if the answer refuses. */
  act: (change: {
    subject: string; body: unknown; undo: () => void;
  }) => Promise<void>;
  /** The last refusal, so the board can say what happened rather than blink. */
  refused: string | null;
  clear: () => void;
};

export function useWrites(lane: string): WriteState {
  const ledger = useRef<Mutations | null>(null);
  if (!ledger.current) ledger.current = new Mutations();
  const [refused, setRefused] = useState<string | null>(null);

  // **Hope has a deadline.** Without this sweep a request nobody answers
  // leaves its optimistic change on screen for as long as the tab is open,
  // which from the reader's chair is indistinguishable from having worked.
  useEffect(() => {
    const t = setInterval(() => {
      const late = ledger.current!.sweep();
      if (late.length) {
        setRefused("The server did not answer, so the board put that back.");
      }
    }, 2000);
    return () => clearInterval(t);
  }, []);

  const act = useCallback(async (change: {
    subject: string; body: unknown; undo: () => void;
  }) => {
    // **The optimistic draw is what the gate measures**, not the round trip.
    // `begin` has already run the caller's own change by the time this stops,
    // so the sample is click to the frame showing it, which is the number the
    // reader experiences. The server's answer arrives later and is measured
    // separately as `write-ack`.
    const drawn = mark("write-optimistic", lane);
    const acked = mark("write-ack", lane);
    const { key, body } = ledger.current!.begin(change);
    drawn();
    let outcome: Outcome;
    try {
      const res = await fetch("/api/applications", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          // **Every board mutation carries this.** Without it the request is
          // refused as cross-site, which is what Apply, Drop, Watch and both
          // Retry controls all send through. `found_lane.py` shipped a POST
          // helper without it once and the Found lane 403ed for five days
          // behind a toast that named no reason.
          "x-jobby-request": "1",
          // Minted with the change and kept across retries. A fresh key per
          // attempt turns the server's dedupe into decoration.
          "idempotency-key": key,
        },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        outcome = { ok: true };
      } else {
        const said = await res.json().catch(() => ({}));
        outcome = {
          ok: false,
          reason: said.error ?? `the server answered ${res.status}`,
          retryable: res.status >= 500,
        };
      }
    } catch (err) {
      outcome = {
        ok: false,
        reason: "that did not reach the server",
        retryable: true,
      };
      console.error("write failed", err);
    }
    acked();
    let settled;
    try {
      settled = ledger.current!.settle(key, outcome);
    } catch {
      // The sweep got there first and has already put the board back. Saying
      // so beats raising into a click handler nobody is catching.
      return;
    }
    if (!outcome.ok) setRefused(outcome.reason);
    if (settled.absorbed) {
      // Events for this subject arrived while the change was in flight and
      // were held so it would not be drawn twice. They are late, not lost.
      window.dispatchEvent(
        new CustomEvent("jobby:refetch", { detail: { subject: change.subject } }));
    }
  }, [lane]);

  // **Stable identities, or the socket reopens on every render.** `useLive`
  // takes `absorbs` in a dependency array; a fresh closure each render makes
  // that array change every render, which tears down the WebSocket and builds
  // a new one, and the seq goes with it. The failure is a board that looks
  // connected and reconnects continuously.
  const absorbs = useCallback((subject: string) =>
    ledger.current!.absorbs(subject), []);
  const pending = useCallback((subject: string) =>
    ledger.current!.pending(subject), []);
  const clear = useCallback(() => setRefused(null), []);

  return { absorbs, pending, act, refused, clear };
}
