import { LOCKUP, DEFS, THEMESEG } from "../chrome.generated";

export const raw = (html: string) => ({ dangerouslySetInnerHTML: { __html: html } });

/**
 * The app bar: brand, the three centred nav pills, the three theme controls.
 *
 * All three theme buttons come from the board verbatim. Hand-drawing a sun and
 * a moon is how two boards end up with different suns, and the icons are the
 * only part of a theme control anybody looks at.
 */
export function AppBar({ here }: { here: "home" | "profile" | "settings" }) {
  return (
    <>
      {/* The board is one document whose panels are toggled by a class on
          <body>: `.settings-page` and `.profile-page` are `display:none` until
          `body.settings-view` or `body.profile-view` is set. v2 serves them as
          separate routes, so each route has to declare which view it is or the
          page renders blank -- which it did, and only a screenshot showed it.
          Set before paint, so nothing flashes. */}
      {here !== "home" ? (
        <script
          dangerouslySetInnerHTML={{
            __html: `document.body.classList.add(${JSON.stringify(here + "-view")});`,
          }}
        />
      ) : null}
      <span {...raw(DEFS)} />
      <header className="appbar">
        <a className="brand" href="/" aria-label="Jobby home" {...raw(LOCKUP)} />
        <nav className="primary-nav">
          <a href="/" {...(here === "home" ? { "aria-current": "page" as const } : {})}>
            Home
          </a>
          <a href="/profile" {...(here === "profile" ? { "aria-current": "page" as const } : {})}>
            Profile
          </a>
          <a href="/settings" {...(here === "settings" ? { "aria-current": "page" as const } : {})}>
            Settings
          </a>
        </nav>
        <span {...raw(THEMESEG)} />
      </header>
    </>
  );
}
