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
 * The keyword box is still excluded. It submits on Enter, which a form already
 * does, and submitting per keystroke would reload the lane per character.
 */
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

    const onChange = (event: Event) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      const control = target.closest<HTMLInputElement | HTMLSelectElement>(
        "input[type=radio], input[type=checkbox], select");
      if (!control) return;
      const form = control.form ?? control.closest("form");
      if (!form || !form.classList.contains("jb-filter-form")) return;

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
    document.addEventListener("submit", onSubmit, true);
    return () => {
      if (pending) clearTimeout(pending);
      document.removeEventListener("change", onChange);
      document.removeEventListener("submit", onSubmit, true);
    };
  }, []);
  return null;
}
