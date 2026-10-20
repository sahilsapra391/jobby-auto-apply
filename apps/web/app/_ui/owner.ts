import { cookies } from "next/headers";
import { ownerConfig, SESSION_COOKIE, verifySession, type OwnerConfig } from "./ownerToken";

/**
 * Who is asking, for the one place on the site that cares.
 *
 * **`isOwner()` gates the resume wording editor and nothing else**: its page
 * section, `GET /api/profile/wording` and `POST /api/profile/wording`. The rest
 * of the site is public by the owner's decision, recorded in `middleware.ts`.
 * A second caller of this function is a second owner-only surface, and that is
 * a product decision rather than a refactor.
 *
 * Not a client module, and it cannot be one: it reads the HttpOnly cookie and
 * the signing secret, neither of which a browser may see.
 */

export function config(): OwnerConfig {
  return ownerConfig(process.env);
}

/** True only for a request carrying a live, correctly signed owner cookie. */
export async function isOwner(): Promise<boolean> {
  const cfg = config();
  if (!cfg.ok) return false;
  const jar = await cookies();
  return verifySession(cfg, jar.get(SESSION_COOKIE)?.value, Date.now());
}
