"use client";

import { useEffect } from "react";

/**
 * A filter control applies when you click it, not when you find the button.
 *
 * **The chips were radios in a GET form that only acted on submit.** Picking
 * "7 days" left the count reading "50 of 18,300" until the reader noticed the
 * Filter button, which is item 17: the old board applies instantly and the
 * count moves with it. The form stays a plain GET form -- linkable, reloadable,
 * no client state -- and this submits it on a change.
 *
 * Delegated to `document` for the reason `ThemeControls` documents: the app bar
 * and the filter row are rendered by the page, so a lane switch replaces these
 * nodes while an effect mounted higher up still holds the old ones. That has
 * now caused the same class of bug three times, so nothing here holds a node.
 *
 * The keyword box is deliberately excluded. It submits on Enter, which is what
 * a form already does, and submitting on every keystroke would reload the lane
 * per character.
 */
export function SubmitOnChange() {
  useEffect(() => {
    const onChange = (event: Event) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      const control = target.closest<HTMLInputElement | HTMLSelectElement>(
        ".jb-basic input[type=radio], .jb-basic input[type=checkbox], "
        + ".jb-basic select");
      if (!control) return;
      control.form?.requestSubmit();
    };
    document.addEventListener("change", onChange);
    return () => document.removeEventListener("change", onChange);
  }, []);
  return null;
}
