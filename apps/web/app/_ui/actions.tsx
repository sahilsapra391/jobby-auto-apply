"use client";

import { createContext, useContext, useState } from "react";
import { ICONS } from "../icons";

/**
 * One of the board's five action marks.
 *
 * **The board draws these because five identical pills were hard to tell
 * apart**, which its own comment says; v2 shipped the pills without the marks.
 * The geometry lives in `icons.ts`, copied from the original board's own
 * icons rather than redrawn: the eye is a drawn eye because the emoji is a
 * photoreal eyeball on macOS.
 */
export function ActionIcon({ name }: { name: string }) {
  const icon = ICONS[name];
  if (!icon) return null;
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true">
      {icon.paths.map((d, i) => <path d={d} key={i} />)}
      {icon.circles.map(([cx, cy, r], i) =>
        <circle cx={cx} cy={cy} r={r} key={`c${i}`} />)}
    </svg>
  );
}

/**
 * The one place a row learns whether its controls do anything.
 *
 * A row component is called as a plain function from `LaneList`'s slice, not
 * rendered as an element, so a hook inside one belongs to the caller's render
 * and its call order changes with the scroll. That is a React error waiting for
 * a fast scroll. So the hook lives in `<Act>`, which *is* an element, and the
 * row passes it what it needs.
 */

export type Actions = {
  /**
   * Whether the account's `live_board` flag is on, read on the server.
   *
   * **This decides whether a control is offered. It is not authorisation.**
   * The route reads the same flag against the same database before it touches
   * anything, so a client that ignores this gets a 503 and a reverted card.
   */
  enabled: boolean;
  /** Draw the change, post it, put it back if the answer refuses. */
  act: (change: { subject: string; body: unknown; undo: () => void }) => Promise<void>;
  /** True while this subject has a change waiting for an answer. */
  busy: (subject: string) => boolean;
  /**
   * Take a row out of the lane now, and hand back how to put it back.
   *
   * The undo belongs to whoever drew the change, and only the lane knows what
   * it drew: the row's contents, and where in the list it stood. A caller
   * reconstructing that from an id would put a card back at the bottom of a
   * lane it was at the top of.
   */
  draw: (id: string) => () => void;
};

export const OFF: Actions = {
  enabled: false,
  act: async () => {},
  busy: () => false,
  draw: () => () => {},
};

export const ActionsContext = createContext<Actions>(OFF);

export const useActions = () => useContext(ActionsContext);

/** Why a control is dark while the gate is still running. */
export const READ_ONLY =
  "This is a write. v2 stays read-only until the parity gate closes.";

/**
 * Apply, with a confirmation that names the job it is about to apply to.
 *
 * **Apply is the one control here that cannot be taken back.** Watch and Pass
 * are a decision the reader can reverse from Watching or Dropped; Drop is
 * archival. Apply hands a real application to an agent that fills a real
 * employer's form under a real name, and `finish.py`'s submission nonce exists
 * because that is the step nothing downstream can undo.
 *
 * **The confirmation names the company and role, because the misclick worth
 * catching is the right button on the wrong row.** A bare "are you sure"
 * catches nothing a second click would not also wave through: the reader
 * already meant to press Apply, they just meant a different card.
 *
 * **And it moves the target rather than relabelling in place.** A button that
 * turns into its own confirmation is armed exactly where an impatient double
 * click is already landing. The three decisions are replaced by a question and
 * two buttons, so the confirming control is somewhere the second click of a
 * double was never going. That is a layout decision doing a job a timing guard
 * would otherwise have to do badly.
 */
export function ApplyWithConfirm({
  subject, rowId, company, role, body,
}: {
  subject: string;
  rowId: string;
  company?: string;
  role?: string;
  body: unknown;
}) {
  const { enabled, act, busy, draw } = useActions();
  const [asking, setAsking] = useState(false);
  const waiting = busy(subject);

  // The question is the whole point, so it is built from what the card shows
  // and falls back to something honest rather than to "this job".
  const named = [role, company].filter(Boolean).join(" at ");

  if (!asking) {
    return (
      <button className="jb-btn primary" type="button" disabled={!enabled || waiting}
              title={enabled ? "Start work on this one" : READ_ONLY}
              onClick={() => setAsking(true)}>
        <ActionIcon name="apply" />
        <span>Apply</span>
      </button>
    );
  }
  return (
    <>
      <span className="jb-confirm" role="status">
        {named ? `Apply to ${named}?` : "Apply to this posting?"}
      </span>
      <button className="jb-btn" type="button" autoFocus
              onClick={() => setAsking(false)}>
        <span>Cancel</span>
      </button>
      <button className="jb-btn primary" type="button" disabled={waiting}
              onClick={() => {
                setAsking(false);
                void act({ subject, body, undo: draw(rowId) });
              }}>
        <ActionIcon name="apply" />
        <span>Yes, apply</span>
      </button>
    </>
  );
}

/**
 * One control that changes something.
 *
 * Disabled with a reason rather than absent: the card is the right shape, and
 * a reader who presses it learns why instead of finding nothing there.
 */
export function Act({
  className, label, title, subject, rowId, body, keep, icon, children,
}: {
  className: string;
  label?: string;
  title?: string;
  /** What the change is about, and what an event will name. */
  subject: string;
  /** The row to take out of this lane while the answer is awaited. */
  rowId: string;
  body: unknown;
  /** Set when the change leaves the row where it is, like a phase move. */
  keep?: boolean;
  /** Which of the board's five marks this action carries. */
  icon?: string;
  children: React.ReactNode;
}) {
  const { enabled, act, busy, draw } = useActions();
  const waiting = busy(subject);
  return (
    <button
      className={className}
      type="button"
      aria-label={label}
      disabled={!enabled || waiting}
      title={enabled ? title : READ_ONLY}
      onClick={() => {
        const undo = keep ? () => {} : draw(rowId);
        void act({ subject, body, undo });
      }}
    >
      {icon ? <ActionIcon name={icon} /> : null}
      <span>{children}</span>
    </button>
  );
}
