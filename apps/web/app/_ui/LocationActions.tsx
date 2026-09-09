"use client";

/**
 * The two location controls that change the form and post nothing.
 *
 * **They are client-side by nature, not by shortcut.** Clear all unchecks every
 * country box and Only checks one and unchecks the rest; the write is Save
 * search, which reads the form afterwards. So these need a handler and no
 * route, which is why a guard that equates "reaches nothing" with "unwired"
 * had to learn a third notion of driven.
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

export function ClearAllLocations({ enabled, title }: {
  enabled: boolean; title?: string;
}) {
  return (
    <button type="button" className="jb-location-clear"
            disabled={!enabled} title={title}
            onClick={(e) => {
              for (const box of boxes(e.currentTarget)) box.checked = false;
            }}>
      Clear all
    </button>
  );
}

export function OnlyThisCountry({ name, enabled, title }: {
  name: string; enabled: boolean; title?: string;
}) {
  return (
    <button type="button" className="jb-only"
            disabled={!enabled} title={title}
            aria-label={`Select only ${name}`}
            onClick={(e) => {
              for (const box of boxes(e.currentTarget)) {
                box.checked = box.dataset.countryName === name;
              }
            }}>
      Only
    </button>
  );
}
