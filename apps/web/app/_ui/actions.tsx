"use client";

import { createContext, useContext } from "react";
import { ICONS } from "../icons.generated";

/**
 * One of the board's five action marks.
 *
 * **The board draws these because five identical pills were hard to tell
 * apart**, which its own comment says; v2 shipped the pills without the marks.
 * The geometry is generated from `found_lane.SCRIPT`'s `ICONS` rather than
 * redrawn: the eye is a drawn eye because the emoji is a photoreal eyeball on
 * macOS, and redrawing it is how two boards end up with different eyes.
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
