"use client";

import { useEffect } from "react";

/**
 * The filter form applies when you click it, and never when you find a button.
 *
 * **This covered `.jb-basic` only, and the picker is not in it.** The date
 * chips submitted on click and every control inside Advanced -- the whole
 * 199-country picker, the exclude box, the workplace boxes, max experience --
 * did nothing until the reader found Filter. Combined with the country boxes
 * being `readOnly`, ticking a country was inert twice over. The scope is the
 * filter form now, so a control that filters applies.
 *
 * Delegated to `document` for the reason `ThemeControls` documents: the app bar
 * and the filter row are rendered by the page, so a lane switch replaces these
 * nodes while an effect mounted higher up still holds the old ones. That has
 * now caused the same class of bug three times, so nothing here holds a node.
 *
 * **Enter is handled here, and this used to say it did not need to be.** The
 * sentence was "The keyword box is still excluded. It submits on Enter, which a
 * form already does." A form does -- when it has a submit button, or exactly
 * one text field. The HTML spec's implicit submission refuses Enter in a form
 * with no submit button and more than one field that blocks it, and this form
 * has three: the keyword box, "Hide jobs mentioning" and the company names.
 * Removing the Filter button was correct and is what made the sentence false,
 * because Enter only ever worked through the button that was removed. Found
 * from a screen recording on 2026-09-10: "product manager" typed on a real
 * keyboard, Enter pressed, and the URL, the chips and the count all stayed
 * exactly where they were. The cause is measured rather than reproduced: the
 * form has three blocking fields and no submit button.
 *
 * **An earlier version of this comment said the bug was "reproduced with real
 * keystrokes" in the browser pane, and that was false.** The pane's `type`
 * inserts text without firing `keydown`, and its `key` action delivers no DOM
 * keydown at all: a window capture-phase logger recorded zero events for seven
 * typed characters and an Enter. So "pressed Enter, nothing happened" was
 * identical whether the page ignored Enter or the key never arrived. The
 * reproduction proved nothing, and after the fix deployed the same method made
 * the working handler look broken. What does verify this handler is a
 * synthetic `keydown` dispatched at the box: it is `defaultPrevented`, the form
 * submits, and the lane went from 2,421 jobs to 312. That proves the handler
 * and the submit path, not hardware delivery, which only a real keyboard does.
 *
 * Two correct changes, and a property that only existed between them -- the
 * composition failure CLAUDE.md records for the blank Settings page.
 *
 * **Exclude and company names apply on Enter and on leaving the box, not per
 * keystroke, and that is a deliberate difference from the board.** The board
 * filters an embedded slice in the browser, so it re-renders on every `input`
 * for free. Here every apply is a navigation, and one per character would
 * reload the lane under the reader mid-word and take their cursor with it.
 * The keyword box does not apply on leaving it, because the board does not add
 * a keyword on blur either: a half-typed word is not a chip.
 */

/**
 * What Enter in a filter text box should do. Pure and exported so the test
 * compiles it out and runs it, rather than reading the source and asserting
 * what it looks like.
 *
 *   ignore   an IME is mid-composition, where Enter confirms a character; or
 *            the keyword box is empty -- the board's `if (word && ...)` adds
 *            nothing, and a reload that changes nothing is not a response
 *   clear    the keyword is already a chip -- the board dedupes with
 *            `keywords.indexOf(word) === -1` and clears the box regardless
 *   submit   everything else, including an emptied exclude or company box,
 *            because deleting that text and pressing Enter means "stop
 *            filtering on this"
 */
export function enterAction(
  name: string, value: string, chips: readonly string[], composing: boolean,
): "submit" | "clear" | "ignore" {
  if (composing) return "ignore";
  if (name !== "q") return "submit";
  const word = value.trim();
  if (!word) return "ignore";
  if (chips.includes(word)) return "clear";
  return "submit";
}

export function SubmitOnChange() {
  useEffect(() => {
    /**
     * **A debounce is required here rather than a nicety.** "Clear all"
     * unchecks 203 country boxes and each one fires its own `change`, so
     * submitting per event would be 203 navigations for one click. It also
     * makes ticking four countries in a row cost one page load instead of
     * four, which is what "as soon as I click it" should feel like.
     */
    let pending: ReturnType<typeof setTimeout> | null = null;
    const submitSoon = (form: HTMLFormElement) => {
      if (pending) clearTimeout(pending);
      pending = setTimeout(() => { pending = null; form.requestSubmit(); }, 300);
    };
    /** Enter is one deliberate act, so it goes now and takes any pending
     *  debounced submit with it rather than racing it into a second load. */
    const submitNow = (form: HTMLFormElement) => {
      if (pending) { clearTimeout(pending); pending = null; }
      form.requestSubmit();
    };

    const onChange = (event: Event) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      // `change` on a text field fires when focus leaves it with a new value,
      // which is how exclude and company names apply without a reload per
      // keystroke. The keyword box is deliberately not in this list.
      const control = target.closest<HTMLInputElement | HTMLSelectElement>(
        "input[type=radio], input[type=checkbox], select, " +
        "input[name=not], input[name=conames]");
      if (!control) return;
      const form = control.form ?? control.closest("form");
      // Found's filter form, or an application lane's search row, whose one
      // select is the board's `filter_bar` option list.
      if (!form || !(form.classList.contains("jb-filter-form")
                     || form.hasAttribute("data-lane-filter"))) return;

      // A continent box owns its children rather than a value of its own. Its
      // markup already said so ("selects and clears its children") while it was
      // `readOnly` and did nothing, which is the confident-sentence-about-a-
      // mechanism shape this repository keeps meeting.
      const panel = control.closest<HTMLElement>("[data-continent-panel]");
      if (panel && control.hasAttribute("aria-label")
          && control === panel.querySelector("summary input[type=checkbox]")) {
        const key = panel.getAttribute("data-continent-panel");
        const wanted = (control as HTMLInputElement).checked;
        form.querySelectorAll<HTMLInputElement>(
          `input[name="loc"][data-continent-country="${key}"]`)
          .forEach((box) => { box.checked = wanted; });
      }

      submitSoon(form);
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Enter") return;
      const target = event.target as HTMLElement | null;
      const box = target?.closest?.<HTMLInputElement>(
        "input[type=search], input[type=text]");
      if (!box) return;
      const form = box.form;
      if (!form || !form.classList.contains("jb-filter-form")) return;
      const chips = [...form.querySelectorAll<HTMLInputElement>(
        'input[type=hidden][name="q"]')].map((i) => i.value);
      const action = enterAction(box.name, box.value, chips, event.isComposing);
      if (action === "ignore") {
        // Never swallow an IME's Enter: it is confirming a character.
        if (!event.isComposing) event.preventDefault();
        return;
      }
      event.preventDefault();
      if (action === "clear") { box.value = ""; return; }
      submitNow(form);
    };

    /**
     * **Everything selected is no filter, and it has to serialise that way.**
     * All 199 boxes ticked is the default, and submitting them produced a URL
     * of about 2,400 characters listing every country on earth -- which also
     * quietly excluded the 1,467 postings whose country is unknown, because
     * `country = any(...)` is false for NULL. Disabling the boxes leaves them
     * out of the submission, which is exactly the storage contract the board
     * already holds: no `loc` means worldwide.
     */
    const onSubmit = (event: Event) => {
      const form = event.target as HTMLFormElement | null;
      if (!form || !form.classList.contains("jb-filter-form")) return;
      const boxes = [...form.querySelectorAll<HTMLInputElement>('input[name="loc"]')];
      if (boxes.length && boxes.every((b) => b.checked)) {
        boxes.forEach((b) => { b.disabled = true; });
      }
    };

    document.addEventListener("change", onChange);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("submit", onSubmit, true);
    return () => {
      if (pending) clearTimeout(pending);
      document.removeEventListener("change", onChange);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("submit", onSubmit, true);
    };
  }, []);
  return null;
}
