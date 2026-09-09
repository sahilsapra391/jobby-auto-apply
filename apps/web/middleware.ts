import { NextRequest, NextResponse } from "next/server";

/**
 * Reads are public. Writes must come from this board.
 *
 * **The password gate is gone, at the owner's explicit instruction**, so that
 * anyone who opens the site sees what he sees. It was HTTP Basic against
 * `DASHBOARD_PASSWORD` until 2026-09-09. `deploy/api/board.js` dropped the
 * identical gate on 2026-08-31 for the identical reason, and the two boards
 * now have one policy between them rather than two to keep in step.
 *
 * **What is public, stated once here rather than inferred from the absence of
 * a check.** The board names companies, roles, match scores and the profile.
 * `/api/artifact` serves rendered resume and cover-letter PDFs to anyone who
 * knows a filename, and those carry a home address and a phone number.
 * `/api/profile` serves the profile. Vercel's SSO is `all_except_custom_domains`,
 * so v2.jobby.fyi has no platform gate behind this one either. Nothing is
 * between the internet and this data, and that is the decision rather than an
 * oversight.
 *
 * **Removing the gate is not removing the write protection, and conflating the
 * two is how a public board becomes a public control panel.** Every route under
 * `/api` was relying on the Basic gate and carries no check of its own: nine of
 * them, zero occurrences of `x-jobby-request` or a 403 between them. Deleting
 * this file would have left `POST /api/applications` open to the internet,
 * which is Drop and Apply on real applications under a real name. So the mutation
 * guard lands in the same commit, ported from `board.js`'s
 * `rejectCrossSiteMutation` rather than reinvented.
 *
 * It sits in the middleware rather than in each route because a new route
 * inherits it. A per-route check is a list somebody has to remember to extend,
 * and what such a list forgets is by definition the route nobody thought of.
 */

/**
 * A mutation has to come from this origin, say it is JSON, and carry the
 * board's own header.
 *
 * The header is the load-bearing one and it has cost this project five days
 * before: `found_lane.py` grew its own `post()` without it on 2026-08-19 and
 * Watch, Pass and Apply from Found all answered 403 until 2026-08-23, with a
 * toast that named no reason. `tests/v2_writes_carry_the_token.test.mjs` is
 * why that cannot recur silently here.
 */
function refuseCrossSiteMutation(request: NextRequest): NextResponse | null {
  if (request.method === "GET" || request.method === "HEAD") return null;

  if (!sameOrigin(request)) {
    return json({ ok: false, error: "cross-site request refused" }, 403);
  }
  const type = (request.headers.get("content-type") || "").toLowerCase();
  if (!type.startsWith("application/json")) {
    return json({ ok: false, error: "application/json required" }, 415);
  }
  if (request.headers.get("x-jobby-request") !== "1") {
    return json({ ok: false, error: "request token missing" }, 403);
  }
  return null;
}

/**
 * Did this POST come from a page on this host?
 *
 * **`new URL(request.url).origin` is not the browser\'s origin and comparing
 * against it refused every button.** `board.js` can use it because it runs as a
 * Vercel function handling the request as sent; Next\'s middleware sees a URL
 * the framework has normalised, so behind the traced bundle its host is not the
 * host the client asked for, and the comparison is false for every real
 * request. The deployed-shape check caught it: seven problems, one cause, with
 * the unit tests and `next build` both green over a middleware that refused
 * Apply, Drop, Watch, Retry and every provider control.
 *
 * `Origin` and `Host` both come from the client on the same request, so
 * comparing them asks exactly the question worth asking and needs nothing about
 * how the framework rewrote the URL. `x-forwarded-host` wins where a proxy set
 * it, which is what Vercel does in front of this.
 *
 * A missing or unparseable `Origin` is refused rather than waved through. Every
 * browser sends it on a cross-origin POST and on a same-origin `fetch`; what
 * omits it is a non-browser client, which has no business writing here.
 */
function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  const host = request.headers.get("x-forwarded-host")
            ?? request.headers.get("host");
  return Boolean(host) && originHost === host;
}

function json(body: unknown, status: number): NextResponse {
  return new NextResponse(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export function middleware(request: NextRequest) {
  return refuseCrossSiteMutation(request) ?? NextResponse.next();
}

export const config = {
  // Everything, so a route added tomorrow is covered without an edit here.
  matcher: "/:path*",
};
