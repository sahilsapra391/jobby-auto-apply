import Link from "next/link";
import { LOCKUP, DEFS, THEMESEG_PROPS, THEMESEG_INNER } from "../chrome.generated";
import type { Theme } from "./theme";
import { RouteBody } from "./RouteBody";

export const raw = (html: string) => ({ dangerouslySetInnerHTML: { __html: html } });

/**
 * The board's own theme markup, with the reader's choice already marked.
 *
 * **Stamped into the generated string rather than retyped.** The three buttons
 * carry the board's own icons and `data-theme-set` values; marking the active
 * one is a one-attribute edit, and rebuilding the markup here to add a class
 * is how two boards end up with different suns.
 *
 * Doing it server-side is the point: the active control was previously moved
 * by `paintTheme` after hydration, so the first frame showed none of the three
 * selected and then one appeared.
 */
function themeSegment(theme: Theme) {
  const active = theme === "system" ? "auto" : theme;
  return THEMESEG_INNER.replace(
    /class="themebtn" data-theme-set="([a-z]+)" role="radio" aria-checked="false"/g,
    (whole, mode) => (mode === active
      ? `class="themebtn on" data-theme-set="${mode}" role="radio" aria-checked="true"`
      : whole));
}

/**
 * The app bar: brand, the three centred nav pills, the three theme controls.
 *
 * All three theme buttons come from the board verbatim. Hand-drawing a sun and
 * a moon is how two boards end up with different suns, and the icons are the
 * only part of a theme control anybody looks at.
 */
export function AppBar({ here, theme = "system", home = "/", stopped }: {
  here: "home" | "profile" | "settings";
  theme?: Theme;
  /** Where the logo and Home go. The board's rule is that the logo returns to
   *  the board, and the board means the lane the reader was last on. */
  home?: string;
  /**
   * `board_meta.blocked`: why automation is stopped, when it is.
   *
   * **Page-wide and red, above the app bar, on every route**, which is the
   * board's `blocked_banner`. v2 printed the same sentence in bold inside the
   * Found ingest line, so a stopped device read the same as a sweep note and
   * was invisible from every other lane, Settings and Profile. The device
   * writes it when something needs a person -- a saved search the schema
   * refused names itself here -- and clears it when that passes.
   */
  stopped?: string | null;
}) {
  return (
    <>
      {stopped ? (
        <div className="sysbanner" role="alert">
          <strong>Automation is stopped.</strong> {stopped}
        </div>
      ) : null}
      {/* The board is one document whose panels are toggled by a class on
          <body>; v2 serves them as separate routes, so each route declares
          which view it is or the page renders blank. The inline script covers
          a document load, before paint. `RouteBody` covers a client
          navigation, which the script cannot: React does not execute a script
          it inserts after hydration. Both are needed and both are idempotent.
          See `_ui/RouteBody.tsx`. */}
      {here !== "home" ? (
        <script
          dangerouslySetInnerHTML={{
            __html: `document.body.classList.add(${JSON.stringify(here + "-view")});`,
          }}
        />
      ) : null}
      <RouteBody view={here} />
      <span {...raw(DEFS)} />
      <header className="appbar">
        {/* `Link`, not `<a>`. A plain anchor is a document load: the blank
            second, the hero re-render and the mascot replaying its draw-on
            every time, which is items 1 and 2 together. A client navigation
            keeps the layout mounted, so the theme is resolved once per
            document rather than once per click. */}
        <Link className="brand" href={home} aria-label="Jobby home" {...raw(LOCKUP)} />
        <nav className="primary-nav">
          <Link href={home} {...(here === "home" ? { "aria-current": "page" as const } : {})}>
            Home
          </Link>
          <Link href="/profile" {...(here === "profile" ? { "aria-current": "page" as const } : {})}>
            Profile
          </Link>
          <Link href="/settings" {...(here === "settings" ? { "aria-current": "page" as const } : {})}>
            Settings
          </Link>
        </nav>
        {/* **The element the stylesheet places must be the element React
            renders.** `.appbar` is `grid-template-columns: 1fr auto 1fr` and
            `.themeseg` carries `justify-self: end`, which applies to a grid
            *item*: a `<span>` around it made the span the item, the rule
            selected a child, and the controls sat against the nav instead of
            the right edge. The props are generated with the markup so they
            cannot drift from what the board emits. */}
        <div {...THEMESEG_PROPS} {...raw(themeSegment(theme))} />
      </header>
    </>
  );
}

/**
 * The board's own stamp: who this is for, and how fresh the data is.
 *
 * **`clock.now_label()`'s format, and its timezone.** The board writes
 * `02 Sep 2026, 13:55 EDT` from `America/New_York`, and a stamp that renders
 * in the viewer's zone would disagree with every other time on the page --
 * "next run 15:45 EDT" in the scan banner is already Eastern, because the
 * device that runs the scans is.
 *
 * `%Z` gives EDT or EST by the date, which is what `timeZoneName: "short"`
 * produces; hardcoding either is wrong for half the year.
 *
 * **`en-US`, and that is not a default.** `en-GB` abbreviates September as
 * "Sept" and renders the zone as "GMT-4"; `en-US` gives "Sep" and "EDT",
 * which is what `%b` and `%Z` produce. Checked on both sides of the DST
 * boundary, since a zone name that is right in September and wrong in January
 * is the failure this parameter invites.
 */
export function boardStamp(when: string | null): string | null {
  if (!when) return null;
  const at = new Date(when);
  if (Number.isNaN(at.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", day: "2-digit", month: "short",
    year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    timeZoneName: "short",
  }).formatToParts(at);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("day")} ${get("month")} ${get("year")}, `
    + `${get("hour")}:${get("minute")} ${get("timeZoneName")}`;
}
