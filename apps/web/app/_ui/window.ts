/**
 * Found's date window, and why it has a default.
 *
 * **An unfiltered Found is 20,330 postings and the reader meets the oldest as
 * readily as today's.** The board's own default was never "everything ever
 * ingested"; v2's was, because the absence of a parameter meant no predicate.
 *
 * `all` is the explicit escape, so this is a default and not a ceiling. It has
 * to be a real value the radios can carry: a default that can only be undone
 * by editing the URL is a filter the reader cannot turn off, which is worse
 * than the one they could not turn on.
 *
 * **Not a `"use client"` module.** `page.tsx` is a server component and reads
 * this to decide the window before any query runs. A constant exported from a
 * client file reaches the server as a client reference, not a string, and
 * `jar.get(undefined)` is a miss like any other -- which is how the lane cookie
 * was silently empty while the theme cookie beside it worked.
 */

/** The window Found uses when the reader has not chosen one. */
export const DEFAULT_WINDOW = "30d";

/** What the reader picks to mean "no window at all". */
export const ANY_WINDOW = "all";
