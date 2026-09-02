"use client";

import { useEffect } from "react";
import { THEME_COOKIE, type Theme } from "./theme";

/**
 * The three theme controls, wired to the cookie the server reads.
 *
 * **This used to run `dashboard.py`'s own `THEME_WIRE` block**, which stored
 * the choice in `localStorage` and repainted after hydration. The block was
 * correct; what was wrong is that a store the server cannot see means the
 * server cannot resolve the theme, so every document load had to run script to
 * find out what colour it was. `_ui/theme.ts` says why that had to go.
 *
 * What is kept from that block, because it is the board's behaviour and not an
 * implementation detail:
 *
 *   * `system` **removes** the stored value rather than storing the word, so a
 *     reader on system keeps following the OS if they change it later. The
 *     cookie is deleted, exactly as `setTheme('auto')` removed the key.
 *   * an explicit choice applies immediately, without a reload, by setting
 *     `data-theme` on the same element the server stamps.
 *   * the two `theme-color` metas are pinned to match. The server does this in
 *     the markup now; a click has to keep them in step until the next load.
 *
 * The `on` class is **not** set here. It is server-rendered from the same
 * cookie, so the active control is correct in the first byte rather than after
 * hydration, and this only moves it on a click.
 */
export function ThemeControls({ theme }: { theme: Theme }) {
  useEffect(() => {
    if (!document.querySelector(".themebtn")) {
      // Never silent: three controls that render and do nothing is the exact
      // failure this file has already had twice, and both times the symptom
      // was indistinguishable from working.
      console.error("no theme controls found to wire");
      return;
    }

    const apply = (mode: string) => {
      const root = document.documentElement;
      const controls = Array.from(
        document.querySelectorAll<HTMLElement>(".themebtn"));
      const metas = Array.from(
        document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'));
      if (mode === "auto") {
        delete root.dataset.theme;
        // Deleted rather than set to "auto". An absent cookie is what the
        // server reads as system, and it is the only spelling of it.
        document.cookie = `${THEME_COOKIE}=;path=/;max-age=0;samesite=lax`;
      } else {
        root.dataset.theme = mode;
        document.cookie =
          `${THEME_COOKIE}=${mode};path=/;max-age=31536000;samesite=lax`;
      }
      for (const control of controls) {
        const on = control.dataset.themeSet === mode;
        control.classList.toggle("on", on);
        control.setAttribute("aria-checked", on ? "true" : "false");
      }
      if (metas.length === 2) {
        metas[0].media = mode === "auto" ? "(prefers-color-scheme:light)"
          : (mode === "light" ? "all" : "not all");
        metas[1].media = mode === "auto" ? "(prefers-color-scheme:dark)"
          : (mode === "dark" ? "all" : "not all");
      }
    };

    // **Delegated, and that is the third version of this bug.** The first two
    // bound to `.themebtn` nodes that hydration then discarded. This one bound
    // after hydration, correctly, and then the nav pills became `Link`s: the
    // app bar is rendered by the page rather than the layout, so a lane switch
    // replaces those buttons while this effect, mounted in the layout, still
    // holds the old ones. Three controls that render and do nothing, again,
    // for the third distinct reason.
    //
    // A listener on `document` holds no node references, so it survives the
    // markup being replaced however many times.
    const onClick = (event: MouseEvent) => {
      const control = (event.target as Element | null)
        ?.closest<HTMLElement>(".themebtn");
      if (control) apply(control.dataset.themeSet ?? "auto");
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [theme]);
  return null;
}
