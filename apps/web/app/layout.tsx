import type { Metadata } from "next";
// The board's own stylesheet, generated from dashboard.py by
// scripts/sync_v2_styles.py. Not hand-written and not to be edited: v2 has to
// look one for one identical to the board it replaces, and a hand-port
// delivers that once and drifts from then on.
import "./board.css";

export const metadata: Metadata = {
  title: "Jobby",
  description: "Job applications, running.",
};

/**
 * The theme script runs before first paint, so a dark-mode reader never sees a
 * cream flash. It reads the saved override and stamps data-theme; absent an
 * override it stamps nothing, which is the third state and leaves the page
 * following the operating system.
 *
 * Inline and blocking on purpose. Any deferred version of this paints twice.
 */
const THEME_BOOT = `(function(){try{
  var t=localStorage.getItem("jobby-theme");
  if(t==="dark"||t==="light")document.documentElement.setAttribute("data-theme",t);
}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
