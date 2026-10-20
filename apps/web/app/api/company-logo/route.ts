import { NextRequest, NextResponse } from "next/server";
import { candidates, faviconFor, trustedSource } from "../../_ui/logo_source";

/**
 * An employer's mark, fetched server-side so the browser only ever loads
 * same-origin images.
 *
 * **Every card on v2's Found lane asked for this and got a 404.** The markup
 * came across in the Soft Premium port and the route did not, so the `<img>`
 * failed on all fifty, the CSS took it to `opacity: 0`, and the monogram
 * underneath showed. It looked like a design choice. Measured on the Python
 * board the same day this was written, 891 of 906 rendered records resolve to
 * a real logo, so v2 was showing a monogram for every company where the board
 * it replaces shows one for about one in six.
 *
 * Ported from the original board's logo route, and the trusted host set is generated
 * from `company_logos.TRUSTED_SOURCE_HOSTS` rather than typed again: a host
 * removed there and left in a hand-written copy here is an open redirect
 * nobody is watching.
 *
 * `alt=1` means "try the other TLDs of this label". The list is a constant in
 * the code rather than spelled out per job, which is what kept the Python
 * board's Found payload inside its page-weight cap; v2 has no such cap and
 * inherits the shape anyway, because the split is one contract.
 */
export const dynamic = "force-dynamic";

const miss = () =>
  new NextResponse("No company logo.\n", {
    status: 404, headers: { "content-type": "text/plain; charset=utf-8" },
  });

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const source = trustedSource(params.get("src") ?? "");
  const targets = source
    ? [source]
    : candidates(params.get("domain") ?? "", params.get("alt") === "1")
        .map(faviconFor);
  if (!targets.length) return miss();

  for (const target of targets) {
    try {
      const upstream = await fetch(target, { redirect: "follow" });
      // A redirect off a trusted host is no longer a trusted host. Checked
      // after the follow, because that is where it could have gone.
      if (source && !trustedSource(upstream.url)) return miss();
      const type = (upstream.headers.get("content-type") ?? "")
        .split(";")[0].toLowerCase();
      if (!upstream.ok || !type.startsWith("image/")) continue;
      const bytes = await upstream.arrayBuffer();
      // A favicon endpoint answers 404 for a domain it does not know, so a
      // wrong guess costs a monogram rather than a stranger's mark. The floor
      // catches the handful of near-empty replies that are not an icon.
      if (bytes.byteLength < 100 || bytes.byteLength > 750_000) continue;
      return new NextResponse(bytes, {
        status: 200,
        headers: {
          "content-type": type,
          // Private: this is one reader's board behind a password, and a
          // shared cache has no business holding what it looked at.
          "cache-control": "private, max-age=86400",
          "x-content-type-options": "nosniff",
        },
      });
    } catch {
      // Try the next candidate rather than giving up on the company.
    }
  }
  return miss();
}
