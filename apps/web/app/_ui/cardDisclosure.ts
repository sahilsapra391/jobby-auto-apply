/**
 * The Found card's details disclosure, as plain functions.
 *
 * Kept out of `CardDetails.tsx` so a test can run the rules rather than read
 * them: `tests/card_details_match_the_board.test.mjs` compiles these out and
 * drives them. Two of them were wrong in v2 while the component's docstring
 * claimed rule-for-rule parity with `found_lane.py`, and a claim of parity is
 * not a check of it.
 */

/** What the panel shows, and whether a click is what is holding it there. */
export type Disclosure = { pinned: boolean; shown: boolean };

export const CLOSED: Disclosure = { pinned: false, shown: false };

/**
 * The board's three listeners, from `found_lane.py`, one branch each:
 *
 *   click   pinned = !pinned; show(pinned)
 *   enter   if (!pinned) show(true)
 *   leave   if (!pinned) show(false)
 *
 * A click sets what is shown, it does not add a reason to show it. v2 had
 * `open = pinned || hovered` with the click flipping only `pinned`, so a
 * second click left the panel open for as long as the pointer stayed on the
 * card, which is always, because the button is on the card.
 */
export function disclose(state: Disclosure,
                         event: "click" | "enter" | "leave"): Disclosure {
  if (event === "click") {
    const pinned = !state.pinned;
    return { pinned, shown: pinned };
  }
  if (state.pinned) return state;
  return { pinned: false, shown: event === "enter" };
}

export type Sections = { responsibilities: string[]; requirements: string[] };

/**
 * What `/api/posting` answered, or null when it did not answer with sections.
 *
 * `null` covers a non-ok status (a pruned posting is a 404 on purpose), a
 * body that is not the route's shape, and the caller maps a network error to
 * the same place. Empty lists are not null: they are a posting that was read
 * and has no headings this can split on.
 */
export function readPosting(data: unknown): Sections | null {
  if (!data || typeof data !== "object") return null;
  const body = data as Record<string, unknown>;
  if (body.ok !== true) return null;
  const list = (value: unknown) =>
    Array.isArray(value) ? value.map((v) => String(v ?? "")).filter(Boolean)
      : [];
  return {
    responsibilities: list(body.responsibilities),
    requirements: list(body.requirements),
  };
}

/**
 * The line under the sections, if any.
 *
 * "Does not use headings" is a claim about the posting, so it is drawn only
 * when the posting was read and both halves came back empty. A read that
 * failed says it failed: telling the reader a posting has no headings when
 * nobody looked is a statement about a document drawn from a fault.
 */
export function sectionNote(sections: Sections | null,
                            failed: boolean): string | null {
  if (failed) return "Could not read the posting.";
  if (!sections) return null;
  if (sections.responsibilities.length || sections.requirements.length) {
    return null;
  }
  return "This posting does not use headings this can read.";
}
