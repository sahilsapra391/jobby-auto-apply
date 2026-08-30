import { NextRequest, NextResponse } from "next/server";

/**
 * Nothing on this board is served to an unauthenticated request. Not a page,
 * not a chunk, not a placeholder.
 *
 * **This exists because a custom domain is exempt from the protection the rest
 * of the deployment has.** The Vercel team's SSO is set to
 * `all_except_custom_domains`, which is what lets jobby.fyi answer a scripted
 * request at all; the corollary nobody states is that v2.jobby.fyi, the moment
 * it is attached, is a public URL. Preview and *.vercel.app URLs stay behind
 * SSO and the custom domain does not, so the platform gate that covers this
 * app during development is exactly the one that stops covering it in
 * production. An app-level gate is therefore not a second layer here. It is
 * the only layer.
 *
 * It is the same mechanism `deploy/api/page.py` uses -- HTTP Basic against
 * DASHBOARD_PASSWORD, password only, username ignored -- so there is one
 * credential and one behaviour across both boards rather than two things to
 * keep in step.
 *
 * **A missing password is a 503, never an open door.** That is the half worth
 * being loud about: an unset environment variable is the single most likely
 * way this ends up serving the world, because it looks like nothing at all. It
 * happens on a fresh project, on a renamed variable, on a preview environment
 * somebody forgot to populate. Falling open there would be silent and would
 * look exactly like working. The Python board already refuses this way; the
 * refusal is copied rather than improved on.
 *
 * The matcher is deliberately everything. Exempting `_next/static` would be
 * the ordinary optimisation and it is wrong here: those chunks are the board's
 * own code, and a lane's shape is inferable from them.
 */

const REALM = 'Basic realm="Application board", charset="UTF-8"';

/**
 * Constant time, on a runtime with no timingSafeEqual.
 *
 * Edge has no node:crypto, so both sides are HMAC'd under a key generated for
 * this comparison and the fixed-length digests are compared instead. Two
 * digests are always 32 bytes whatever the inputs were, so the loop's timing
 * carries nothing about the password, and an attacker cannot predict the key.
 * A plain `a === b` would return on the first differing byte and leak the
 * prefix a character at a time.
 */
async function equals(a: string, b: string): Promise<boolean> {
  const key = await crypto.subtle.importKey(
    "raw",
    crypto.getRandomValues(new Uint8Array(32)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([
    crypto.subtle.sign("HMAC", key, enc.encode(a)),
    crypto.subtle.sign("HMAC", key, enc.encode(b)),
  ]);
  const u = new Uint8Array(x);
  const v = new Uint8Array(y);
  let diff = u.length ^ v.length;
  for (let i = 0; i < u.length && i < v.length; i++) diff |= u[i] ^ v[i];
  return diff === 0;
}

function supplied(header: string | null): string | null {
  if (!header || !header.toLowerCase().startsWith("basic ")) return null;
  try {
    const decoded = atob(header.slice(6).trim());
    const colon = decoded.indexOf(":");
    return colon === -1 ? "" : decoded.slice(colon + 1);
  } catch {
    return null;
  }
}

export async function middleware(request: NextRequest) {
  const expected = process.env.DASHBOARD_PASSWORD;
  if (!expected) {
    // Never 200, never 401-with-a-way-in. The board is misconfigured and says
    // so, in the words the Python board already uses.
    return new NextResponse("Dashboard password is not configured.\n", {
      status: 503,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }

  const password = supplied(request.headers.get("authorization"));
  if (password === null || !(await equals(password, expected))) {
    return new NextResponse("Authentication required.\n", {
      status: 401,
      headers: {
        "content-type": "text/plain; charset=utf-8",
        "www-authenticate": REALM,
      },
    });
  }

  return NextResponse.next();
}

export const config = {
  // Everything. See the note above on why _next/static is not exempt.
  matcher: "/:path*",
};
