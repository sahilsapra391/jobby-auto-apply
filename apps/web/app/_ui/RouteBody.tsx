"use client";

import { useEffect, useLayoutEffect } from "react";

/**
 * Puts the view class on `<body>`, on a document load and on a client
 * navigation.
 *
 * **This is the /settings outage's mechanism, and client-side nav reopens it.**
 * The board is one document whose panels are toggled by a class on `<body>`:
 * `.settings-page` and `.profile-page` are `display:none` until
 * `body.settings-view` or `body.profile-view` is set. v2 serves them as
 * separate routes, so each route has to declare which view it is, and the page
 * rendered nothing at all until it did. Correct markup, byte-identical CSS,
 * every test green, and a blank page.
 *
 * An inline `<script>` covers a document load, which is what shipped. It does
 * **not** cover a client navigation: React does not execute a script element it
 * inserts after hydration, so the moment the nav pills became `Link`s, walking
 * from Home to Settings would have landed on a blank page again. The class of
 * failure is the same one and the trigger is new, which is why this is a
 * component rather than a line in each page.
 *
 * `useLayoutEffect` on the client, because it runs before the browser paints
 * the swapped route: an ordinary effect leaves one frame where the settings
 * markup is mounted and still hidden. It falls back to `useEffect` during the
 * server render, where neither runs and React warns about the layout one.
 */
const useBeforePaint =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

export function RouteBody({ view }: { view: "home" | "profile" | "settings" }) {
  useBeforePaint(() => {
    const classes = document.body.classList;
    // Every view class is cleared, not just this one: arriving from Settings
    // to Profile without clearing leaves both set, and both panels drawn.
    classes.remove("settings-view", "profile-view");
    if (view !== "home") classes.add(`${view}-view`);
    return () => classes.remove("settings-view", "profile-view");
  }, [view]);
  return null;
}
