import { createHash, createHmac, timingSafeEqual } from "node:crypto";

/**
 * The owner's session, as pure functions: no cookies, no request, no clock of
 * its own. `_ui/owner.ts` reads the cookie and calls these, and
 * `tests/owner_session.test.mjs` transpiles this file and runs them, because a
 * gate that is only read is a gate nobody has seen refuse anything.
 *
 * **It gates one thing.** The site is public with no sign-in, at the owner's
 * instruction. The single exception is the resume wording editor, its read
 * route and its write route, because the wording is his employment history in
 * his own words and is kept off every public surface. Nothing else on the site
 * asks who you are, and nothing else may start asking through this module.
 *
 * Two environment variables, both set by the owner on the Vercel project:
 *
 *     JOBBY_OWNER_PASSWORD   what the sign-in page asks for
 *     JOBBY_SESSION_SECRET   the HMAC key the session cookie is signed with
 *
 * Either one missing, or too short to be worth guessing at, and the editor is
 * simply unavailable and says how to turn it on. There is no default for
 * either and no variable that switches the check off.
 */

export const SESSION_COOKIE = "jobby.owner";
export const SESSION_DAYS = 30;
export const SESSION_SECONDS = SESSION_DAYS * 24 * 60 * 60;

/** Below these, the variable is treated as unset rather than as weak. */
export const MIN_PASSWORD = 12;
export const MIN_SECRET = 32;

export type OwnerConfig =
  | { ok: true; password: string; secret: string }
  | { ok: false; why: string };

/** What the environment says, read on every call so a rotation takes effect. */
export function ownerConfig(env: Record<string, string | undefined>): OwnerConfig {
  const password = env.JOBBY_OWNER_PASSWORD ?? "";
  const secret = env.JOBBY_SESSION_SECRET ?? "";
  const missing: string[] = [];
  if (password.length < MIN_PASSWORD) {
    missing.push(`JOBBY_OWNER_PASSWORD (at least ${MIN_PASSWORD} characters)`);
  }
  if (secret.length < MIN_SECRET) {
    missing.push(`JOBBY_SESSION_SECRET (at least ${MIN_SECRET} random characters)`);
  }
  if (missing.length) {
    return {
      ok: false,
      why: `The resume editor is off on this deployment. To turn it on, set `
         + `${missing.join(" and ")} in the Vercel project's environment `
         + `variables and redeploy.`,
    };
  }
  return { ok: true, password, secret };
}

/**
 * The signing key, bound to the password as well as the secret.
 *
 * So rotating either variable ends every session signed before it, which is
 * what a person changing a leaked password expects and would not get if the
 * cookie only knew the secret.
 */
function signingKey(config: { password: string; secret: string }): Buffer {
  const bound = createHash("sha256").update(config.password, "utf8").digest("hex");
  return createHmac("sha256", config.secret)
    .update(`jobby-owner-key.v1:${bound}`, "utf8").digest();
}

function signature(config: { password: string; secret: string }, expires: number) {
  return createHmac("sha256", signingKey(config))
    .update(`jobby-owner.v1.${expires}`, "utf8").digest("base64url");
}

/** Equal-length digests compared in constant time, whatever the inputs. */
function sameSecret(a: string, b: string): boolean {
  const da = createHash("sha256").update(a, "utf8").digest();
  const db = createHash("sha256").update(b, "utf8").digest();
  return timingSafeEqual(da, db);
}

/** Whether the typed password is the owner's. Constant time in its length. */
export function passwordMatches(config: { password: string }, given: unknown): boolean {
  if (typeof given !== "string" || !given) return false;
  return sameSecret(given, config.password);
}

/** A cookie value good until `nowMs` plus thirty days. */
export function signSession(config: { password: string; secret: string },
                            nowMs: number): string {
  const expires = Math.floor(nowMs / 1000) + SESSION_SECONDS;
  return `v1.${expires}.${signature(config, expires)}`;
}

/**
 * Whether a cookie value is a live owner session.
 *
 * Every refusal is the same `false`: unsigned, tampered, signed under another
 * secret or password, expired, or malformed. A caller does not need to know
 * which, and a response that said which would be a tool for whoever is
 * guessing.
 */
export function verifySession(config: { password: string; secret: string },
                              token: string | undefined | null,
                              nowMs: number): boolean {
  if (typeof token !== "string") return false;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return false;
  if (!/^\d{1,12}$/.test(parts[1])) return false;
  const expires = Number(parts[1]);
  // Checked after the signature would be the more paranoid order, but the
  // signature check is constant time either way and an expired token is not
  // a secret.
  if (!Number.isSafeInteger(expires) || expires * 1000 <= nowMs) return false;
  // A token claiming to live longer than a session can is one nobody here
  // minted, whatever its signature says.
  if (expires * 1000 > nowMs + SESSION_SECONDS * 1000 + 60_000) return false;
  return sameSecret(parts[2], signature(config, expires));
}

/** The `Set-Cookie` attributes, written once for sign-in and sign-out. */
export function cookieAttributes(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: true,
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
