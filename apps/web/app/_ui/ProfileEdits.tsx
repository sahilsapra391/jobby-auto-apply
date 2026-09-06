"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import type { EditOutcome } from "./db";
import {
  resolveEditState, slotOf, SENT_TTL_MS, type EditState, type Sent,
} from "./editState";

export { slotOf };
export type { EditState };

/**
 * The Profile page's write half: what the reader sent, and what the device did
 * with it.
 *
 * **Enqueue-only, so there is nothing to draw optimistically.** Every other
 * write in v2 changes a row this deployment owns, so `useWrites` draws the
 * change, posts it, and puts it back on a refusal. An answer is not that: it
 * lives in `answers.json` on the device and reaches `answer_bank` through the
 * Class B mirror, so the device is the only writer and v2 has no row to move.
 * Drawing the new value here would be v2 showing a state the store may refuse,
 * which is the failure `edit_outcomes` was built to end rather than a shape to
 * copy.
 *
 * So the reader is shown the truth at each step, and there are four of them:
 *
 *     sent      the queue took it; the device has not looked yet
 *     pending   the device claimed it; `store.py` has not decided
 *     accepted  it applied
 *     refused   it did not, and here is what was sent, what is held, and why
 *
 * **"Sent" is local and expires.** It is the only one of the four that is not
 * a fact about the store, so it cannot be allowed to stand for ever: a sent
 * edit that never becomes `pending` means the daemon never heard, and that has
 * to become visible rather than sitting as a hopeful label until the tab
 * closes. Twenty minutes, then it says so. Same rule as the board's
 * `CLICK_MEMORY`, which was an immortal id list once and replayed a drop for
 * two hours.
 */

/** How often the board asks what the device decided, while anything is open. */
const POLL_MS = 3_000;
/** And the ceiling on that, so a tab left open does not poll for a day. */
const POLL_CEILING_MS = 15 * 60_000;
const STORE_KEY = "jobby.profile-edits";

type Edits = {
  enabled: boolean;
  stateOf: (slot: string) => EditState;
  /** Post one edit. Resolves to the refusal text, or null when it was taken. */
  send: (edit: { kind: "answer" | "resume"; slot: string }
              & Record<string, unknown>)
    => Promise<string | null>;
};

const EditsContext = createContext<Edits>({
  enabled: false,
  stateOf: () => ({ kind: "none" }),
  send: async () => "writes are off",
});

export const useEdits = () => useContext(EditsContext);

function readSent(): Sent[] {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    const held = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(held)) return [];
    // An entry with no stamp predates the expiry and is discarded rather than
    // migrated: its POST has already gone out, and believing it now can only
    // mislabel a row.
    return held.filter((e) => e && typeof e.editId === "string"
                         && typeof e.slot === "string"
                         && typeof e.at === "number");
  } catch {
    return [];
  }
}

function writeSent(list: Sent[]) {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(list));
  } catch {
    // A private window, or site data blocked. The page still works; it just
    // forgets across a reload, which is the smaller loss.
  }
}

/** A random id the queue will accept: `[A-Za-z0-9._-]`, and unguessable. */
function mintEditId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return "v2-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function EditsProvider({ enabled, outcomes: initial, children }: {
  enabled: boolean; outcomes: EditOutcome[]; children: React.ReactNode;
}) {
  const [outcomes, setOutcomes] = useState<EditOutcome[]>(initial);
  const [sent, setSent] = useState<Sent[]>([]);
  const started = useRef<number>(0);

  // localStorage is read after mount, never during render: the server has no
  // window and a value only the client has would make the two markups differ.
  useEffect(() => { setSent(readSent()); }, []);

  const decided = useMemo(
    () => new Set(outcomes.map((o) => o.editId)), [outcomes]);

  // An edit the device has answered is no longer a local hope. Dropping it
  // here rather than at render time is what keeps the store from growing.
  useEffect(() => {
    setSent((held) => {
      const keep = held.filter((s) => !decided.has(s.editId));
      if (keep.length !== held.length) writeSent(keep);
      return keep.length === held.length ? held : keep;
    });
  }, [decided]);

  const open = sent.some((s) => !decided.has(s.editId))
    || outcomes.some((o) => o.state === "pending");

  useEffect(() => {
    if (!open) { started.current = 0; return; }
    if (!started.current) started.current = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - started.current > POLL_CEILING_MS) return;
      try {
        const res = await fetch("/api/profile", { cache: "no-store" });
        if (!res.ok) return;
        const said = await res.json();
        if (Array.isArray(said.outcomes)) setOutcomes(said.outcomes);
      } catch {
        // The next tick asks again. A failed poll says nothing about the
        // edit, so nothing about the row changes.
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [open]);

  const stateOf = useCallback(
    (slot: string): EditState =>
      resolveEditState(slot, sent, outcomes, Date.now()),
    [sent, outcomes]);

  const send = useCallback(async (edit: {
    kind: "answer" | "resume"; slot: string;
  } & Record<string, unknown>): Promise<string | null> => {
    const editId = mintEditId();
    const { slot, ...body } = edit;
    const entry: Sent = { editId, slot, at: Date.now() };
    setSent((held) => {
      const next = [...held.filter((s) => Date.now() - s.at < SENT_TTL_MS), entry];
      writeSent(next);
      return next;
    });
    try {
      const res = await fetch("/api/profile", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          // Minted with the edit and reused on a retry, so the queue's own
          // dedupe and the store's `lastEditId` replay both hold.
          "idempotency-key": editId,
        },
        body: JSON.stringify({ ...body, editId }),
      });
      if (res.ok) return null;
      const said = await res.json().catch(() => ({}));
      const why = said.error ?? `the server answered ${res.status}`;
      // The queue never took it, so there is nothing to wait for and the
      // hopeful label would be a lie.
      setSent((held) => {
        const next = held.filter((s) => s.editId !== editId);
        writeSent(next);
        return next;
      });
      return why;
    } catch {
      setSent((held) => {
        const next = held.filter((s) => s.editId !== editId);
        writeSent(next);
        return next;
      });
      return "that did not reach the server, so nothing was sent";
    }
  }, []);

  const value = useMemo(() => ({ enabled, stateOf, send }),
                        [enabled, stateOf, send]);
  return <EditsContext.Provider value={value}>{children}</EditsContext.Provider>;
}

/** One value, as a reader can compare it. An answer is a string; the wording
 *  is an object; an empty answer is a real edit and says so. */
function shown(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim() ? value : "(empty)";
  return JSON.stringify(value, null, 1);
}

/**
 * What happened to this slot's last edit, drawn where the edit was made.
 *
 * **A refusal is not a toast.** It stays until the reader does something about
 * it, and it carries three facts that do not share a field: what they sent,
 * what the store holds instead, and the store's own words. A refusal the
 * reader cannot compare against is a refusal they cannot act on, which is the
 * rule `answer_record.py` already applies one table over.
 */
export function EditStateLine({ slot, local }: { slot: string; local?: string | null }) {
  const { stateOf } = useEdits();
  const state = stateOf(slot);
  if (local) {
    return <span className="profile-message bad" role="status">{local}</span>;
  }
  if (state.kind === "none") return null;
  if (state.kind === "sent") {
    return <span className="profile-message" role="status">
      Sent. Waiting for your device to pick it up.
    </span>;
  }
  if (state.kind === "lost") {
    return <span className="profile-message bad" role="status">
      Sent {new Date(state.at).toLocaleTimeString()} and your device has not
      picked it up. It is still on the queue; nothing has been saved yet.
    </span>;
  }
  if (state.kind === "pending") {
    return <span className="profile-message" role="status">
      Your device has it and is deciding.
    </span>;
  }
  if (state.kind === "accepted") {
    return <span className="profile-message" role="status">
      Saved{state.at ? ` ${new Date(state.at).toLocaleTimeString()}` : ""}.
    </span>;
  }
  return (
    <div className="profile-error" role="status">
      <strong>Your device refused this edit. Nothing was saved.</strong>
      <p>{state.reason || "The store gave no reason."}</p>
      <dl className="identity-rows">
        <div className="identity-row">
          <dt>You sent</dt><dd>{shown(state.submitted) || "(nothing)"}</dd>
        </div>
        <div className="identity-row">
          <dt>The store holds</dt>
          <dd>{state.heldRecorded ? shown(state.held)
               : "not recorded with this refusal"}</dd>
        </div>
      </dl>
    </div>
  );
}
