"use client";

import { useEffect } from "react";
import { LANE_COOKIE } from "./theme";

/**
 * Remembers which lane the reader was on, so the logo comes back to it.
 *
 * **The board's rule is that the logo is the way back to the board, and the
 * board means whichever lane you were looking at.** The old board remembers
 * it; v2 always returned to Found, so a trip to Settings from Submitted came
 * back somewhere else.
 *
 * A cookie rather than `localStorage` because the server renders the link, and
 * a value the server cannot read cannot be in the href. `SameSite=Lax` and no
 * expiry beyond the session: this is a convenience, not a preference, and it
 * should not outlive the browser being closed.
 *
 * The cookie's name lives in `theme.ts`, which is not a client module. It was
 * exported from here, and a server component importing a constant from a
 * `"use client"` file gets a client reference rather than the string, so the
 * cookie lookup missed silently. See that file.
 */
export function RememberLane({ lane }: { lane: string }) {
  useEffect(() => {
    try {
      document.cookie = `${LANE_COOKIE}=${lane};path=/;samesite=lax`;
    } catch {
      // A blocked cookie costs the reader nothing but the memory itself.
    }
  }, [lane]);
  return null;
}
