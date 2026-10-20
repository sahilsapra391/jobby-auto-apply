"use client";

import { useEffect } from "react";
import { SELECT_SCRIPT } from "../chrome";

/**
 * Runs the board's own select-face script.
 *
 * **The native `<select>` stays in the DOM owning value and events.** This is
 * `premium_ui.SCRIPT` verbatim, not a reimplementation: a custom listbox would
 * look the same and behave differently in keyboard handling, form
 * participation, and the two things CLAUDE.md says the face has to keep doing
 * -- saved searches assign to `.value` and dispatch nothing, so the face
 * redefines that property, and an implicit `<label>` forwards clicks to a
 * control that is now invisible.
 *
 * In an effect rather than a `<script>` tag so it runs after React has put the
 * selects on the page. The script is idempotent over what it finds: it marks
 * what it has wrapped and skips it next time.
 */
export function SelectFace() {
  useEffect(() => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-implied-eval
      new Function(SELECT_SCRIPT)();
    } catch (err) {
      // Never silent. A face that failed to attach leaves a bare select, which
      // is usable and wrong-looking, and nobody would know why.
      console.error("select face failed to attach", err);
    }
  }, []);
  return null;
}
