"use client";

import { useEffect } from "react";
import { HERO_INTRO } from "../chrome";

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
 *
 * **Once per document, not once per lane.** The hero lives inside the page, so
 * a lane switch remounts it; while the tabs were full page loads that was one
 * flourish per load and looked deliberate. Client-side switching makes the
 * page remount without the document reloading, and the draw-on would replay on
 * every tab click -- which is exactly the complaint about the old full-load
 * behaviour, arriving by a new route.
 *
 * A module-level flag has the semantics wanted, precisely: it survives a client
 * navigation because the module stays loaded, and it resets on a real reload
 * because the module is loaded again. A ref would reset with the component and
 * a cookie would outlive the document.
 */
let played = false;

export function HeroIntro() {
  useEffect(() => {
    if (played) {
      // The class the CSS gates on is added once by the layout, per document.
      // Clearing it here is what stops a remount re-running the keyframes.
      document.documentElement.classList.remove("jb-intro");
      return;
    }
    played = true;
    try {
      // eslint-disable-next-line @typescript-eslint/no-implied-eval
      new Function(HERO_INTRO)();
    } catch (err) {
      console.error("hero intro failed to attach", err);
    }
  }, []);
  return null;
}
