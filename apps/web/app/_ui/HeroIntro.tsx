"use client";

import { useEffect } from "react";
import { HERO_INTRO } from "../chrome.generated";

/**
 * The hero mark's first-paint flourish, and its replay on click.
 *
 * **`html.jb-intro` is added by the boot script and nothing was removing it.**
 * The contour-draw and the ink-wipe are gated on that class, so without this
 * the flourish never ends and clicking the mark does nothing at all. v2 shipped
 * the markup and the CSS and left the eight lines that drive them behind.
 *
 * `dashboard.py`'s block, generated and unedited. Two things in it are
 * load-bearing and would be easy to lose in a rewrite: the reflow between
 * removing and re-adding the class, without which the browser coalesces the
 * two and the replay does not run at all; and the `animationend` listener on
 * the wipe rect, which ends the intro as soon as it is actually over rather
 * than at a fixed timeout.
 *
 * In an effect for the reason `ThemeControls` documents: a `<script>` is
 * hoisted by React 19 and a `DOMContentLoaded` deferral still fires before
 * hydration, so listeners bound either way attach to nodes that are then
 * discarded.
 */
export function HeroIntro() {
  useEffect(() => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-implied-eval
      new Function(HERO_INTRO)();
    } catch (err) {
      console.error("hero intro failed to attach", err);
    }
  }, []);
  return null;
}
