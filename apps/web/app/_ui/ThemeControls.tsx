"use client";

import { useEffect } from "react";
import { THEME_WIRE } from "../chrome.generated";

/**
 * Runs the board's own theme wiring, after React has hydrated.
 *
 * **Not a `<script>` tag, and the reason took two attempts to find.** The first
 * version put the block at the end of `<body>`; React 19 hoists script elements
 * into the head, so it ran before a single `.themebtn` existed and bound its
 * listeners to an empty NodeList. Wrapping it in a `DOMContentLoaded` deferral
 * fixed that and introduced the next one: `DOMContentLoaded` fires *before*
 * React hydrates, so the listeners attached to nodes hydration then discarded.
 * The symptom is identical both times, three controls that render and do
 * nothing, and in the second case `window.__jb_ran_theme` was set, which reads
 * as "it ran" and is true and useless.
 *
 * An effect is the only placement that is after hydration by construction. It
 * is also what `SelectFace` already does for `premium_ui.SCRIPT`, so this is
 * the established shape here rather than a new one.
 *
 * The block itself is `dashboard.py`'s, generated and unedited: `paintTheme`
 * moves the `on` class and pins the two `theme-color` metas, `setTheme` stores
 * only an explicit choice so `auto` keeps following the OS, and the storage key
 * is `jobby.theme`, which is the board's.
 */
export function ThemeControls() {
  useEffect(() => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-implied-eval
      new Function(THEME_WIRE)();
    } catch (err) {
      // Never silent. Three controls that render and do nothing is exactly the
      // failure this file exists for, and it has already happened twice.
      console.error("theme controls failed to attach", err);
    }
  }, []);
  return null;
}
