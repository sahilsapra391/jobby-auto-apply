"use client";

/**
 * The two location controls that change the form and post nothing.
 *
 * **They are client-side by nature, not by shortcut.** Clear all unchecks every
 * country box and Only checks one and unchecks the rest; `SubmitOnChange` then
 * submits the GET form and the lane re-queries. So these need a handler and no
 * route, which is why a guard that equates "reaches nothing" with "unwired"
 * had to learn a third notion of driven.
 *
 * **This used to say "the write is Save search, which reads the form
 * afterwards", and that stopped being true in #234.** There is no Save search
 * and no write; a country box is a `loc=` parameter. The sentence outlived the
 * mechanism and kept an `enabled` prop alive that dimmed the whole picker
 * behind `live_board` for a write it no longer performs.
 *
 * The Worldwide checkbox is deliberately left alone. It is `readOnly` and
 * marked decorative: an aggregate the server renders over the country boxes,
 * not an input anybody stores. Setting it here would be writing to a display,
 * and it corrects itself on the next render.
 *
 * They operate on the checkboxes by name rather than by holding React state,
 * because the picker is a server component rendering 199 countries inside a
 * `<form method="get">` and lifting all of it into client state to move two
 * buttons would be a rewrite for the sake of the buttons.
 */

/** Every country checkbox in the picker this control belongs to.
 *
 * `.jb-location-picker` is the `<details>` the picker renders. The first
 * version of this asked for `.jb-location`, which is not a class this app has,
 * so `closest` returned null and the scope silently widened to the whole
 * document. It would have worked and for the wrong reason, and on a page with
 * two pickers it would have cleared both. A selector that matches nothing is
 * the same defect as a button that posts nothing, one layer down. */
function boxes(from: HTMLElement): HTMLInputElement[] {
  const scope: ParentNode = from.closest(".jb-location-picker") ?? document;
  return Array.from(
    scope.querySelectorAll<HTMLInputElement>("input[data-country-name]"));
}

/**
 * Say that the selection moved, once.
 *
 * **Setting `.checked` from script fires nothing.** `SubmitOnChange` listens
 * for `change` on the document, so Clear all and Only rearranged 203 boxes and
 * the lane sat exactly where it was: two controls that looked like they
 * applied on click and did not, on a filter block whose whole promise is that
 * everything applies on click. The board's own versions call `render()` on the
 * spot; this is the same move through the event the form already listens for.
 *
 * One event from one box, not 203: the listener debounces, but 203 events for
 * one press is 203 wake-ups to coalesce and the first box speaks for all of
 * them. A picker with no boxes at all dispatches nothing rather than reaching
 * for the form, because there is no selection to have moved.
 */
function announce(all: HTMLInputElement[]) {
  all[0]?.dispatchEvent(new Event("change", { bubbles: true }));
}

export function ClearAllLocations() {
  return (
    <button type="button" className="jb-location-clear"
            onClick={(e) => {
              const all = boxes(e.currentTarget);
              for (const box of all) box.checked = false;
              announce(all);
            }}>
      Clear all
    </button>
  );
}

export function OnlyThisCountry({ name }: { name: string }) {
  return (
    <button type="button" className="jb-only"
            aria-label={`Select only ${name}`}
            onClick={(e) => {
              const all = boxes(e.currentTarget);
              for (const box of all) {
                box.checked = box.dataset.countryName === name;
              }
              announce(all);
            }}>
      Only
    </button>
  );
}
