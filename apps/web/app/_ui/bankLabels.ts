/**
 * The Memory Bank's display names, in a module that is not `"use client"`.
 *
 * **Both sides of the RSC boundary read this, which is why it is here.** The
 * category chip strip is rendered by the server component and the chip on each
 * row is rendered by a client one, and the lookup is the same lookup. It has
 * now been in the wrong place twice on the same afternoon, in opposite
 * directions, and each time the page 500'd only when it had a row to render:
 *
 *   `page.tsx` held it and passed it down     Functions cannot be passed
 *   as a prop to a client component.          directly to Client Components.
 *
 *   `ProfileSurface.tsx` held it, and         Attempted to call
 *   `page.tsx` called it on the server.       categoryLabel() from the server
 *                                             but categoryLabel is on the
 *                                             client.
 *
 * CLAUDE.md already states the rule, from a constant whose declaration in a
 * `"use client"` file made `cookies().get(undefined)` a silent miss: a value a
 * server component reads must be declared in a module that is not
 * `"use client"`. A function is that rule's sharper case, because it fails
 * loudly instead of silently, and only when a row exists to render it.
 *
 * `bank.generated.ts` is a plain constant module generated from the board, so
 * there is nothing to move but the two lines that read it.
 */

import { BANK_CATEGORIES } from "../bank.generated";

/** The display name for a category slug, or the slug when it has none. */
export function categoryLabel(slug: string): string {
  return BANK_CATEGORIES.find(([k]) => k === slug)?.[1] ?? slug;
}
