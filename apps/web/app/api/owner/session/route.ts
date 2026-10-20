import { NextRequest, NextResponse } from "next/server";
import { config, isOwner } from "../../../_ui/owner";
import {
  cookieAttributes, passwordMatches, SESSION_COOKIE, SESSION_SECONDS, signSession,
} from "../../../_ui/ownerToken";

/**
 * Owner sign-in and sign-out, for the resume wording editor and nothing else.
 *
 * It writes no store and orders no work, so it is not behind `live_board`:
 * signing in changes what this browser may read, not anything on the device.
 * It is still a non-GET, so `middleware.ts` refuses it unless it is
 * same-origin JSON carrying `x-jobby-request: 1`, which is what keeps a form
 * on another site from signing a visitor in or out.
 *
 * **A wrong password costs a second.** There is no store to count attempts in
 * across lambdas, so the bound is per request: every refusal waits before
 * answering. With a password of twelve or more characters that is the
 * difference between a guess a millisecond and a guess a second per
 * connection, and the answer names nothing about why it failed.
 */
export const dynamic = "force-dynamic";

const WRONG_PASSWORD_DELAY_MS = 1_000;

/** Whether this browser is signed in, and whether signing in is possible. */
export async function GET() {
  const cfg = config();
  return NextResponse.json(
    { configured: cfg.ok, owner: cfg.ok ? await isOwner() : false,
      ...(cfg.ok ? {} : { why: cfg.why }) },
    { headers: { "cache-control": "no-store" } });
}

export async function POST(request: NextRequest) {
  const cfg = config();
  if (!cfg.ok) {
    return NextResponse.json({ error: cfg.why }, { status: 503 });
  }
  let body: { password?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "body is not JSON" }, { status: 400 });
  }
  if (!passwordMatches(cfg, body?.password)) {
    await new Promise((resolve) => setTimeout(resolve, WRONG_PASSWORD_DELAY_MS));
    return NextResponse.json({ error: "That is not the owner password." },
                             { status: 401 });
  }
  const response = NextResponse.json({ owner: true });
  response.cookies.set(SESSION_COOKIE, signSession(cfg, Date.now()),
                       cookieAttributes(SESSION_SECONDS));
  response.headers.set("cache-control", "no-store");
  return response;
}

/** Sign out. Always succeeds: clearing a cookie nobody had is still clear. */
export async function DELETE() {
  const response = NextResponse.json({ owner: false });
  response.cookies.set(SESSION_COOKIE, "", cookieAttributes(0));
  response.headers.set("cache-control", "no-store");
  return response;
}
