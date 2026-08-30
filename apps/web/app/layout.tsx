import type { Metadata } from "next";
import "./globals.css";

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
