/**
 * The theme, resolved on the server before a single byte is painted.
 *
 * **Three states, and the server can settle all three.** That is worth stating
 * because it sounds like it cannot: `system` means "follow the OS", and the OS
 * preference is a client fact no cookie carries. But the server does not need
 * to know what `system` resolves to. It needs to emit markup whose resolution
 * is complete at first paint, and for `system` that markup is *no*
 * `data-theme` attribute at all, which hands the decision to
 * `prefers-color-scheme` in the stylesheet. A CSS media query is applied by the
 * time the first frame is composed. So:
 *
 *   cookie `light` or `dark`  ->  the server stamps `data-theme`
 *   cookie absent (system)    ->  the server stamps nothing, and CSS decides
 *
 * Both are resolved before paint, neither needs JavaScript, and there is
 * exactly one resolution per document.
 *
 * **What this replaces is not a bug anyone found; it is a second resolution.**
 * The theme was read from `localStorage` by a boot script in the head, which is
 * itself before paint and correct. Driven headless across twelve loads under
 * both OS schemes it was deterministic every time, so the six different results
 * a reader saw in one pass are not reproduced here and their cause is not
 * identified. What is certain is the shape: every lane switch was a full
 * document load, so the theme was resolved again on each one, from a store the
 * document had to run script to read. Removing the reload (client-side lane
 * switching) and removing the script (this file) leaves nothing to disagree.
 *
 * **A cookie rather than `localStorage` because the server has to see it.**
 * `localStorage` is unreachable from a server render by construction, so a
 * preference kept only there can never be in the first byte.
 */

/** The cookie the board's own storage key becomes. */
export const THEME_COOKIE = "jobby.theme";

export type Theme = "light" | "dark" | "system";

/** A cookie value, or `system` for anything else including absence. */
export function themeFrom(value: string | undefined): Theme {
  return value === "light" || value === "dark" ? value : "system";
}

/**
 * What `<html data-theme>` should be, or null to let CSS decide.
 *
 * Null is not "unknown". It is the positive statement that this reader follows
 * their operating system, which the stylesheet already implements.
 */
export const htmlTheme = (theme: Theme) => (theme === "system" ? null : theme);

/**
 * The `media` attribute for each of the two `theme-color` metas.
 *
 * The board's own rule, moved from `paintTheme` to the server: under `system`
 * the pair stays media-scoped so the browser picks; under an explicit choice
 * one is forced on and the other off. Doing it in the markup means the address
 * bar is right on the first frame too, rather than after hydration.
 */
export function themeColorMedia(theme: Theme): [string, string] {
  if (theme === "light") return ["all", "not all"];
  if (theme === "dark") return ["not all", "all"];
  return ["(prefers-color-scheme:light)", "(prefers-color-scheme:dark)"];
}

/**
 * A one-time migration from the old store, and nothing else.
 *
 * A reader who chose Light before this shipped has it in `localStorage` and not
 * in a cookie, so the server would render them `system` once. This copies it
 * across **during the same frame the boot script already ran in**, so that
 * single load is still correct, and every load after it is server-resolved
 * because the cookie now exists.
 *
 * It deliberately does not run when the cookie is present: the cookie is the
 * store, and a script that keeps writing one from the other is the second
 * resolution this file exists to remove. Delete this once no reader can still
 * be carrying the old key.
 */
export const THEME_MIGRATION = `
(function () {
  try {
    if (document.cookie.indexOf('${THEME_COOKIE}=') !== -1) return;
    var t = localStorage.getItem('${THEME_COOKIE}');
    if (t !== 'light' && t !== 'dark') return;
    document.documentElement.dataset.theme = t;
    document.cookie = '${THEME_COOKIE}=' + t
      + ';path=/;max-age=31536000;samesite=lax';
  } catch (e) {}
})();
`.trim();


/**
 * Which lane the reader was last on, so the logo comes back to it.
 *
 * **Declared here rather than in `RememberLane.tsx`, and that is not tidying.**
 * It was exported from that file, which is `"use client"`, and a server
 * component importing a value from a client module does not get the value: the
 * bundler replaces the module with a client reference, so `cookies().get(...)`
 * was called with `undefined` and quietly returned nothing. The theme cookie
 * beside it worked, because its constant lives in this file, which is why the
 * symptom was a logo that ignored the lane while the theme was perfect.
 *
 * Nothing throws and nothing warns. `jar.get(undefined)` is simply a miss.
 */
export const LANE_COOKIE = "jobby.lane";
