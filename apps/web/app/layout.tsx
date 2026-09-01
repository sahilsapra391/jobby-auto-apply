import type { Metadata } from "next";
// The board's own stylesheet and its own head, generated from dashboard.py by
// scripts/sync_v2_styles.py. Not hand-written and not to be edited: v2 has to
// look one for one identical to the board it replaces, and a hand-port
// delivers that once and drifts from then on.
import "./board.css";
import { HEAD, THEME_BOOT, THEME_WIRE } from "./chrome.generated";

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

/**
 * Run a block of the board's own JavaScript once, after the DOM exists.
 *
 * The board runs its scripts at the end of its document and can assume the
 * markup above them. v2 cannot: React hoists script elements, so the same
 * block runs against an empty document unless it is deferred. Wrapping rather
 * than editing keeps the inner text byte-identical to what the generator
 * pulled out of `dashboard.build`.
 */
const DEFER_UNTIL_READY = (js: string, key: string) => `(function(){
  var k = ${JSON.stringify("__jb_ran_")} + ${JSON.stringify(key)};
  if (window[k]) return;
  function go(){ if (window[k]) return; window[k] = 1; ${js}
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", go);
  } else { go(); }
})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        {/* Both theme-color metas, three inlined icons, the manifest link. */}
        <HeadMarkup />
        {/* Before first paint. A deferred version paints twice, and a dark
            reader sees a frame of cream. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        {children}
        {/* After the controls exist, never before.

            **React 19 hoists `<script>` into the head**, so placing the
            board's own block at the end of the body is not enough: it ran
            before any `.themebtn` was in the document, bound to an empty
            NodeList, and left three controls that render and do nothing --
            with `paintTheme` defined on `window`, which is what makes the
            symptom read as "the script never ran" when it had.

            The wrapper is v2's and the body inside it is the board's,
            unedited. It defers to DOMContentLoaded when the document is still
            parsing, and it refuses to run twice, because React emits this
            element in both places and a second pass would bind every listener
            again. */}
        <script dangerouslySetInnerHTML={{ __html: DEFER_UNTIL_READY(THEME_WIRE, "theme") }} />
      </body>
    </html>
  );
}

function HeadMarkup() {
  return <>{parseHead(HEAD_REST)}</>;
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
