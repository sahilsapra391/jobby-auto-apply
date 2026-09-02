import type { Metadata } from "next";
// The board's own stylesheet and its own head, generated from dashboard.py by
// scripts/sync_v2_styles.py. Not hand-written and not to be edited: v2 has to
// look one for one identical to the board it replaces, and a hand-port
// delivers that once and drifts from then on.
import "./board.css";
import { cookies } from "next/headers";
import { HEAD } from "./chrome.generated";
import { ThemeControls } from "./_ui/ThemeControls";
import { CompanyLogos } from "./_ui/CompanyLogos";
import {
  THEME_COOKIE, THEME_MIGRATION, htmlTheme, themeColorMedia, themeFrom,
} from "./_ui/theme";

/**
 * The title comes out of the generated head rather than being retyped.
 *
 * Next owns `<title>`, `<meta charset>` and the viewport, so those three are
 * lifted out of the generated markup and declared through its API instead;
 * everything else in that string (both `theme-color` metas, the three inlined
 * icons and the manifest link) is emitted verbatim. Retyping the title here
 * is how the two boards end up disagreeing about their own name.
 */
const titleMatch = /<title>([^<]*)<\/title>/.exec(HEAD);
const BOARD_TITLE = titleMatch ? titleMatch[1] : "Jobby";

const HEAD_REST = HEAD
  .replace(/<meta charset=[^>]*>/i, "")
  .replace(/<title>[^<]*<\/title>/i, "")
  .replace(/<meta name="viewport"[^>]*>/i, "")
  .trim();

export const metadata: Metadata = { title: BOARD_TITLE };
export const viewport = { width: "device-width", initialScale: 1 };

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // **Resolved here, in the markup, and nowhere else.** See `_ui/theme.ts`:
  // an explicit choice becomes `data-theme` and `system` becomes its absence,
  // which hands the decision to `prefers-color-scheme` in the stylesheet. Both
  // are settled by the time the first frame is composed.
  const theme = themeFrom((await cookies()).get(THEME_COOKIE)?.value);
  const stamped = htmlTheme(theme);
  return (
    <html lang="en"
          {...(stamped ? { "data-theme": stamped } : {})}
          // The hero flourish is a first-paint thing and the class that gates
          // it used to be added by the boot script. A document load is the
          // only time it should appear, and this is the only place that runs
          // exactly once per document load.
          className="jb-intro">
      <head>
        {/* Both theme-color metas, three inlined icons, the manifest link. */}
        <HeadMarkup theme={theme} />
        {/* The old store, copied across once. Not a resolver: it returns
            immediately when the cookie exists, which is every load after the
            first. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_MIGRATION }} />
      </head>
      <body>
        {children}
        <ThemeControls theme={theme} />
        {/* In the layout, not the page: every route draws marks,
            and the observer has to outlive a lane switch. */}
        <CompanyLogos />
      </body>
    </html>
  );
}

function HeadMarkup({ theme }: { theme: Parameters<typeof themeColorMedia>[0] }) {
  // The two `theme-color` metas are media-scoped in the board's own head for
  // the no-JS case. An explicit choice pins one on and the other off, which
  // `paintTheme` used to do after hydration; doing it in the markup means the
  // address bar is right on the first frame as well.
  const media = themeColorMedia(theme);
  let seen = 0;
  return <>{parseHead(HEAD_REST).map((el) => {
    if (el.type !== "meta") return el;
    const props = el.props as Record<string, string>;
    if (props.name !== "theme-color") return el;
    const next = media[seen++] ?? props.media;
    return { ...el, props: { ...props, media: next } } as typeof el;
  })}</>;
}

/**
 * React will not render a raw HTML string inside `<head>`, and
 * `dangerouslySetInnerHTML` on a wrapper would put a real element around tags
 * that may not have one. So the generated markup is turned into elements.
 *
 * Only the two tag shapes the board's head actually contains are handled, and
 * anything else raises at build time rather than being dropped: a head that
 * silently emits less is the favicon outage again.
 */
function parseHead(markup: string) {
  const out: React.ReactElement[] = [];
  const tag = /<(meta|link)\b([^>]*)>/gi;
  let m: RegExpExecArray | null;
  let consumed = 0;
  while ((m = tag.exec(markup))) {
    consumed += m[0].length;
    const props: Record<string, string> = {};
    const attr = /([a-zA-Z-]+)="([^"]*)"/g;
    let a: RegExpExecArray | null;
    while ((a = attr.exec(m[2]))) {
      const key = a[1] === "class" ? "className" : a[1];
      props[key] = a[2];
    }
    out.push(m[1].toLowerCase() === "meta"
      ? <meta key={out.length} {...props} />
      : <link key={out.length} {...props} />);
  }
  const leftover = markup.replace(/<(meta|link)\b[^>]*>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "").trim();
  if (leftover) {
    throw new Error(
      `the board's head carries markup this layout cannot emit: ${leftover.slice(0, 120)}`);
  }
  if (!consumed) throw new Error("the generated head produced no tags");
  return out;
}
