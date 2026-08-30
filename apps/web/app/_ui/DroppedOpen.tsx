"use client";

import { useEffect } from "react";

/**
 * The dock opens the dialog and the dialog closes.
 *
 * A `<dialog>` closes on its own form buttons and on Escape, but nothing opens
 * it without a call to showModal, and the board's own opener lives in
 * dashboard.py's script rather than in premium_ui.SCRIPT. This is the smallest
 * thing that makes the control work; it draws nothing.
 */
export function DroppedOpen() {
  useEffect(() => {
    const dock = document.querySelector<HTMLButtonElement>(".dropped-dock");
    const dialog = document.querySelector<HTMLDialogElement>(".dropped-dialog");
    if (!dock || !dialog) return;
    const open = () => dialog.showModal();
    const close = () => dialog.close();
    dock.addEventListener("click", open);
    const closer = dialog.querySelector(".dialog-close");
    closer?.addEventListener("click", close);
    return () => {
      dock.removeEventListener("click", open);
      closer?.removeEventListener("click", close);
    };
  }, []);
  return null;
}
